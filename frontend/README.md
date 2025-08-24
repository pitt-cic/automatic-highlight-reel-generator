# Local Streamlit UI for Automatic Highlight Reel

This Streamlit app lets you upload or reference a video, sends it to S3 with optional prompt metadata, then tracks backend processing via CloudWatch Logs and S3 until your merged highlights are ready to preview and download. No infrastructure changes are performed by the app.

## Prerequisites

- Python 3.10+ and pip
- AWS credentials with permissions to:
  - Read/Write the target S3 bucket (PutObject/HeadObject/GetObject)
  - Describe CloudFormation stacks (optional bucket discovery)
  - Read from CloudWatch Logs (Lambda and ECS log groups)
- Existing backend that processes new S3 objects in the input prefix

## Setup

From the `frontend/` folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure

You can configure the app via the in‑app sidebar or an `.env` file.

Option A — In‑app sidebar (recommended):
- Start the app (see Run below) and open the left sidebar.
- Set S3 Bucket, AWS Region, and Stack name (used for CloudWatch log groups; default `HighlightProcessorStack`).
- Click “Save settings” to persist values to `ui/.env`.

Option B — Create `ui/.env` before starting:

```
BUCKET_NAME=your-bucket
AWS_REGION=us-east-1
STACK_NAME=HighlightProcessorStack
# Optional: simulate upload client-side without sending to S3
DRY_RUN=false
``` 

Standard AWS env/credential resolution is honored (e.g., `AWS_PROFILE`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`). If the bucket is not set, the app will attempt to discover it from the specified CloudFormation stack outputs.

## Run

From the `frontend/` folder with the virtualenv activated:

```bash
streamlit run ui/app.py
```

This opens the app in your browser. If running remotely, use the URL printed in the terminal (configure Streamlit to listen on `0.0.0.0` if needed).

## Use the app

1) Settings (sidebar)
- Enter/verify S3 Bucket, AWS Region, and Stack name. Click Save to persist.

2) Upload Video
- Quick upload: drag & drop a smaller video in the browser (good for modest sizes).
- Large upload (recommended for big files):
  - Local file path on this machine: paste an absolute path and check “Use this local path.” The file is staged on disk and uploaded with multipart to S3.
  - Existing S3 object: provide `s3://bucket/key` to server‑side copy into the input prefix (no re‑upload).

3) Process Video
- Optionally adjust the “Custom prompt” (stored as S3 object metadata).
- Click “Start upload & process.” The app shows:
  - Upload/copy progress
  - Processing progress inferred from CloudWatch logs with stages:
    - Lambda trigger received
    - ECS task started
    - Stage 1: Downsampling
    - Stage 2: Inference (with % when available)
    - Stage 3: Clipping & Merging
    - Finished successfully
- When complete, you’ll see the S3 path for the highlights, an inline video preview (when possible), and a download button.
- Use “Reset” anytime to clear state.

Notes/limits:
- The app enforces a target file size limit (default 10 GB) and checks local disk space before staging.
- For multi‑GB files, prefer “Large upload” methods to avoid browser bottlenecks.

## Optional: Streamlit server cap for large browser uploads

If you want to increase the browser upload cap, set Streamlit’s server limit (values are in MB). Create `~/.streamlit/config.toml` or a project‑local `.streamlit/config.toml`:

```toml
[server]
maxUploadSize = 10240  # 10 GB
```

Even with a higher cap, large files are more reliable via local path or S3 URI.
