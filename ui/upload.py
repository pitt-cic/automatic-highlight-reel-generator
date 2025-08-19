"""Multipart uploader with on-disk buffering and progress callbacks.
Includes a dry-run mode for tests.
"""
from __future__ import annotations

import io
import os
import time
import math
import json
import shutil
import hashlib
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from ui.config import (
    INPUT_PREFIX,
    CUSTOM_PROMPT_KEY,
    multipart_params_bytes,
)


@dataclass
class ProgressState:
    filename: str
    filesize: int
    bytes_transferred: int = 0
    part_size: int = 5 * 1024 * 1024
    start_time: float = time.time()
    def update(self, n: int) -> None:
        # boto3 Transfer callbacks receive incremental byte counts,
        # so accumulate for correct overall progress. Dry-run simulates similarly.
        self.bytes_transferred += n

    @property
    def pct(self) -> float:
        if self.filesize <= 0:
            return 0.0
        return min(100.0, 100.0 * self.bytes_transferred / self.filesize)

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    @property
    def eta(self) -> Optional[float]:
        if self.bytes_transferred == 0:
            return None
        rate = self.bytes_transferred / max(1e-9, self.elapsed)
        remaining = max(0, self.filesize - self.bytes_transferred)
        return remaining / max(1e-9, rate)


def _ensure_cache_dir() -> str:
   cache_dir = os.path.join(os.path.dirname(__file__), ".cache")
   os.makedirs(cache_dir, exist_ok=True)
   return cache_dir


def _cache_state_path(file_digest: str) -> str:
   return os.path.join(_ensure_cache_dir(), f"upload_{file_digest}.json")


def _file_sha1(path: str) -> str:
   h = hashlib.sha1()
   with open(path, "rb") as f:
       for chunk in iter(lambda: f.read(1024 * 1024), b""):
           h.update(chunk)
   return h.hexdigest()


def _save_state(file_digest: str, state: dict) -> None:
   with open(_cache_state_path(file_digest), "w", encoding="utf-8") as f:
       json.dump(state, f)


def _clear_state(file_digest: str) -> None:
   p = _cache_state_path(file_digest)
   if os.path.exists(p):
       os.remove(p)


def save_uploaded_to_disk(uploaded_file, dst_path: str) -> int:
   """Write an UploadedFile to disk in buffered chunks. Returns size in bytes."""
   os.makedirs(os.path.dirname(dst_path), exist_ok=True)
   size = 0
   with open(dst_path, "wb") as out:
       for chunk in iter(lambda: uploaded_file.read(1024 * 1024), b""):
           out.write(chunk)
           size += len(chunk)
   return size


def check_free_space(path: str, required_bytes: int) -> bool:
   total, used, free = shutil.disk_usage(os.path.dirname(path) or ".")
   return free >= required_bytes


def _make_transfer_config(part_size: int, threshold: int, max_conc: int) -> TransferConfig:
   return TransferConfig(
       multipart_threshold=threshold,
       multipart_chunksize=part_size,
       max_concurrency=max_conc,
       use_threads=True,
   )


def s3_key_for_upload(filename: str) -> str:
   base = os.path.basename(filename)
   return f"{INPUT_PREFIX}{base}"


def copy_s3_object_to_input(
   s3,
   dest_bucket: str,
   source_bucket: str,
   source_key: str,
   prompt: Optional[str],
) -> str:
   """Server-side copy of an existing S3 object into INPUT_PREFIX, attaching prompt metadata.
   Returns the destination key.
   """
   base = os.path.basename(source_key)
   dest_key = f"{INPUT_PREFIX}{base}"
   extra = {"MetadataDirective": "REPLACE", "Metadata": {}}
   if prompt:
       extra["Metadata"][CUSTOM_PROMPT_KEY] = prompt
   s3.copy(
       {"Bucket": source_bucket, "Key": source_key},
       dest_bucket,
       dest_key,
       ExtraArgs=extra,
   )
   return dest_key


def multipart_upload(
   s3,
   bucket: str,
   src_path: str,
   prompt: Optional[str],
   on_progress: Optional[Callable[[ProgressState], None]] = None,
   dry_run: bool = False,
) -> str:
   """Upload file with multipart and progress callbacks. Returns S3 key.
   Persists a small resume state best-effort.
   """
   part_size, threshold, max_conc = multipart_params_bytes()
   cfg = _make_transfer_config(part_size, threshold, max_conc)

   filesize = os.path.getsize(src_path)
   key = s3_key_for_upload(src_path)
   file_digest = _file_sha1(src_path)

   if dry_run:
       # Simulate per-part progress without S3 writes
       state = ProgressState(filename=os.path.basename(src_path), filesize=filesize, part_size=part_size)
       total_parts = math.ceil(filesize / part_size) if filesize else 1
       for i in range(total_parts):
           # simulate part transfer
           to_add = part_size if (i < total_parts - 1) else (filesize - part_size * (total_parts - 1))
           time.sleep(0.05)
           state.update(to_add)
           if on_progress:
               on_progress(state)
       _clear_state(file_digest)
       return key

   extra_args = {"Metadata": {}}
   if prompt:
       extra_args["Metadata"][CUSTOM_PROMPT_KEY] = prompt

   state = ProgressState(filename=os.path.basename(src_path), filesize=filesize, part_size=part_size)

   def _cb(bytes_amount):
       state.update(bytes_amount)
       if on_progress:
           on_progress(state)

   try:
       with open(src_path, "rb") as f:
           s3.upload_fileobj(
               f,
               bucket,
               key,
               ExtraArgs=extra_args,
               Callback=_cb,
               Config=cfg,
           )
       _clear_state(file_digest)
       return key
   except ClientError:
       # Best-effort state so the UI can say "retry from start"
       _save_state(file_digest, {"uploaded_bytes": state.bytes_transferred, "filesize": filesize, "key": key})
       raise
