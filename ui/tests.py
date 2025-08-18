"""Minimal smoke tests for uploader and polling helpers.
Run with `python -m ui.tests`.
"""
from __future__ import annotations

import os
import tempfile

from .upload import save_uploaded_to_disk, multipart_upload
from .polling import result_key_for_input


class _DummyUpload:
    def __init__(self, data: bytes, name: str):
        self._b = data
        self.name = name
        self.size = len(data)
        self._pos = 0

    def read(self, n: int):
        if self._pos >= len(self._b):
            return b""
        chunk = self._b[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def seek(self, pos: int):
        self._pos = pos


def test_save_and_dry_run():
    tmpdir = tempfile.mkdtemp(prefix="ui_test_")
    dst = os.path.join(tmpdir, "test.bin")
    data = b"hello" * 1024
    up = _DummyUpload(data, "test.bin")
    n = save_uploaded_to_disk(up, dst)
    assert n == len(data)
    # dry run upload must return expected key
    class _S3:
        pass
    key = multipart_upload(_S3(), bucket="ignored", src_path=dst, prompt=None, on_progress=None, dry_run=True)
    assert key.endswith("test.bin")


def test_result_key_mapping():
    k = result_key_for_input("videos/clip.mp4")
    assert k.startswith("results/") and k.endswith("_highlights.mp4")


if __name__ == "__main__":
    test_save_and_dry_run()
    test_result_key_mapping()
    print("OK")
