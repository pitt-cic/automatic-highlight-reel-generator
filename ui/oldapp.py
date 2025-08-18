import os
import time
from datetime import datetime, timezone
from pathlib import Path
import json

import boto3
from botocore.exceptions import ClientError, BotoCoreError, NoCredentialsError
import streamlit as st

# --------------------
# Page Setup
# --------------------
st.set_page_config(page_title="Highlight Reel Demo", page_icon="🎬", layout="wide")
st.title("🎬 Automatic Highlight Reel Generator — Demo UI")
st.caption("Upload a practice video → pipeline runs on AWS → watch the merged highlight reel here.")

try:
    from streamlit import config as _config
    CURRENT_MAX_MB = int(_config.get_option("server.maxUploadSize"))
except Exception:
    CURRENT_MAX_MB = 200

# --------------------
# Helpers
# --------------------

def _guess_region() -> str:
    return boto3.session.Session().region_name or os.environ.get("AWS_REGION") or "us-east-1"

def _get_cf_output_value(outputs, keys):
    for o in outputs:
        if o.get("OutputKey") in keys:
            return o.get("OutputValue")
    return None

def autodetect_stack(bucket_state):
    stack_name = st.session_state.get("stack_name", "HighlightProcessorStack")
    region = st.session_state.get("aws_region", _guess_region())
    try:
        cf = boto3.client("cloudformation", region_name=region)
        resp = cf.describe_stacks(StackName=stack_name)
        outputs = resp["Stacks"][0].get("Outputs", [])
        bucket = _get_cf_output_value(outputs, {"BucketName", "VideoUploadsBucket", "UploadsBucket"})
        if bucket:
            st.session_state["bucket_name"] = bucket
            st.session_state["aws_region"] = region
            bucket_state.success(f"Detected bucket: s3://{bucket} (region: {region})")
        else:
            bucket_state.warning("Couldn't find a bucket output on that stack. Enter it manually below.")
    except Exception as e:
        bucket_state.error(f"Auto-detection failed: {e}")

def content_type_for(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
    }.get(ext, "application/octet-stream")

# --------------------
# Sidebar — AWS & Prompt
# --------------------
with st.sidebar:
    st.header("AWS Settings")
    if "aws_region" not in st.session_state:
        st.session_state["aws_region"] = _guess_region()
    if "stack_name" not in st.session_state:
        st.session_state["stack_name"] = "HighlightProcessorStack"

    st.text_input("Region", key="aws_region")
    st.text_input("Stack name", key="stack_name")

    bucket_holder = st.empty()
    col_a, col_b = st.columns([1, 1])
    with col_a:
        st.button("Auto-detect bucket", on_click=autodetect_stack, args=(bucket_holder,))
    with col_b:
        if st.session_state.get("bucket_name"):
            bucket_holder.info(f"Using s3://{st.session_state['bucket_name']}")
        else:
            bucket_holder.info("No bucket detected yet.")

    st.text_input("Bucket name (fallback/manual)", key="bucket_name")

    st.divider()
    st.header("Prompt")
    mode = st.radio("How should the prompt be set?", ["Use pipeline default", "Attach custom prompt"], index=0)
    custom_prompt = None
    if mode == "Attach custom prompt":
        presets = {
            "Basketball jump shot": "<image> is there a player jumping and shooting a basketball?",
            "Swimming dive": "<image> is there a person in the air jumping into the water?",
            "Soccer goal attempt": "<image> is a player taking a shot on goal?",
        }
        preset = st.selectbox("Preset", list(presets.keys()), index=0)
        custom_prompt = st.text_area("Custom prompt (stored as S3 object metadata 'prompt')", presets[preset], height=100)
        st.caption("Your Lambda reads this metadata and passes it downstream to ECS.")

# --------------------
# Main — Direct-to-S3 Only
# --------------------
region = st.session_state.get("aws_region")
bucket = st.session_state.get("bucket_name")

st.subheader("1) Direct upload to S3 (pre‑signed POST)")
st.caption("Uploads directly from your browser to S3. This avoids Streamlit server timeouts and RAM spikes.")

filename_hint = st.text_input("S3 object name (e.g., practice.mp4) — will be stored under videos/<name>", value="practice.mp4")
input_key = f"videos/{filename_hint}"
result_key = f"results/{Path(filename_hint).stem}_highlights.mp4"

uploader_area = st.empty()
ready = False

if not (region and bucket and filename_hint):
    st.info("Fill in Region, Bucket, and S3 object name above.")
else:
    try:
        s3_client = boto3.client("s3", region_name=region)
        conditions = [
            {"bucket": bucket},
            ["starts-with", "$key", "videos/"],
            ["content-length-range", 1, 20 * 1024 * 1024 * 1024],
        ]
        fields = {}
        if custom_prompt:
            fields["x-amz-meta-prompt"] = custom_prompt
            #conditions.append(["starts-with", "$x-amz-meta-prompt", ""])
        presigned = s3_client.generate_presigned_post(
            Bucket=bucket,
            Key=input_key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=3600,
        )
        html = (
            "<div style=\"font-family:system-ui,sans-serif;\">"
            f"<div style='margin-bottom:6px;'>Max per-file (Streamlit display): {CURRENT_MAX_MB}MB. Direct-to-S3 bypasses it.</div>"
            "<input id=\"file\" type=\"file\" accept=\"video/*\" />"
            "<button id=\"btn\">Upload to S3</button>"
            "<div id=\"status\" style=\"margin-top:8px;\">Idle</div>"
            "<progress id=\"pg\" value=\"0\" max=\"100\" style=\"width:100%; height:16px;\"></progress>"
            "<script>"
            f"const url = {json.dumps(presigned['url'])};"
            f"const fields = {json.dumps(presigned['fields'])};"
            "const btn = document.getElementById('btn');"
            "const status = document.getElementById('status');"
            "const pg = document.getElementById('pg');"
            "btn.onclick = () => {"
            "  const f = document.getElementById('file').files[0];"
            "  if (!f) { alert('Choose a file first.'); return; }"
            "  const fd = new FormData();"
            "  for (const [k,v] of Object.entries(fields)) { fd.append(k, v); }"
            "  fd.append('file', f);"
            "  const xhr = new XMLHttpRequest();"
            "  xhr.upload.addEventListener('progress', (e) => {"
            "    if (e.lengthComputable) {"
            "      const pct = Math.round((e.loaded/e.total)*100);"
            "      pg.value = pct;"
            "      status.innerText = `Uploading… ${pct}%`;"
            "    }"
            "  });"
            "  xhr.onreadystatechange = () => {"
            "    if (xhr.readyState === 4) {"
            "      status.innerText = xhr.status === 204 ? 'Upload complete! Click \"Start\" below to watch processing.' : `Upload failed: ${xhr.status} ${xhr.responseText}`;"
            "    }"
            "  };"
            "  xhr.open('POST', url, true);"
            "  xhr.send(fd);"
            "};"
            "</script>"
            "</div>"
        )
        st.components.v1.html(html, height=220)
        ready = True
    except Exception as e:
        st.error(f"Could not create pre-signed POST: {e}")

proceed = st.checkbox("I've completed the S3 upload and want to start watching for results.", value=False, disabled=not ready)
start_btn = st.button("Start processing / watch logs", type="primary", disabled=not (ready and proceed and bucket))

st.divider()
st.subheader("2) Progress & logs")
status_area = st.empty()
log_expander = st.expander("Lambda logs (live) — optional", expanded=False)
video_area = st.empty()

if start_btn:
    try:
        logs = boto3.client("logs", region_name=region)
        s3 = boto3.client("s3", region_name=region)
        start_ms = int(time.time() * 1000)
        log_group = f"/aws/lambda/{st.session_state['stack_name']}-VideoTriggerLambda"
        last_lines = 0
        waiting = st.status("Processing on AWS… (this may take a while)", expanded=True)
        deadline = time.time() + 3600
        poll = 6
        while True:
            if time.time() > deadline:
                raise TimeoutError("Processing timed out (demo limit)")
            try:
                resp = logs.filter_log_events(logGroupName=log_group, startTime=start_ms, interleaved=True, limit=200)
                lines = []
                for e in resp.get("events", []):
                    ts = datetime.fromtimestamp(e["timestamp"]/1000, tz=timezone.utc).strftime("%H:%M:%S")
                    lines.append(f"[{ts}] {e.get('message','').rstrip()}")
                if lines:
                    with log_expander:
                        for ln in lines[last_lines:]:
                            st.text(ln)
                        last_lines = len(lines)
            except Exception:
                pass
            try:
                s3.head_object(Bucket=bucket, Key=result_key)
                waiting.update(label="Processing complete! Downloading result…", state="complete")
                break
            except ClientError:
                time.sleep(poll)
                poll = min(poll + 2, 15)
        obj = s3.get_object(Bucket=bucket, Key=result_key)
        out_bytes = obj["Body"].read()
        st.subheader("3) Highlight reel")
        video_area.video(out_bytes)
        st.download_button("Download highlight reel", out_bytes, file_name=f"{Path(filename_hint).stem}_highlights.mp4", mime="video/mp4")
        with st.expander("S3 object info"):
            st.write({"input": f"s3://{bucket}/{input_key}", "output": f"s3://{bucket}/{result_key}", "region": region})
    except (NoCredentialsError, BotoCoreError, ClientError) as e:
        status_area.error(f"AWS error: {e}")
    except TimeoutError as e:
        status_area.error(str(e))
    except Exception as e:
        status_area.error(f"Unexpected error: {e}")

st.markdown("---")
st.caption("Tip: For long practice videos, prefer the Direct-to-S3 path above to avoid Streamlit timeouts.")
