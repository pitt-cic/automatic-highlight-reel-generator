import os
import io
import time
import tempfile
import traceback
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
from ui.upload import save_uploaded_to_disk, check_free_space, multipart_upload
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

if start and uploaded is not None:
    # Validate settings
    if not bucket:
        st.error("Bucket is required. Set it in Settings.")
        st.stop()
    # size guard
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
