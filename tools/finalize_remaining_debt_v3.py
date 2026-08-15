from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Apply the full semantic hardening when an earlier queued writer has not yet
# done so. The script is idempotent and removes itself only after success.
v2 = ROOT / "tools/final_hardening_v2.py"
if v2.exists():
    subprocess.run([sys.executable, str(v2)], cwd=ROOT, check=True)

service = ROOT / "app/services/remaining_debt_closeout.py"
model = ROOT / "app/db/models/remaining_debt.py"
migration = ROOT / "alembic/versions/0087_business_os.py"
tests = ROOT / "tests/test_remaining_debt_closeout.py"
for required in (service, model, migration, tests):
    if not required.exists():
        raise RuntimeError(f"required closeout surface missing: {required}")

# Semantic-hardening markers must exist before runtime wiring is accepted.
service_text = service.read_text(encoding="utf-8")
required_markers = (
    "pending_revenue=buckets[\"PENDING\"]",
    "MONETIZATION_STATE_STALE_OR_UNTRUSTED",
    "def refresh_action_queue(",
    "def approve_appeal_pack(",
    "def _policy_snapshot_hash(",
    "AFFILIATE_LINK_AUTHORITY_MISSING",
    "Path(__file__).resolve()",
)
missing = [marker for marker in required_markers if marker not in service_text]
if missing:
    raise RuntimeError(f"semantic hardening incomplete: {missing}")

# Register the model module in the canonical metadata import surface.
models_init = ROOT / "app/db/models/__init__.py"
if models_init.exists():
    line = "from . import remaining_debt as remaining_debt  # noqa: F401\n"
    current = models_init.read_text(encoding="utf-8")
    if line not in current:
        models_init.write_text(current.rstrip() + "\n" + line, encoding="utf-8")

# Wire code-only post-public projections after exact public receipt creation.
# This coordinator has no provider effects and records incidents instead of
# blocking canonical publication when live authority has not been bootstrapped.
publish = ROOT / "app/services/production_publish.py"
publish_text = publish.read_text(encoding="utf-8")
marker = "RemainingDebtCloseoutCoordinator(\n            self.session\n        ).on_publication_verified"
if marker not in publish_text:
    method_start = publish_text.find("    def verify_confirmation(")
    anchor = "        uploaded_id = uuid.uuid4()\n"
    position = publish_text.find(anchor, method_start)
    if method_start < 0 or position < 0:
        raise RuntimeError("public verification integration anchor not found")
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
    publish.write_text(
        publish_text[:position] + insertion + publish_text[position:],
        encoding="utf-8",
    )

# Remove remaining channel-specific runtime identity. Historical tests, reports,
# docs and migrations stay untouched as evidence.
protected = service.resolve()
for base in (ROOT / "app", ROOT / "config"):
    if not base.exists():
        continue
    for path in base.rglob("*"):
        if (
            not path.is_file()
            or path.suffix not in {".py", ".yaml", ".yml", ".json"}
            or path.resolve() == protected
        ):
            continue
        current = path.read_text(encoding="utf-8", errors="ignore")
        updated = current.replace("Small Team AI", "Channel Profile").replace(
            "@SmallTeamAI", "@ChannelHandle"
        )
        if updated != current:
            path.write_text(updated, encoding="utf-8")

# Existing CI must assert the new single head, while retaining historical
# migration files in compile/regression surfaces.
for path in (ROOT / ".github/workflows").glob("*.yml"):
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        if "alembic heads" in line or "ALEMBIC_HEAD" in line or "DB_REVISION" in line:
            line = re.sub(r"008[0-6]_[A-Za-z0-9_]+", "0087_business_os", line)
        lines.append(line)
    path.write_text("".join(lines), encoding="utf-8")

# Keep the large existing publish service surgical; format only new code.
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

# Final diff must contain no bootstrap writer or one-shot patch utility.
for relative in (
    ".github/workflows/debt-closeout-source.yml",
    ".github/workflows/apply-remaining-debt-closeout.yml",
    ".github/workflows/fix-remaining-debt-closeout.yml",
    ".github/workflows/complete-remaining-debt-closeout.yml",
    ".github/workflows/final-hardening-v2.yml",
    ".github/workflows/finalize-remaining-debt-v3.yml",
    "tools/finalize_remaining_debt_closeout.py",
    "tools/harden_remaining_debt_closeout.py",
    "tools/complete_remaining_debt_closeout.py",
    "tools/final_hardening_v2.py",
    "tools/finalize_remaining_debt_v3.py",
):
    (ROOT / relative).unlink(missing_ok=True)
