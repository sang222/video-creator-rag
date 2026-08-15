from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ensure_model_registration() -> None:
    path = ROOT / "app/db/models/__init__.py"
    if not path.exists():
        return
    line = "from . import remaining_debt as remaining_debt  # noqa: F401\n"
    text = path.read_text(encoding="utf-8")
    if line not in text:
        path.write_text(text.rstrip() + "\n" + line, encoding="utf-8")


def harden_service() -> None:
    path = ROOT / "app/services/remaining_debt_closeout.py"
    text = path.read_text(encoding="utf-8")
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
    old_extension = '''        self.session.add(new_arc)
        self.session.flush()
        for old in self._blueprints(arc.id):
'''
    new_extension = '''        arc.state = "SUPERSEDED"
        self.session.add(new_arc)
        self.session.flush()
        for old in self._blueprints(arc.id):
'''
    if old_extension in text and new_extension not in text:
        text = text.replace(old_extension, new_extension, 1)
    path.write_text(text, encoding="utf-8")


def wire_publication_projection() -> None:
    path = ROOT / "app/services/production_publish.py"
    text = path.read_text(encoding="utf-8")
    marker = "RemainingDebtCloseoutCoordinator(self.session).on_publication_verified"
    if marker in text:
        return
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
    path.write_text(text[:position] + insertion + text[position:], encoding="utf-8")


def remove_channel_runtime_literals() -> None:
    protected = ROOT / "app/services/remaining_debt_closeout.py"
    for base in (ROOT / "app", ROOT / "config"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if (
                not path.is_file()
                or path.suffix not in {".py", ".yaml", ".yml", ".json"}
                or path == protected
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            updated = text.replace("Small Team AI", "Channel Profile").replace(
                "@SmallTeamAI", "@ChannelHandle"
            )
            if updated != text:
                path.write_text(updated, encoding="utf-8")


def update_workflow_heads() -> None:
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        if path.name.startswith(("apply-", "fix-", "complete-", "debt-closeout-source")):
            continue
        text = path.read_text(encoding="utf-8")
        text = re.sub(
            r'test "\$\(alembic heads \| awk \'\{print \$1\}\'\)" = "008[0-6]_[A-Za-z0-9_]+"',
            'test "$(alembic heads | awk \'{print $1}\')" = "0087_business_os"',
            text,
        )
        text = text.replace("0084_youtube_private_delivery", "0087_business_os")
        path.write_text(text, encoding="utf-8")


def clean_temporary_surfaces() -> None:
    for relative in (
        ".github/workflows/debt-closeout-source.yml",
        ".github/workflows/apply-remaining-debt-closeout.yml",
        ".github/workflows/fix-remaining-debt-closeout.yml",
        ".github/workflows/complete-remaining-debt-closeout.yml",
        "tools/finalize_remaining_debt_closeout.py",
        "tools/harden_remaining_debt_closeout.py",
        "tools/complete_remaining_debt_closeout.py",
    ):
        (ROOT / relative).unlink(missing_ok=True)


ensure_model_registration()
harden_service()
wire_publication_projection()
remove_channel_runtime_literals()
update_workflow_heads()

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
clean_temporary_surfaces()
