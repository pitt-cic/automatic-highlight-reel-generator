"""
Configuration and defaults for the local Streamlit UI.

No secrets. Values are discovered from environment, boto3 session, or CloudFormation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from dotenv import dotenv_values
import boto3


# Defaults from the prompt
STACK_NAME = os.getenv("STACK_NAME", "HighlightProcessorStack")
INPUT_PREFIX = os.getenv("INPUT_PREFIX", "videos/")
RESULT_PREFIX = os.getenv("RESULT_PREFIX", "results/")
RESULT_NAMING = os.getenv("RESULT_NAMING", "{basename}_highlights.mp4")
CUSTOM_PROMPT_KEY = os.getenv("CUSTOM_PROMPT_KEY", "prompt")
DEFAULT_PROMPT = os.getenv(
    "DEFAULT_PROMPT", "<image> Is there a person in the air jumping into the water? Answer with 'yes' or 'no'.\n"
)
AWS_REGION_SOURCE = "env or boto3 Session().region_name"

TARGET_MAX_SIZE_GB = int(os.getenv("TARGET_MAX_SIZE_GB", "10"))
PROMPT_MAX_CHARS = int(os.getenv("PROMPT_MAX_CHARS", "1000"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "5"))
BACKOFF_FACTOR = float(os.getenv("BACKOFF_FACTOR", "1.5"))
MAX_WAIT_MIN = int(os.getenv("MAX_WAIT_MIN", "45"))
MULTIPART_THRESHOLD_MB = int(os.getenv("MULTIPART_THRESHOLD_MB", "64"))
PART_SIZE_MB = int(os.getenv("PART_SIZE_MB", "64"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "8"))
UPLOAD_LIMIT_MB = int(os.getenv("UPLOAD_LIMIT_MB", "10240"))


@dataclass
class Settings:
    bucket_name: Optional[str]
    region: Optional[str]
    dry_run: bool = False


def _env_path() -> str:
    # Store .env under ui/ only (local-only)
    return os.path.join(os.path.dirname(__file__), ".env")


def load_local_env() -> dict:
    path = _env_path()
    if os.path.exists(path):
        return dotenv_values(path)
    return {}


def get_initial_settings() -> Settings:
    """
    Resolve bucket and region from env, local .env, or boto3 session fallback for region.
    Bucket may be discovered later via CloudFormation if missing.
    """
    local_env = load_local_env()
    bucket_env = os.getenv("BUCKET_NAME") or local_env.get("BUCKET_NAME")
    # Region precedence: env > .env > boto3 session region
    region_env = os.getenv("AWS_REGION") or local_env.get("AWS_REGION")
    if not region_env:
        region_env = boto3.Session().region_name
    dry_run_val = os.getenv("DRY_RUN") or local_env.get("DRY_RUN") or "false"
    dry_run = str(dry_run_val).lower() in {"1", "true", "yes", "y"}
    return Settings(bucket_name=bucket_env, region=region_env, dry_run=dry_run)


def persist_settings(bucket: Optional[str], region: Optional[str]) -> None:
    """Persist BUCKET_NAME and AWS_REGION to ui/.env for later runs."""
    path = _env_path()
    lines = []
    if bucket:
        lines.append(f"BUCKET_NAME={bucket}\n")
    if region:
        lines.append(f"AWS_REGION={region}\n")
    # Preserve DRY_RUN if present
    current = load_local_env()
    if "DRY_RUN" in current:
        lines.append(f"DRY_RUN={current['DRY_RUN']}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def limits_bytes() -> Tuple[int, int]:
    """Return (max_upload_bytes, multipart_threshold_bytes)."""
    up_b = UPLOAD_LIMIT_MB * 1024 * 1024
    thr_b = MULTIPART_THRESHOLD_MB * 1024 * 1024
    return up_b, thr_b


def multipart_params_bytes() -> Tuple[int, int, int]:
    part_size = PART_SIZE_MB * 1024 * 1024
    threshold = MULTIPART_THRESHOLD_MB * 1024 * 1024
    return part_size, threshold, MAX_CONCURRENCY
