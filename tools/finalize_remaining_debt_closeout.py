from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch_once(path: Path, old: str, new: str, *, required: bool = True) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        if required:
            raise RuntimeError(f"expected one patch anchor in {path}, found {count}")
        return
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Register the new model surface for metadata discovery without importing every
# class into the package namespace.
models_init = ROOT / "app/db/models/__init__.py"
if models_init.exists():
    text = models_init.read_text(encoding="utf-8")
    line = "from . import remaining_debt as remaining_debt  # noqa: F401\n"
    if line not in text:
        models_init.write_text(text.rstrip() + "\n" + line, encoding="utf-8")

# Ensure the old current arc leaves the partial-unique ACTIVE slot before the
# new extension version is flushed.
service = ROOT / "app/services/remaining_debt_closeout.py"
patch_once(
    service,
    "        self.session.add(new_arc)\n        self.session.flush()\n        for old in self._blueprints(arc.id):",
    "        arc.state = \"SUPERSEDED\"\n        self.session.add(new_arc)\n        self.session.flush()\n        for old in self._blueprints(arc.id):",
)

# Project verified publication into the new series/learning/audience authority.
# The coordinator is deterministic and emits no provider effects.
publish = ROOT / "app/services/production_publish.py"
text = publish.read_text(encoding="utf-8")
if "RemainingDebtCloseoutCoordinator(self.session).on_publication_verified" not in text:
    start = text.find("    def verify_confirmation(")
    anchor = "        uploaded_id = uuid.uuid4()\n"
    position = text.find(anchor, start)
    if start < 0 or position < 0:
        raise RuntimeError("production publication coordinator anchor not found")
    insertion = (
        "        from app.services.remaining_debt_closeout import (\n"
        "            RemainingDebtCloseoutCoordinator,\n"
        "        )\n\n"
        "        RemainingDebtCloseoutCoordinator(\n"
        "            self.session\n"
        "        ).on_publication_verified(\n"
        "            candidate=candidate,\n"
        "            public_receipt=public_receipt,\n"
        "            observed_at=data.observed_published_at,\n"
        "        )\n\n"
    )
    text = text[:position] + insertion + text[position:]
    publish.write_text(text, encoding="utf-8")

# Remove channel-specific runtime identity from production code/config.  Tests,
# docs and historical migrations remain historical evidence and are not edited.
for base in (ROOT / "app", ROOT / "config"):
    if not base.exists():
        continue
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".yaml", ".yml", ".json"}:
            continue
        if path == service:
            continue
        value = path.read_text(encoding="utf-8", errors="ignore")
        replaced = value.replace("Small Team AI", "Channel Profile").replace(
            "@SmallTeamAI", "@ChannelHandle"
        )
        if replaced != value:
            path.write_text(replaced, encoding="utf-8")

# Every existing CI workflow must follow the single Alembic head instead of
# freezing an older revision string.
for path in (ROOT / ".github/workflows").glob("*.yml"):
    if path.name in {
        "apply-remaining-debt-closeout.yml",
        "debt-closeout-source.yml",
    }:
        continue
    value = path.read_text(encoding="utf-8")
    value = re.sub(
        r"008[0-6]_[A-Za-z0-9_]+",
        lambda match: (
            "0087_business_os"
            if "alembic heads" in value
            or "ALEMBIC_HEAD" in value
            or "DB_REVISION" in value
            else match.group(0)
        ),
        value,
    )
    value = value.replace("0084_youtube_private_delivery", "0087_business_os")
    path.write_text(value, encoding="utf-8")

# Format only touched Python surfaces.  The workflow installs the repository's
# development tools before invoking this one-shot finalizer.
subprocess.run(
    [
        "ruff",
        "format",
        "app/db/models/remaining_debt.py",
        "app/services/remaining_debt_closeout.py",
        "app/services/production_publish.py",
        "tests/test_remaining_debt_closeout.py",
        "alembic/versions/0085_series_authority.py",
        "alembic/versions/0086_learning_authority.py",
        "alembic/versions/0087_business_os.py",
    ],
    cwd=ROOT,
    check=True,
)

# Remove one-shot bootstrap surfaces before the generated commit is created.
for relative in (
    ".github/workflows/debt-closeout-source.yml",
    ".github/workflows/apply-remaining-debt-closeout.yml",
    "tools/finalize_remaining_debt_closeout.py",
):
    (ROOT / relative).unlink(missing_ok=True)
