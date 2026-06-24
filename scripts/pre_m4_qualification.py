#!/usr/bin/env python3
"""Pre-M4 qualification runner for VCOS.

Runs repo-canonical migrate/seed/test commands twice against the configured DB.
Use with a disposable PostgreSQL database URL.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTEST = ROOT / ".venv" / "bin" / "pytest"
VCOS = ROOT / ".venv" / "bin" / "vcos"
ALEMBIC = ROOT / ".venv" / "bin" / "alembic"


def run(label: str, command: list[str]) -> dict[str, object]:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    record = {
        "label": label,
        "command": " ".join(command),
        "exit_code": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
    }
    print(json.dumps(record, ensure_ascii=False), flush=True)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    return record


def main() -> int:
    if "VCOS_DATABASE_URL" not in os.environ:
        print("VCOS_DATABASE_URL is required; use a disposable DB.", file=sys.stderr)
        return 2
    commands = [
        ("migrate_fresh_or_existing", [str(ALEMBIC), "upgrade", "head"]),
        ("alembic_current", [str(ALEMBIC), "current"]),
        ("config_seed_a", [str(VCOS), "config", "seed"]),
        ("gate_seed_a", [str(VCOS), "gate", "seed-definitions"]),
        ("config_seed_b", [str(VCOS), "config", "seed"]),
        ("gate_seed_b", [str(VCOS), "gate", "seed-definitions"]),
        ("pytest_pass_a", [str(PYTEST)]),
        ("pytest_pass_b", [str(PYTEST)]),
    ]
    records = [run(label, command) for label, command in commands]
    print(json.dumps({"verdict": "PASS", "records": records}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
