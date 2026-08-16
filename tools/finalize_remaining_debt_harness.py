from __future__ import annotations

import runpy
import subprocess
import tempfile
from pathlib import Path


_ORIGINAL_HARNESS_BLOB = "d9eb2405c70c054d04faa950a960661c386dc1cb"
_ORIGINAL_ROUND2_BLOB = "2103f07ed075af06c0c13fe086526b067d0c8efe"


def _blob_text(blob_sha: str) -> str:
    return subprocess.check_output(
        ["git", "cat-file", "blob", blob_sha],
        text=True,
    )


def _run_python_blob(blob_sha: str) -> None:
    source = _blob_text(blob_sha)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        prefix="vcos-closeout-",
        delete=False,
        encoding="utf-8",
    )
    try:
        with handle:
            handle.write(source)
        runpy.run_path(handle.name, run_name="__main__")
    finally:
        Path(handle.name).unlink(missing_ok=True)


def main() -> None:
    # finalize_remaining_debt.py intentionally removes old recovery artifacts.
    # Execute the exact audited harness blob first, then recreate the exact
    # audited Round-2 source for the G1 wrapper that follows in the workflow.
    _run_python_blob(_ORIGINAL_HARNESS_BLOB)
    Path("tools/closeout-hardening.recovery.err").write_text(
        _blob_text(_ORIGINAL_ROUND2_BLOB),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
