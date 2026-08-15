from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
service = ROOT / "app/services/remaining_debt_closeout.py"
text = service.read_text(encoding="utf-8")
old = '''            text = path.read_text(encoding="utf-8", errors="ignore")
            relative = str(path.relative_to(root))
            if any(marker in text for marker in channel_markers):
'''
new = '''            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            relative = str(path.relative_to(root))
            if any(marker in text for marker in channel_markers):
'''
if new not in text:
    if text.count(old) != 1:
        raise RuntimeError("architecture audit self-exclusion anchor not found")
    text = text.replace(old, new, 1)
service.write_text(text, encoding="utf-8")

# Make current-arc supersession ordering idempotently safe even when the first
# one-shot finalizer did not reach its commit step.
text = service.read_text(encoding="utf-8")
old = '''        self.session.add(new_arc)
        self.session.flush()
        for old in self._blueprints(arc.id):
'''
new = '''        arc.state = "SUPERSEDED"
        self.session.add(new_arc)
        self.session.flush()
        for old in self._blueprints(arc.id):
'''
if old in text and new not in text:
    text = text.replace(old, new, 1)
service.write_text(text, encoding="utf-8")

subprocess.run(
    [
        "ruff",
        "format",
        "app/db/models/remaining_debt.py",
        "app/services/remaining_debt_closeout.py",
        "tests/test_remaining_debt_closeout.py",
        "alembic/versions/0085_series_authority.py",
        "alembic/versions/0086_learning_authority.py",
        "alembic/versions/0087_business_os.py",
    ],
    cwd=ROOT,
    check=True,
)

for relative in (
    ".github/workflows/debt-closeout-source.yml",
    ".github/workflows/apply-remaining-debt-closeout.yml",
    ".github/workflows/fix-remaining-debt-closeout.yml",
    "tools/finalize_remaining_debt_closeout.py",
    "tools/harden_remaining_debt_closeout.py",
):
    (ROOT / relative).unlink(missing_ok=True)
