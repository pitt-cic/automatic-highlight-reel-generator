# Local Streamlit UI for Automatic Highlight Reel

This UI uploads a video to S3, triggers backend processing (via existing infra), polls for the merged highlights, and previews/downloads the result. No infra changes.

## Quickstart

1) Create and activate a venv (optional) and install deps:

```
python -m venv .venv
source .venv/bin/activate
pip install -r ui/requirements.txt
```

2) Ensure AWS credentials are available locally (env vars or `~/.aws`), and that you have access to the deployed stack outputs.

3) Run Streamlit:

```
streamlit run ui/app.py
```

4) In the sidebar Settings, confirm the S3 bucket and region. The app will try to discover the bucket from the `HighlightProcessorStack` outputs; otherwise, enter them and click Save.

5) Choose a video (≤ 2 GB), optionally edit the prompt, and click "Start upload & process". You'll see per-part upload progress, then a processing state until the result appears.

## Streamlit config for large uploads

In `~/.streamlit/config.toml`:

```
[server]
maxUploadSize = 2048  # ~2 GB
```

## Dry-run mode

Toggle "Dry run" in the sidebar to simulate multipart upload and polling without writing to S3. Useful for smoke tests and demos.

## Known limits

- Resume of in-progress multipart uploads is best-effort (state is saved, but automatic resume is not implemented; UI provides retry).
- Preview loads the entire result into memory for playback; for very large outputs, fall back to the S3 path and download button.
- CloudWatch log peek is best-effort; use the console link for full logs.

## Troubleshooting

- Permission denied / 403: ensure your AWS credentials have s3:PutObject to the input prefix and s3:GetObject to the results prefix.
- Timeouts: the app waits up to 45 minutes. If it times out, check CloudWatch logs and your ECS service.
- Bucket discovery: if not auto-detected, enter the bucket and region in Settings and Save.
