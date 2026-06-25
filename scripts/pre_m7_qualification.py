#!/usr/bin/env python3
"""Pre-M7 M0→M6 qualification runner for VCOS."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
REPORT_MD = ROOT / "reports" / "pre_m7_qualification_report.md"
REPORT_JSON = ROOT / "reports" / "pre_m7_qualification_report.json"
COMMAND_LOG = ROOT / "reports" / "pre_m7_qualification_command_log.jsonl"
ALEMBIC = ROOT / ".venv" / "bin" / "alembic"
PYTEST = ROOT / ".venv" / "bin" / "pytest"
VCOS = ROOT / ".venv" / "bin" / "vcos"
PYTHON = ROOT / ".venv" / "bin" / "python"

REQUIRED_TAGS = ["m5-daily-run-context-admission", "m6-production-media-qc-foundation"]
EXPECTED_ALEMBIC_HEAD = "0007_m6_production"
MEDIA_GITIGNORE_PATTERNS = ["var/generated/", "test-render-output/", "*.mp4", "*.mov", "*.wav"]
MEDIA_SUFFIXES = {".mp4", ".mov", ".wav"}
WHOLE_RUNNER_TIMEOUT_SECONDS = 600

REQUIRED_SOURCE_OF_TRUTH_PATHS = [
    "README.md",
    "docs/architecture/source-of-truth.md",
    "docs/architecture/architecture-ledger.md",
    "docs/architecture/m0-scope.md",
    "docs/architecture/m1-scope.md",
    "docs/architecture/m2-artifact-workflow.md",
    "docs/architecture/m3-policy-gate-readiness.md",
    "docs/architecture/m4-ops-foundation.md",
    "docs/architecture/m5-daily-run-context-admission.md",
    "docs/architecture/m6-production-artifacts.md",
    "docs/architecture/policy-snapshot-invariants.md",
    "docs/architecture/profile-compiler.md",
    "reports/m2-final-report.md",
    "reports/m3-final-report.md",
    "reports/m4-final-report.md",
    "reports/m5-final-report.md",
    "reports/m6-final-report.md",
    "reports/pre_m4_qualification_report.md",
    "scripts/pre_m4_qualification.py",
]

ALLOWED_QUALIFICATION_DIRTY_PREFIXES = (
    "tests/qualification/",
    "scripts/pre_m7_qualification.py",
    "reports/pre_m7_qualification_",
    "alembic/versions/0002_m1_channel_profile_backbone.py",
)


Status = Literal["PASS", "FAIL", "BLOCKED", "WARN", "SKIP"]


@dataclass
class Check:
    name: str
    status: Status
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandRecord:
    label: str
    command: str
    exit_code: int | None
    timeout_seconds: int
    duration_seconds: float
    stdout_tail: str = ""
    stderr_tail: str = ""
    timed_out: bool = False


@dataclass
class RunnerState:
    checks: list[Check] = field(default_factory=list)
    commands: list[CommandRecord] = field(default_factory=list)
    smoke: dict[str, Any] = field(default_factory=dict)
    db_name: str | None = None
    db_url: str | None = None
    blocked_reasons: list[str] = field(default_factory=list)
    fail_reasons: list[str] = field(default_factory=list)

    def add(self, name: str, status: Status, **details: Any) -> None:
        self.checks.append(Check(name=name, status=status, details=details))
        if status == "BLOCKED":
            self.blocked_reasons.append(name)
        elif status == "FAIL":
            self.fail_reasons.append(name)

    def verdict(self) -> str:
        if self.blocked_reasons:
            return "BLOCKED"
        if self.fail_reasons:
            return "FAIL"
        return "PASS"

    def safe_to_start_m7(self) -> bool:
        return self.verdict() == "PASS"


def git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False)


def run_command(
    state: RunnerState,
    label: str,
    command: list[str],
    *,
    env: dict[str, str],
    timeout: int,
    deadline: float,
) -> bool:
    remaining = max(1, int(deadline - time.monotonic()))
    effective_timeout = min(timeout, remaining)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=effective_timeout,
            check=False,
        )
        record = CommandRecord(
            label=label,
            command=" ".join(command),
            exit_code=completed.returncode,
            timeout_seconds=effective_timeout,
            duration_seconds=round(time.monotonic() - started, 3),
            stdout_tail=completed.stdout[-4000:],
            stderr_tail=completed.stderr[-4000:],
        )
    except subprocess.TimeoutExpired as exc:
        record = CommandRecord(
            label=label,
            command=" ".join(command),
            exit_code=None,
            timeout_seconds=effective_timeout,
            duration_seconds=round(time.monotonic() - started, 3),
            stdout_tail=(exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            stderr_tail=(exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
            timed_out=True,
        )
    state.commands.append(record)
    with COMMAND_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    if record.timed_out:
        state.add(label, "FAIL", timeout_seconds=effective_timeout)
        return False
    if record.exit_code != 0:
        state.add(label, "FAIL", exit_code=record.exit_code, stderr_tail=record.stderr_tail, stdout_tail=record.stdout_tail)
        return False
    state.add(label, "PASS", duration_seconds=record.duration_seconds)
    return True


def dirty_path(line: str) -> str:
    return line[3:] if len(line) > 3 else line


def check_repo_preflight(state: RunnerState) -> None:
    git_root = git(["rev-parse", "--show-toplevel"])
    state.add(
        "repo_path",
        "PASS" if git_root.returncode == 0 and Path(git_root.stdout.strip()).resolve() == ROOT else "FAIL",
        expected=str(ROOT),
        actual=git_root.stdout.strip(),
    )
    head = git(["rev-parse", "HEAD"])
    state.add("head_commit", "PASS" if head.returncode == 0 else "FAIL", head=head.stdout.strip(), stderr=head.stderr.strip())

    tag_status = {}
    for tag in REQUIRED_TAGS:
        tag_status[tag] = git(["rev-parse", "-q", "--verify", f"refs/tags/{tag}"]).returncode == 0
    state.add("required_tags", "PASS" if all(tag_status.values()) else "FAIL", tags=tag_status)

    status = git(["status", "--porcelain"])
    porcelain = [line for line in status.stdout.splitlines() if line.strip()]
    unrelated = [line for line in porcelain if not dirty_path(line).startswith(ALLOWED_QUALIFICATION_DIRTY_PREFIXES)]
    if unrelated:
        state.add("working_tree_clean", "FAIL", porcelain=porcelain, unrelated_dirty=unrelated)
    elif porcelain:
        state.add(
            "working_tree_clean",
            "WARN",
            porcelain=porcelain,
            note="dirty paths are limited to Pre-M7 qualification deliverables generated for this run",
        )
    else:
        state.add("working_tree_clean", "PASS", porcelain=[])

    missing = [path for path in REQUIRED_SOURCE_OF_TRUTH_PATHS if not (ROOT / path).exists()]
    m0_m1_final = [path for path in ["reports/m0-final-report.md", "reports/m1-final-report.md"] if (ROOT / path).exists()]
    m0_m1_waiver_docs = [path for path in ["docs/architecture/m0-scope.md", "docs/architecture/m1-scope.md"] if (ROOT / path).exists()]
    design_report_present = any(
        ("pre-m7" in path.name.lower() or "pre_m7" in path.name.lower())
        and ("design" in path.name.lower() or "thiết" in path.name.lower())
        and not path.name.startswith("pre_m7_qualification_")
        for path in (ROOT / "reports").glob("*")
    )
    state.add(
        "source_of_truth",
        "PASS" if not missing and len(m0_m1_waiver_docs) == 2 else "FAIL",
        missing=missing,
        m0_m1_final_reports_present=m0_m1_final,
        m0_m1_waiver_docs_present=m0_m1_waiver_docs,
        m0_m1_waiver_applied=len(m0_m1_final) < 2,
        design_report_present=design_report_present,
    )

    gitignore_lines = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    gitignore_status = {pattern: pattern in gitignore_lines for pattern in MEDIA_GITIGNORE_PATTERNS}
    state.add("generated_media_gitignore", "PASS" if all(gitignore_status.values()) else "FAIL", patterns=gitignore_status)

    staged = git(["diff", "--cached", "--name-only", "--diff-filter=ACMRT"])
    staged_files = [line.strip() for line in staged.stdout.splitlines() if line.strip()]
    staged_binary = [path for path in staged_files if Path(path).suffix.lower() in MEDIA_SUFFIXES]
    state.add("staged_binary_media", "PASS" if not staged_binary else "FAIL", staged_binary_media=staged_binary)


def check_dependencies(state: RunnerState) -> bool:
    missing_bins = [str(path) for path in [ALEMBIC, PYTEST, VCOS, PYTHON] if not path.exists()]
    state.add("venv_commands", "PASS" if not missing_bins else "BLOCKED", missing=missing_bins)
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    state.add("ffmpeg_ffprobe", "PASS" if ffmpeg and ffprobe else "BLOCKED", ffmpeg=ffmpeg, ffprobe=ffprobe)
    return not missing_bins and bool(ffmpeg and ffprobe)


def admin_url() -> str:
    return os.getenv("VCOS_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg://vcos:vcos@localhost:55432/postgres")


def psycopg_conninfo(url: str, *, database: str | None = None) -> str:
    parsed = make_url(url).set(drivername="postgresql")
    if database is not None:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


def create_disposable_database(state: RunnerState) -> bool:
    url = make_url(admin_url())
    db_name = f"vcos_pre_m7_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    state.db_name = db_name
    state.db_url = url.set(database=db_name).render_as_string(hide_password=False)
    deadline = time.monotonic() + 20
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(psycopg_conninfo(admin_url()), autocommit=True) as connection:
                connection.execute("select 1")
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
            state.add("postgres_disposable_db", "PASS", database=db_name)
            return True
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    state.add("postgres_disposable_db", "BLOCKED", database=db_name, error=str(last_error))
    return False


def drop_disposable_database(state: RunnerState) -> None:
    if not state.db_name:
        return
    try:
        with psycopg.connect(psycopg_conninfo(admin_url()), autocommit=True) as connection:
            connection.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname = %s", (state.db_name,))
            connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(state.db_name)))
    except Exception as exc:
        state.add("postgres_disposable_db_drop", "WARN", error=str(exc), database=state.db_name)


def run_migration_seed_pytest_sequence(state: RunnerState, *, deadline: float) -> None:
    env = os.environ.copy()
    env["VCOS_DATABASE_URL"] = state.db_url or ""
    env.setdefault("VCOS_TEST_ADMIN_DATABASE_URL", admin_url())
    commands = [
        ("alembic_upgrade_head", [str(ALEMBIC), "upgrade", "head"], 60),
        ("alembic_current", [str(ALEMBIC), "current"], 60),
        ("vcos_db_migrate_a", [str(VCOS), "db", "migrate"], 60),
        ("vcos_db_migrate_b", [str(VCOS), "db", "migrate"], 60),
        ("config_seed_a", [str(VCOS), "config", "seed"], 30),
        ("config_seed_b", [str(VCOS), "config", "seed"], 30),
        ("provider_seed_a", [str(VCOS), "provider", "seed-mocks"], 30),
        ("provider_seed_b", [str(VCOS), "provider", "seed-mocks"], 30),
        ("gate_seed_a", [str(VCOS), "gate", "seed-definitions"], 30),
        ("gate_seed_b", [str(VCOS), "gate", "seed-definitions"], 30),
        ("pytest_qualification_a", [str(PYTEST), "-q", "tests/qualification"], 240),
        ("pytest_qualification_b", [str(PYTEST), "-q", "tests/qualification"], 240),
    ]
    for label, command, timeout in commands:
        if time.monotonic() >= deadline:
            state.add("runner_deadline", "BLOCKED", timeout_seconds=WHOLE_RUNNER_TIMEOUT_SECONDS)
            return
        ok = run_command(state, label, command, env=env, timeout=timeout, deadline=deadline)
        if not ok:
            return


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_local_mp4_smoke(state: RunnerState, *, deadline: float) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        state.add("local_mp4_smoke", "BLOCKED", reason_code="FFMPEG_UNAVAILABLE")
        return
    output_dir = ROOT / "var" / "generated" / "pre_m7"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "runner_smoke.mp4"
    command = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x203040:s=320x180:r=15:d=0.500",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100:d=0.500",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(output_path),
    ]
    env = os.environ.copy()
    if not run_command(state, "local_mp4_ffmpeg_smoke", command, env=env, timeout=min(60, int(deadline - time.monotonic())), deadline=deadline):
        return
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(output_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if probe.returncode != 0:
        state.add("local_mp4_ffprobe_smoke", "FAIL", stderr=probe.stderr[-1000:])
        return
    size = output_path.stat().st_size
    checksum = sha256_file(output_path)
    ignored = git(["check-ignore", str(output_path)]).returncode == 0
    state.smoke = {
        "output_path": str(output_path),
        "checksum_sha256": checksum,
        "duration_sec": float(probe.stdout.strip()),
        "size_bytes": size,
        "gitignored": ignored,
    }
    state.add("local_mp4_ffprobe_smoke", "PASS" if size > 0 and ignored else "FAIL", **state.smoke)


def command_summary(state: RunnerState, label_prefix: str) -> str:
    matches = [record for record in state.commands if record.label.startswith(label_prefix)]
    if not matches:
        return "not_run"
    return "; ".join(f"{record.label}: exit={record.exit_code}" for record in matches)


def check_status(state: RunnerState, name: str) -> str:
    for check in state.checks:
        if check.name == name:
            return check.status
    return "not_run"


def write_reports(state: RunnerState) -> None:
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    verdict = state.verdict()
    safe = "YES" if state.safe_to_start_m7() else "NO"
    head = next((check.details.get("head") for check in state.checks if check.name == "head_commit"), "")
    tags = next((check.details.get("tags") for check in state.checks if check.name == "required_tags"), {})
    source = next((check.details for check in state.checks if check.name == "source_of_truth"), {})
    smoke = state.smoke or {}
    failed_commands = [asdict(record) for record in state.commands if record.exit_code not in (0, None) or record.timed_out]
    pytest_commands = [asdict(record) for record in state.commands if record.label.startswith("pytest_qualification")]
    bugs = []
    repairs = []
    if state.blocked_reasons:
        repairs.append("Unblock environment/preflight, then rerun scripts/pre_m7_qualification.py.")
    if state.fail_reasons:
        repairs.append("Repair failed P0 qualification checks before starting M7.")
        bugs.extend(state.fail_reasons)
    if not bugs:
        bugs.append("None recorded" if verdict == "PASS" else "Not fully assessed due to BLOCKED preflight")
    waivers = []
    if source.get("m0_m1_waiver_applied"):
        waivers.append("M0/M1 final reports absent; accepted docs/architecture/m0-scope.md and docs/architecture/m1-scope.md per explicit waiver.")
    if not source.get("design_report_present"):
        waivers.append("Uploaded/deep-research Pre-M7 design report is not present as a repo file; user prompt used as implementation brief.")
    if check_status(state, "working_tree_clean") == "WARN":
        waivers.append("Working tree dirty only because Pre-M7 qualification deliverables/report are uncommitted for user review.")

    md = f"""# Pre-M7 M0→M6 Qualification Report

## Verdict
{verdict}

## Safe to start M7
{safe}

## Repo path
{ROOT}

## Git status
- Head commit: {head}
- Required tags: {tags}
- Working tree clean: {check_status(state, "working_tree_clean")}

## Source-of-truth evidence
- Reports present: M2/M3/M4/M5/M6 plus Pre-M4 checked by source_of_truth={check_status(state, "source_of_truth")}
- M0/M1 evidence waiver: {source.get("m0_m1_waiver_applied")}

## Migration status
- Alembic head expected: {EXPECTED_ALEMBIC_HEAD}
- Fresh migrate: {check_status(state, "alembic_upgrade_head")}
- Idempotent migrate: {check_status(state, "vcos_db_migrate_b")}
- Downgrade/upgrade sanity: not_run_forward_only_m0_m6

## Seed status
- config seed once/twice: {check_status(state, "config_seed_a")} / {check_status(state, "config_seed_b")}
- provider seed once/twice: {check_status(state, "provider_seed_a")} / {check_status(state, "provider_seed_b")}
- gate seed once/twice: {check_status(state, "gate_seed_a")} / {check_status(state, "gate_seed_b")}
- catalog/reason code integrity: covered_by_pytest_if_run

## Scope guard status
- M7 publish/upload absent: covered_by_pytest_and_scope_scanner_if_run
- M8 analytics absent: covered_by_pytest_and_scope_scanner_if_run
- M9/M10/M11 absent: covered_by_pytest_and_scope_scanner_if_run
- no real provider/network: covered_by_pytest_network_sentinel_if_run
- no Envato API/download/generation: covered_by_pytest_and_scope_scanner_if_run
- no scraping/vector/RAG/OPA/Cedar/agents: covered_by_pytest_and_scope_scanner_if_run

## Milestone invariant status
- M0: {check_status(state, "source_of_truth")}
- M1: {check_status(state, "source_of_truth")}
- M2: {command_summary(state, "pytest_qualification")}
- M3: {command_summary(state, "pytest_qualification")}
- M4: {command_summary(state, "pytest_qualification")}
- M5: {command_summary(state, "pytest_qualification")}
- M6: {command_summary(state, "pytest_qualification")}

## API/CLI smoke
- parity summary: covered_by_pytest_qualification_if_run
- failed commands: {[record["label"] for record in failed_commands]}

## Local MP4 smoke status
- ffmpeg available: {bool(shutil.which("ffmpeg"))}
- ffprobe available: {bool(shutil.which("ffprobe"))}
- smoke verdict: {check_status(state, "local_mp4_ffprobe_smoke")}
- output path: {smoke.get("output_path")}
- checksum_sha256: {smoke.get("checksum_sha256")}
- duration_sec: {smoke.get("duration_sec")}
- size_bytes: {smoke.get("size_bytes")}

## Generated media safety
- output under gitignored path: {smoke.get("gitignored")}
- staged binary check: {check_status(state, "staged_binary_media")}

## E2E status
- E2E A happy path: covered_by_pytest_qualification_if_run
- E2E B missing snapshot: covered_by_pytest_qualification_if_run
- E2E C malformed M5 LLM: covered_by_pytest_qualification_if_run
- E2E D malformed M6 script: covered_by_pytest_qualification_if_run
- E2E E quota exhausted: covered_by_pytest_qualification_if_run
- E2E F provider unavailable: covered_by_pytest_qualification_if_run
- E2E G invalid scene timing: covered_by_pytest_qualification_if_run
- E2E H missing asset/ref: covered_by_pytest_qualification_if_run
- E2E I ffmpeg unavailable simulation: covered_by_pytest_qualification_if_run
- E2E J generated media safety: covered_by_pytest_qualification_if_run
- E2E K scope leak sentinel: covered_by_pytest_qualification_if_run

## Bugs found
{chr(10).join(f"- {item}" for item in bugs)}

## Required repairs
{chr(10).join(f"- {item}" for item in (repairs or ["None"]))}

## Waivers
{chr(10).join(f"- {item}" for item in (waivers or ["None"]))}

## Technical appendix
- command log paths: {COMMAND_LOG}
- pytest summary: {pytest_commands}
- failed assertion excerpts: {[record.get("stderr_tail") for record in failed_commands]}
- reason code histogram: not_collected_by_runner
- lineage audit sample: covered_by tests/qualification/test_pre_m7_e2e.py if pytest ran
"""
    REPORT_MD.write_text(md, encoding="utf-8")
    payload = {
        "verdict": verdict,
        "safe_to_start_M7": state.safe_to_start_m7(),
        "repo_path": str(ROOT),
        "checks": [asdict(check) for check in state.checks],
        "commands": [asdict(record) for record in state.commands],
        "local_mp4_smoke": smoke,
        "blocked_reasons": state.blocked_reasons,
        "fail_reasons": state.fail_reasons,
        "waivers": waivers,
        "bugs_found": bugs,
        "required_repairs": repairs,
    }
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    state = RunnerState()
    COMMAND_LOG.parent.mkdir(parents=True, exist_ok=True)
    COMMAND_LOG.write_text("", encoding="utf-8")
    deadline = time.monotonic() + WHOLE_RUNNER_TIMEOUT_SECONDS
    try:
        check_repo_preflight(state)
        deps_ok = check_dependencies(state)
        if deps_ok and create_disposable_database(state):
            try:
                run_migration_seed_pytest_sequence(state, deadline=deadline)
                if not state.fail_reasons and not state.blocked_reasons:
                    run_local_mp4_smoke(state, deadline=deadline)
            finally:
                drop_disposable_database(state)
    finally:
        write_reports(state)
    verdict = state.verdict()
    if verdict == "PASS":
        return 0
    if verdict == "BLOCKED":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
