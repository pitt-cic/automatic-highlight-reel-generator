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
from ui.aws_client import get_s3_client, discover_bucket_from_stack, object_exists
from ui.upload import save_uploaded_to_disk, check_free_space, multipart_upload, copy_s3_object_to_input
from ui.polling import poll_for_result, result_key_for_input
from ui.logs import latest_log_line, get_pipeline_status, PipelineStatus


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
    stack_name = s.stack_name
    # Dry run is no longer configurable from the UI; honor env/.env default silently
    dry_run = s.dry_run

    if not bucket:
        st.caption("Bucket not found in env; attempting CloudFormation discovery…")
        discovered = discover_bucket_from_stack(region, stack_name)
        if discovered:
            bucket = discovered
    bucket = st.text_input("S3 Bucket", value=bucket or "")
    region = st.text_input("AWS Region", value=region or "")
    stack_name = st.text_input("Stack name (for CloudWatch log groups)", value=stack_name or "HighlightProcessorStack")
    if st.button("Save settings"):
        persist_settings(bucket.strip() or None, region.strip() or None, stack_name.strip() or None)
        st.success("Saved. Restart not required.")

st.title("Automatic Highlight Reel – Local UI")

max_size_bytes, _ = limits_bytes()

# -------- Upload Section --------
st.header("Upload Video")

# Initialize variables used later
use_s3_uri = False
s3_uri_input = ""
use_local = False
local_path = ""

col_quick, col_large = st.columns(2)

with col_quick:
    st.subheader("Quick upload")
    st.caption(
        "Best for smaller videos. Drag and drop to upload via your browser. For multi‑GB files, use 'Large upload'."
    )
    uploaded = st.file_uploader(
        "Drag and drop or browse a video",
        type=["mp4", "mov", "mkv", "avi"],
        accept_multiple_files=False,
    )

with col_large:
    st.subheader("Large upload (recommended for big files)")
    st.caption("Avoid browser bottlenecks by using a local path or an existing S3 object.")
    large_method = st.selectbox(
        "Choose a large upload method",
        ["Local file path on this machine", "Existing S3 object (s3://bucket/key)"],
        index=0,
    )

    if large_method == "Local file path on this machine":
        local_path = st.text_input("Local video path (absolute path preferred)", value="")
        use_local = st.checkbox("Use this local path", value=False)
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
    else:
        s3_uri_input = st.text_input("S3 URI (s3://bucket/path/file.mp4)", value="")
        use_s3_uri = st.checkbox("Use this S3 object", value=False)

st.divider()

# -------- Process Section --------
st.header("Process Video")

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

# Stacked checklist placeholders for stages
stages_container = st.container()
with stages_container:
    stage_lambda_ph = st.empty()
    stage_ecs_ph = st.empty()
    stage1_ph = st.empty()
    stage2_ph = st.empty()
    stage3_ph = st.empty()
    stage_done_ph = st.empty()

def _render_stages(st_status: PipelineStatus) -> None:
    def render(ph, ok: bool, label: str) -> None:
        icon = "✅" if ok else "⏳"
        ph.markdown(f"{icon} {label}")

    render(stage_lambda_ph, st_status.lambda_triggered, "Lambda trigger received")
    render(stage_ecs_ph, st_status.ecs_task_started, "ECS task started (spin-up)")
    render(stage1_ph, st_status.stage1_done, "Stage 1: Downsampling")
    infix = f" — {st_status.stage2_inference_pct:.0f}%" if st_status.stage2_inference_pct is not None else ""
    render(stage2_ph, st_status.stage2_done or (st_status.stage2_inference_pct is not None), f"Stage 2: Inference{infix}")
    render(stage3_ph, st_status.stage3_done or st_status.stage3_started, "Stage 3: Clipping & Merging")
    render(stage_done_ph, st_status.finished_success, "Finished successfully")

def _normalize_status(st_status: PipelineStatus) -> PipelineStatus:
    """Ensure stage monotonicity: if a later stage is reached, mark prior ones as done.
    This prevents UI inconsistencies like Stage 2 done while Stage 1 appears pending.
    """
    # Finished implies all done
    if st_status.finished_success:
        st_status.stage3_done = True
        st_status.stage3_started = True
        st_status.stage2_done = True
        st_status.stage2_inference_pct = 100.0
        st_status.stage1_done = True
        st_status.ecs_task_started = True
        st_status.lambda_triggered = True
        return st_status

    # If Stage 3 started/done, imply Stage 2 and Stage 1
    if st_status.stage3_done or st_status.stage3_started:
        st_status.stage2_done = True
        st_status.stage2_inference_pct = 100.0
        st_status.stage1_done = True
        st_status.ecs_task_started = True
        st_status.lambda_triggered = True

    # If Stage 2 has progressed/done, imply Stage 1 and earlier
    if st_status.stage2_done or (st_status.stage2_inference_pct is not None):
        st_status.stage1_done = True
        st_status.ecs_task_started = True
        st_status.lambda_triggered = True

    # If Stage 1 done, imply ECS started and Lambda triggered
    if st_status.stage1_done:
        st_status.ecs_task_started = True
        st_status.lambda_triggered = True

    # If ECS started, imply Lambda triggered
    if st_status.ecs_task_started:
        st_status.lambda_triggered = True

    return st_status

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
        # Polling phase with pipeline progress
        status.info("Processing in backend… This can take several minutes.")
        log_box = st.empty()
        stack = stack_name or os.getenv("STACK_NAME", "HighlightProcessorStack")
        processing_start_time = time.time()

        # Interleave S3 existence checks with CloudWatch status peeks (non-blocking)
        result_key = result_key_for_input(dest_key)
        found = False
        start_wait = time.time()
        while True:
            st_status: PipelineStatus = get_pipeline_status(
                region or None, stack, dest_key, start_time=processing_start_time
            )
            st_status = _normalize_status(st_status)
            pct = st_status.overall_pct() / 100.0
            _render_stages(st_status)
            progress.progress(pct, text=f"Processing… {int(pct*100)}%")

            # Show last log line for context
            log_line = latest_log_line(
                region or None,
                [
                    f"/aws/lambda/{stack}-VideoTriggerLambda",
                    f"/ecs/video-processor-{stack}",
                ],
            )
            if log_line:
                log_box.caption(f"Last log: {log_line.strip()}")

            # Non-blocking check if output appeared in S3
            try:
                found = object_exists(s3, bucket, result_key)
            except Exception:
                found = False

            if found or st_status.finished_success:
                break
            # Gentle wait before the next peek
            time.sleep(5)

        # If finished_success but S3 object not yet visible, wait briefly (grace period)
        if (not found) and st_status.finished_success:
            t0 = time.time()
            while time.time() - t0 < 60:
                try:
                    if object_exists(s3, bucket, result_key):
                        found = True
                        break
                except Exception:
                    pass
                time.sleep(2)

        if found:
            status.success("Highlights ready!")
            s3_path = f"s3://{bucket}/{result_key}"
            st.write(s3_path)
            try:
                obj = s3.get_object(Bucket=bucket, Key=result_key)
                data = obj["Body"].read()
                st.video(data)
                st.download_button(
                    "Download highlights",
                    data=data,
                    file_name=os.path.basename(result_key),
                )
            except Exception:
                st.info("Preview not available. Use the S3 path above.")
        else:
            status.error(
                "Timed out or processing did not complete. Verify permissions, bucket name, and check CloudWatch logs."
            )
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
        if "local_path" in locals() and use_local and local_path:
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
            progress.progress(
                pct, text=f"Uploading {ps.filename}: {ps.pct:.1f}% • {eta}"
            )
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

    # Polling phase with pipeline progress
    status.info("Processing in backend… This can take several minutes.")
    log_box = st.empty()
    stack = stack_name or os.getenv("STACK_NAME", "HighlightProcessorStack")
    processing_start_time = time.time()

    result_key = result_key_for_input(key)
    found = False
    while True:
        st_status: PipelineStatus = get_pipeline_status(
            region or None, stack, key, start_time=processing_start_time
        )
        st_status = _normalize_status(st_status)
        pct = st_status.overall_pct() / 100.0
        _render_stages(st_status)
        progress.progress(pct, text=f"Processing… {int(pct*100)}%")

        log_line = latest_log_line(
            region or None,
            [
                f"/aws/lambda/{stack}-VideoTriggerLambda",
                f"/ecs/video-processor-{stack}",
            ],
        )
        if log_line:
            log_box.caption(f"Last log: {log_line.strip()}")

        try:
            found = object_exists(s3, bucket, result_key)
        except Exception:
            found = False

        if found or st_status.finished_success:
            break
        time.sleep(5)

    if (not found) and st_status.finished_success:
        t0 = time.time()
        while time.time() - t0 < 60:
            try:
                if object_exists(s3, bucket, result_key):
                    found = True
                    break
            except Exception:
                pass
            time.sleep(2)

    if found:
        status.success("Highlights ready!")
        s3_path = f"s3://{bucket}/{result_key}"
        st.write(s3_path)
        try:
            obj = s3.get_object(Bucket=bucket, Key=result_key)
            data = obj["Body"].read()
            st.video(data)
            st.download_button(
                "Download highlights",
                data=data,
                file_name=os.path.basename(result_key),
            )
        except Exception:
            st.info("Preview not available. Use the S3 path above.")
    else:
        status.error(
            "Timed out or processing did not complete. Verify permissions, bucket name, and check CloudWatch logs."
        )
