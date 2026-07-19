from __future__ import annotations

from pathlib import Path

from app.services.long_production import run_lpro1_fixture_rehearsal


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    receipt = run_lpro1_fixture_rehearsal(root / "artifacts" / "lpro1")
    print(receipt.model_dump_json(indent=2))
