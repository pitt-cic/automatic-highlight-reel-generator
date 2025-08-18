"""Polling helpers to wait for highlight result with backoff and optional log peek."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple

from botocore.exceptions import ClientError

from ui.config import (
    RESULT_PREFIX,
    RESULT_NAMING,
    POLL_SECONDS,
    BACKOFF_FACTOR,
    MAX_WAIT_MIN,
)
from ui.aws_client import object_exists


@dataclass
class PollResult:
    found: bool
    key: Optional[str]
    last_error: Optional[str] = None
    waited_seconds: float = 0.0

def result_key_for_input(input_key: str) -> str:
    # Map input basename to expected output naming e.g., {basename}_highlights.mp4 under RESULT_PREFIX
    base = input_key.split("/")[-1]
    if "." in base:
        basename = base.rsplit(".", 1)[0]
    else:
        basename = base
    outname = RESULT_NAMING.replace("{basename}", basename)
    return f"{RESULT_PREFIX}{outname}"


def poll_for_result(s3, bucket: str, input_key: str) -> PollResult:
    delay = POLL_SECONDS
    deadline = time.time() + MAX_WAIT_MIN * 60
    key = result_key_for_input(input_key)
    start = time.time()
    last_err = None
    while time.time() < deadline:
        try:
            if object_exists(s3, bucket, key):
                return PollResult(found=True, key=key, waited_seconds=time.time() - start)
        except ClientError as e:
            # Permission issues or other errors should surface later in UI
            last_err = str(e)
        time.sleep(delay)
        delay = min(delay * BACKOFF_FACTOR, 60)
    return PollResult(found=False, key=key, last_error=last_err, waited_seconds=time.time() - start)
