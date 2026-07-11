#!/usr/bin/env python3
"""Minimal ISO-BMFF top-level atom order check; standard library only."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def inspect(path: Path) -> dict:
    atoms: list[dict[str, int | str]] = []
    with path.open("rb") as handle:
        offset = 0
        total = path.stat().st_size
        while offset + 8 <= total:
            handle.seek(offset)
            raw_size = handle.read(4)
            atom_type = handle.read(4).decode("latin-1", errors="replace")
            size = int.from_bytes(raw_size, "big")
            header = 8
            if size == 1:
                size = int.from_bytes(handle.read(8), "big")
                header = 16
            elif size == 0:
                size = total - offset
            if size < header or offset + size > total:
                break
            atoms.append({"type": atom_type, "offset": offset, "size": size})
            offset += size
    positions = {str(atom["type"]): int(atom["offset"]) for atom in atoms}
    return {
        "path": str(path),
        "atoms": atoms,
        "moov_before_mdat": "moov" in positions and "mdat" in positions and positions["moov"] < positions["mdat"],
    }


if __name__ == "__main__":
    result = inspect(Path(sys.argv[1]))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["moov_before_mdat"] else 1)
