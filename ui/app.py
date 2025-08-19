import os
import io
import time
import tempfile
import traceback
import shutil
from threading import Thread
from queue import Queue, Empty
from datetime import timedelta
import sys
from pathlib import Path

# Ensure project root is on sys.path so 'ui.*' imports work when running as a script
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
from botocore.exceptions import ClientError

from ui.config import (
    INPUT_PREFIX,
    RESULT_PREFIX,
    DEFAULT_PROMPT,
    PROMPT_MAX_CHARS,
    TARGET_MAX_SIZE_GB,
    UPLOAD_LIMIT_MB,
)
from ui.config import get_initial_settings, persist_settings, limits_bytes
from ui.aws_client import get_s3_client, discover_bucket_from_stack
from ui.upload import save_uploaded_to_disk, check_free_space, multipart_upload, copy_s3_object_to_input
from ui.polling import poll_for_result
from ui.logs import latest_log_line


st.set_page_config(page_title="Highlight Uploader", layout="centered")


def _human_size(n: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


with st.sidebar:
    st.header("Settings")
    s = get_initial_settings()
    bucket = s.bucket_name
    region = s.region
    dry_run = st.toggle("Dry run (no S3 writes)", value=s.dry_run, help="Simulate multipart upload and polling without S3.")

    if not bucket:
        st.caption("Bucket not found in env; attempting CloudFormation discovery…")
        discovered = discover_bucket_from_stack(region)
        if discovered:
            bucket = discovered
    bucket = st.text_input("S3 Bucket", value=bucket or "")
    region = st.text_input("AWS Region", value=region or "")
    if st.button("Save settings"):
        persist_settings(bucket.strip() or None, region.strip() or None)
        st.success("Saved. Restart not required.")

st.title("Automatic Highlight Reel – Local UI")

max_size_bytes, _ = limits_bytes()

uploaded = st.file_uploader(
    "Select a video", type=["mp4", "mov", "mkv", "avi"], accept_multiple_files=False
)

# Optional alternative: allow providing a local file path to avoid browser upload overhead.
with st.expander("Or provide a local file path (bypass browser upload)"):
    local_path = st.text_input("Local video path on this machine", value="")
    use_local = st.checkbox("Use local path instead of browser upload", value=False)
    if use_local and local_path:
        try:
            lp = Path(local_path).expanduser().resolve()
            if lp.exists() and lp.is_file():
                # Mimic the uploaded object minimally
                class _Local:
                    name = lp.name
                    size = lp.stat().st_size
                    def seek(self, *_):
                        return None
                uploaded = _Local()  # type: ignore
                st.info(f"Selected local file: {lp} ({_human_size(lp.stat().st_size)})")
            else:
                st.warning("Path does not exist or is not a file.")
        except Exception as e:
            st.warning(f"Invalid path: {e}")

# Alternative: already uploaded to S3 via CLI/Console? Provide the URI.
with st.expander("Already in S3? Provide s3://bucket/key to process without browser upload"):
    s3_uri_input = st.text_input("S3 URI of your source video (s3://bucket/path/file.mp4)", value="")
    use_s3_uri = st.checkbox("Use this S3 object instead of uploading", value=False)

prompt = st.text_area(
    "Custom prompt (optional)", value=DEFAULT_PROMPT, max_chars=PROMPT_MAX_CHARS, height=80
)

if uploaded is not None:
    st.info(f"Selected: {uploaded.name} ({_human_size(uploaded.size)})")

col1, col2 = st.columns(2)
start = col1.button("Start upload & process", type="primary", disabled=uploaded is None)
reset = col2.button("Reset")

if reset:
    st.session_state.clear()
    st.rerun()

status = st.empty()
progress = st.progress(0.0, text="Idle")
debug_box = st.empty()

if start and ((uploaded is not None) or (use_s3_uri and s3_uri_input)):
    # Validate settings
    if not bucket:
        st.error("Bucket is required. Set it in Settings.")
        st.stop()
    # If using S3 URI mode, skip local staging/upload and do server-side copy
    if use_s3_uri and s3_uri_input:
        s3 = get_s3_client(region or None)
        # parse s3://bucket/key
        try:
            if not s3_uri_input.startswith("s3://"):
                raise ValueError("Must start with s3://")
            bucket_src_key = s3_uri_input[5:]
            src_bucket, _, src_key = bucket_src_key.partition("/")
            if not src_bucket or not src_key:
                raise ValueError("Provide full s3://bucket/key")
        except Exception as e:
            st.error(f"Invalid S3 URI: {e}")
            st.stop()

        status.info("Copying object in S3 and attaching prompt metadata…")
        try:
            dest_key = copy_s3_object_to_input(
                s3,
                dest_bucket=bucket,
                source_bucket=src_bucket,
                source_key=src_key,
                prompt=prompt.strip() or None,
            )
        except Exception as e:
            status.error("S3 copy failed.")
            with st.expander("Show error details"):
                st.code(str(e), language="text")
            st.stop()

        progress.progress(1.0, text="S3 copy complete. Starting processing…")
        # Polling phase
        status.info("Processing in backend… This can take several minutes.")
        log_box = st.empty()
        pr = poll_for_result(s3, bucket=bucket, input_key=dest_key)
        log_line = latest_log_line(region or None, [
            f"/aws/lambda/-{ 'HighlightProcessorStack' }-VideoTriggerLambda",
            f"/ecs/video-processor-{ 'HighlightProcessorStack' }",
        ])
        if log_line:
            log_box.caption(f"Last log: {log_line.strip()}")
        if pr.found:
            status.success("Highlights ready!")
            s3_path = f"s3://{bucket}/{pr.key}"
            st.write(s3_path)
            try:
                obj = s3.get_object(Bucket=bucket, Key=pr.key)
                data = obj["Body"].read()
                st.video(data)
                st.download_button("Download highlights", data=data, file_name=os.path.basename(pr.key))
            except Exception:
                st.info("Preview not available. Use the S3 path above.")
        else:
            status.error("Timed out waiting for result. Verify permissions, bucket name, and check CloudWatch logs.")
        st.stop()

    # size guard (non-S3-URI flow)
    if uploaded.size > TARGET_MAX_SIZE_GB * 1024 * 1024 * 1024:
        st.error(f"File exceeds {TARGET_MAX_SIZE_GB} GB limit.")
        st.stop()

    # Write to a temp path on disk
    tmpdir = tempfile.mkdtemp(prefix="hl_upload_")
    temp_path = os.path.join(tmpdir, uploaded.name)

    if not check_free_space(temp_path, uploaded.size * 2):  # buffer + temp
        st.error("Insufficient disk space for staging upload.")
        st.stop()

    status.info("Saving file to disk…")
    # If using local path mode, detect and hardlink/copy instead of re-reading into memory
    source_was_local = False
    try:
        # Best-effort: if user provided local path and chose to use it
        if 'local_path' in locals() and use_local and local_path:
            lp = Path(local_path).expanduser().resolve()
            if lp.exists() and lp.is_file():
                source_was_local = True
                try:
                    os.link(str(lp), temp_path)
                except Exception:
                    shutil.copy2(str(lp), temp_path)
                size_written = os.path.getsize(temp_path)
            else:
                uploaded.seek(0)
                size_written = save_uploaded_to_disk(uploaded, temp_path)
        else:
            uploaded.seek(0)
            size_written = save_uploaded_to_disk(uploaded, temp_path)
    except Exception:
        uploaded.seek(0)
        size_written = save_uploaded_to_disk(uploaded, temp_path)

    if size_written != uploaded.size:
        st.warning("Size mismatch after save; proceeding but results may vary.")

    # Begin multipart upload in a worker thread; update UI from the main thread.
    s3 = get_s3_client(region or None)

    q: Queue = Queue()
    result = {"key": None, "error": None}

    def on_progress(ps):
        # Called from s3transfer threads. Do NOT touch Streamlit here.
        # Instead, queue the latest progress state for the main thread to render.
        try:
            q.put(ps, block=False)
        except Exception:
            pass

    def worker():
        try:
            k = multipart_upload(
                s3,
                bucket=bucket,
                src_path=temp_path,
                prompt=prompt.strip() or None,
                on_progress=on_progress,
                dry_run=dry_run,
            )
            result["key"] = k
        except Exception:
            result["error"] = traceback.format_exc()

    status.info("Uploading to S3…")
    t = Thread(target=worker, daemon=True)
    t.start()

    last_update = time.time()
    while t.is_alive():
        try:
            ps = q.get(timeout=0.2)
            pct = ps.pct / 100.0
            eta = f"ETA {timedelta(seconds=int(ps.eta))}" if ps.eta else "Estimating…"
            progress.progress(pct, text=f"Uploading {ps.filename}: {ps.pct:.1f}% • {eta}")
            # light debug heartbeat each ~3s
            if time.time() - last_update > 3:
                debug_box.caption("Uploading… (UI updated from main thread)")
                last_update = time.time()
        except Empty:
            # keep UI responsive
            pass
        except Exception as e:
            debug_box.error(f"Progress update error: {e}")
            break
        # Yield to Streamlit
        time.sleep(0.05)

    t.join(timeout=1)
    if result["error"]:
        status.error("Upload failed.")
        with st.expander("Show error details"):
            st.code(result["error"], language="text")
        st.stop()
    key = result["key"]
    progress.progress(1.0, text="Upload complete.")

    # Polling phase
    status.info("Processing in backend… This can take several minutes.")
    log_box = st.empty()

    pr = poll_for_result(s3, bucket=bucket, input_key=key)
    log_line = latest_log_line(region or None, [
        f"/aws/lambda/-{ 'HighlightProcessorStack' }-VideoTriggerLambda",
        f"/ecs/video-processor-{ 'HighlightProcessorStack' }",  # best-effort
    ])
    if log_line:
        log_box.caption(f"Last log: {log_line.strip()}")

    if pr.found:
        status.success("Highlights ready!")
        # show player and download link
        s3_path = f"s3://{bucket}/{pr.key}"
        st.write(s3_path)
        try:
            # Try to stream the file for preview
            obj = s3.get_object(Bucket=bucket, Key=pr.key)
            data = obj["Body"].read()
            st.video(data)
            st.download_button("Download highlights", data=data, file_name=os.path.basename(pr.key))
        except Exception:
            st.info("Preview not available. Use the S3 path above.")
    else:
        status.error(
            "Timed out waiting for result. Verify permissions, bucket name, and check CloudWatch logs."
        )
