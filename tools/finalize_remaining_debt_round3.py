from __future__ import annotations

import re
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path


_ORIGINAL_ROUND3_BLOB = "c518a1d683921fdee4750280ff855707b0196ff6"


def _blob_text(blob_sha: str) -> str:
    return subprocess.check_output(
        ["git", "cat-file", "blob", blob_sha],
        text=True,
    )


def _run_original_round3() -> None:
    source = _blob_text(_ORIGINAL_ROUND3_BLOB)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        prefix="vcos-round3-",
        delete=False,
        encoding="utf-8",
    )
    try:
        with handle:
            handle.write(source)
        runpy.run_path(handle.name, run_name="__main__")
    finally:
        Path(handle.name).unlink(missing_ok=True)


def _replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _repair_round2_niche_digest_helper_boundary() -> None:
    """Undo only the next-function character consumed by the audited Round-2 regex."""

    path = Path("app/services/m5.py")
    text = path.read_text(encoding="utf-8")
    broken = "def _iche_digest_from_context(\n"
    current = "def _niche_digest_from_context(\n"
    broken_count = text.count(broken)
    current_count = text.count(current)
    if broken_count == 1 and current_count == 0:
        path.write_text(text.replace(broken, current, 1), encoding="utf-8")
        return
    if broken_count == 0 and current_count == 1:
        return
    raise SystemExit(
        "Round-2 niche digest helper boundary is ambiguous: "
        f"broken={broken_count}, current={current_count}"
    )


def _patch_current_evidence_authority_split() -> None:
    """Keep claim provenance and quantitative demand as separate authorities."""

    path = Path("tests/qualification/conftest.py")
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'''(?P<demand>        evidence = None\n'''
        r'''        if evidence_volume is not None:\n'''
        r'''            evidence = SearchDemandEvidenceService\(self\.session\)\.create_evidence\(\n'''
        r'''.*?'''
        r'''            \)\n)'''
        r'''(?=        research = EditorialResearchService\(self\.session\))''',
        re.S,
    )
    claim_block = r'''\g<demand>        claim_evidence = None
        if evidence_volume is not None:
            claim_evidence = SearchDemandEvidenceService(self.session).create_evidence(
                data=SearchDemandEvidenceCreate(
                    company_id=scope.company.id,
                    channel_workspace_id=scope.channel.id,
                    evidence_source_type="OFFICIAL_DOCUMENT",
                    authority_purpose="CLAIM_SOURCE",
                    source_ref=(
                        "https://docs.example.test/qualification/"
                        "approval-checkpoint"
                    ),
                    query="documented automation approval checkpoint",
                    platform="GENERIC",
                    geo=primary_market,
                    language=locale,
                    evidence_confidence="MEDIUM",
                )
            )
'''
    text, count = pattern.subn(claim_block, text, count=1)
    if count != 1:
        raise SystemExit(
            f"NV06 split claim evidence insertion expected 1 match, found {count}"
        )
    path.write_text(text, encoding="utf-8")

    _replace_once(
        path,
        '''                evidence_refs=[{"type": "search_demand_evidence", "id": str(evidence.id) if evidence else "missing"}],\n''',
        '''                evidence_refs=[{"type": "search_demand_evidence", "id": str(claim_evidence.id) if claim_evidence else "missing"}],\n''',
        label="NV06 candidate claim authority",
    )

    _replace_once(
        path,
        '''                evidence_blob={"search_demand_evidence_ids": [str(evidence.id)] if evidence is not None else []},\n''',
        '''                claim_evidence_refs=(\n                    [{"id": str(claim_evidence.id)}]\n                    if claim_evidence is not None\n                    else []\n                ),\n                market_demand_evidence_refs=(\n                    [{"id": str(evidence.id)}] if evidence is not None else []\n                ),\n                evidence_blob={},\n''',
        label="NV06 explicit split preflight authority",
    )


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    _repair_round2_niche_digest_helper_boundary()
    _run_original_round3()
    if stage == "rc2":
        _patch_current_evidence_authority_split()


if __name__ == "__main__":
    main()
