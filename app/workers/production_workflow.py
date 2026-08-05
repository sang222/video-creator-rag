"""Runtime loop for the durable production-workflow outbox."""

from __future__ import annotations

import importlib
import os
import re
import signal
import socket
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.actor import ActorContext, _system_worker_actor
from app.core.db import get_session_factory
from app.core.time import utc_now
from app.db.models.launch_cadence import LaunchRun
from app.services.outbox_dispatcher import (
    ClaimedWorkflowEvent,
    DurableOutboxDispatcher,
    OutboxLeaseLostError,
)
from app.services.cadence_events import CADENCE_EVALUATION_EVENT_TYPE
from app.services.script_qualification import SCRIPT_QUALIFICATION_EVENT_TYPE
from app.services.long_form_analytics import (
    ANALYTICS_WINDOW_EVENT_TYPE,
    LEARNING_GENERATION_EVENT_TYPE,
    LongFormAnalyticsScheduler,
)
from app.services.production_workflow import (
    PreReadinessProductionGateway,
    PostReadinessProductionGateway,
    ProductionStageHandlerRegistry,
    ProductionWorkflowCoordinator,
    build_default_stage_handler_registry,
)
from app.services.stale_workflow_recovery import (
    STALE_WORKFLOW_RECOVERY_EVENT_TYPE,
    StaleWorkflowRecoveryService,
)


SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    status: str
    event_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None
    command_id: str | None = None
    retry_scheduled: bool = False
    dead_letter_job_id: uuid.UUID | None = None


class ProductionWorkflowWorker:
    """One process-safe worker; multiple instances may share the same queue."""

    def __init__(
        self,
        *,
        handlers: ProductionStageHandlerRegistry | None = None,
        pre_readiness_gateway: PreReadinessProductionGateway | None = None,
        post_readiness_gateway: PostReadinessProductionGateway | None = None,
        session_factory: SessionFactory | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 60,
        max_execution_seconds: int = 3600,
        heartbeat_interval_seconds: float | None = None,
        poll_interval_seconds: float = 1.0,
        cadence_scan_interval_seconds: float = 60.0,
        after_stage_before_ack: (Callable[[ClaimedWorkflowEvent], None] | None) = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        if poll_interval_seconds <= 0 or poll_interval_seconds > 60:
            raise ValueError("poll_interval_seconds must be in (0, 60]")
        if cadence_scan_interval_seconds <= 0 or cadence_scan_interval_seconds > 3600:
            raise ValueError("cadence_scan_interval_seconds must be in (0, 3600]")
        if handlers is not None and (
            pre_readiness_gateway is not None or post_readiness_gateway is not None
        ):
            raise ValueError("handlers and production gateways are mutually exclusive")
        if handlers is None:
            if pre_readiness_gateway is None:
                from app.services.v2_package_readiness import (
                    build_v2_package_readiness_gateway,
                )

                pre_readiness_gateway = build_v2_package_readiness_gateway()
            if post_readiness_gateway is None:
                from app.services.v2_provider_production import (
                    build_v2_provider_production_gateway,
                )

                post_readiness_gateway = build_v2_provider_production_gateway()
        self.handlers = handlers or build_default_stage_handler_registry(
            pre_readiness_gateway=pre_readiness_gateway,
            post_readiness_gateway=post_readiness_gateway,
        )
        self.session_factory = session_factory or get_session_factory()
        self.worker_id = worker_id or _default_worker_id()
        self.lease_seconds = lease_seconds
        self.max_execution_seconds = max_execution_seconds
        self.heartbeat_interval_seconds = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else max(0.25, min(20.0, lease_seconds / 3))
        )
        if (
            self.heartbeat_interval_seconds <= 0
            or self.heartbeat_interval_seconds >= lease_seconds
        ):
            raise ValueError(
                "heartbeat interval must be positive and below lease duration"
            )
        self.poll_interval_seconds = poll_interval_seconds
        self.cadence_scan_interval_seconds = cadence_scan_interval_seconds
        self.after_stage_before_ack = after_stage_before_ack
        self.now = now
        self._stop = threading.Event()
        self._actor = _trusted_system_worker_actor()
        self._next_cadence_scan_at: datetime | None = None
        self._next_editorial_replenishment_scan_at: datetime | None = None

    def run_once(self) -> WorkerRunResult:
        self._enqueue_due_stale_workflow_recoveries()
        self._run_due_editorial_replenishments()
        self._enqueue_due_cadence_evaluations()
        self._enqueue_due_analytics_windows()
        claim = self._claim()
        if claim is None:
            return WorkerRunResult(status="IDLE")

        pump = _HeartbeatPump(
            session_factory=self.session_factory,
            event_id=claim.event_id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            max_execution_seconds=self.max_execution_seconds,
            interval_seconds=self.heartbeat_interval_seconds,
            now=self.now,
        )
        session = self.session_factory()
        try:
            dispatcher = self._dispatcher(session)
            event = dispatcher.require_claimed_event(
                event_id=claim.event_id, worker_id=self.worker_id
            )
            pump.start()
            if event.event_type == CADENCE_EVALUATION_EVENT_TYPE:
                from app.contracts.launch_cadence import (
                    CadenceEvaluationCommand,
                )
                from app.services.launch_cadence import LongFormCadenceService

                launch_run_id = uuid.UUID(str(event.payload["launch_run_id"]))
                if launch_run_id != event.aggregate_id or not event.payload.get(
                    "evaluation_key"
                ):
                    raise ValueError("CADENCE_EVENT_AUTHORITY_MISMATCH")
                LongFormCadenceService(session, now=self.now).evaluate(
                    launch_run_id=launch_run_id,
                    data=CadenceEvaluationCommand(
                        evaluation_key=str(event.payload["evaluation_key"])
                    ),
                    actor=self._actor,
                )
            elif event.event_type == SCRIPT_QUALIFICATION_EVENT_TYPE:
                from app.services.launch_cadence import LongFormCadenceService
                from app.services.script_qualification import ScriptQualificationService

                run_id = uuid.UUID(str(event.payload["script_qualification_run_id"]))
                if run_id != event.aggregate_id:
                    raise ValueError("SCRIPT_QUALIFICATION_EVENT_AUTHORITY_MISMATCH")
                qualification = ScriptQualificationService(session, now=self.now).execute(run_id)
                if qualification.state == "QUALIFIED":
                    LongFormCadenceService(session, now=self.now).finalize_qualified_script_run(
                        script_qualification_run_id=run_id, actor=self._actor
                    )
            elif event.event_type == STALE_WORKFLOW_RECOVERY_EVENT_TYPE:
                StaleWorkflowRecoveryService(session, now=self.now).execute_event(
                    event=event,
                    actor=self._actor,
                )
            elif event.event_type == ANALYTICS_WINDOW_EVENT_TYPE:
                LongFormAnalyticsScheduler(session, now=self.now).execute_window(
                    uuid.UUID(str(event.payload["analytics_window_id"]))
                )
            elif event.event_type == LEARNING_GENERATION_EVENT_TYPE:
                from app.services.m10 import LearningCandidateGenerationService

                LearningCandidateGenerationService(session).execute_window_command(
                    analytics_window_id=uuid.UUID(str(event.payload["analytics_window_id"])),
                    command_key=str(event.payload["learning_command_key"]),
                    policy=dict(event.payload.get("learning_policy") or {}),
                    policy_hash=str(event.payload["learning_policy_hash"]),
                )
            else:
                coordinator = ProductionWorkflowCoordinator(
                    session,
                    handlers=self.handlers,
                    now=self.now,
                )
                coordinator.execute_event(
                    event=event,
                    actor=self._actor,
                    heartbeat=pump.heartbeat_now,
                    max_execution_seconds=max(
                        1,
                        int((claim.execution_deadline - self.now()).total_seconds()),
                    ),
                )
            pump.stop()
            if pump.error is not None:
                raise pump.error
            if self.after_stage_before_ack is not None:
                self.after_stage_before_ack(claim)
            dispatcher.mark_delivered(event_id=claim.event_id, worker_id=self.worker_id)
            session.commit()
            return WorkerRunResult(
                status="DELIVERED",
                event_id=claim.event_id,
                workflow_run_id=claim.workflow_run_id,
                command_id=claim.command_id,
            )
        except OutboxLeaseLostError:
            session.rollback()
            return WorkerRunResult(
                status="LEASE_LOST",
                event_id=claim.event_id,
                workflow_run_id=claim.workflow_run_id,
                command_id=claim.command_id,
            )
        except Exception as exc:
            session.rollback()
            return self._record_failure(claim, exc)
        finally:
            pump.stop()
            session.close()

    def run_forever(self) -> None:
        """Poll until ``stop`` is called, then release recoverable leases."""

        try:
            while not self._stop.is_set():
                result = self.run_once()
                if result.status == "IDLE":
                    self._stop.wait(self.poll_interval_seconds)
        finally:
            self.release_leases()

    def stop(self) -> None:
        self._stop.set()

    def release_leases(self) -> int:
        session = self.session_factory()
        try:
            count = self._dispatcher(session).release_worker_leases(
                worker_id=self.worker_id
            )
            session.commit()
            return count
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _claim(self) -> ClaimedWorkflowEvent | None:
        session = self.session_factory()
        try:
            claim = self._dispatcher(session).claim_next(worker_id=self.worker_id)
            session.commit()
            return claim
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _enqueue_due_cadence_evaluations(self) -> int:
        """Use the existing worker/outbox to evaluate every active launch.

        The hourly command identity in ``LongFormCadenceService`` makes scans
        idempotent. Row locks with ``SKIP LOCKED`` let multiple worker
        processes share the scan without producing duplicate commands.
        """

        scan_started_at = self.now()
        if (
            self._next_cadence_scan_at is not None
            and scan_started_at < self._next_cadence_scan_at
        ):
            return 0
        session = self.session_factory()
        try:
            from app.services.launch_cadence import LongFormCadenceService

            launch_runs = list(
                session.scalars(
                    select(LaunchRun)
                    .where(LaunchRun.state == "ACTIVE")
                    .order_by(LaunchRun.id)
                    .with_for_update(skip_locked=True)
                ).all()
            )
            cadence = LongFormCadenceService(session, now=self.now)
            for launch_run in launch_runs:
                cadence.request_evaluation(
                    launch_run_id=launch_run.id,
                    actor=self._actor,
                )
            session.commit()
            self._next_cadence_scan_at = scan_started_at + timedelta(
                seconds=self.cadence_scan_interval_seconds
            )
            return len(launch_runs)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _run_due_editorial_replenishments(self) -> int:
        """Replenish editorial runway before the production-cadence phase.

        ``EditorialResearchRun`` is the durable execution identity.  The
        replenishment service locks each active launch and records terminal
        blockers, so no synthetic candidate or production side effect can be
        produced by this scheduler phase.
        """

        scan_started_at = self.now()
        if (
            self._next_editorial_replenishment_scan_at is not None
            and scan_started_at < self._next_editorial_replenishment_scan_at
        ):
            return 0
        session = self.session_factory()
        try:
            from app.services.editorial_runway_replenishment import (
                EditorialRunwayReplenishmentService,
            )

            results = EditorialRunwayReplenishmentService(
                session, now=self.now
            ).reconcile_active_launches(actor=self._actor)
            session.commit()
            self._next_editorial_replenishment_scan_at = scan_started_at + timedelta(
                seconds=self.cadence_scan_interval_seconds
            )
            return len(results)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _enqueue_due_stale_workflow_recoveries(self) -> int:
        """Use the normal worker outbox for zero-effect stale recovery only."""

        session = self.session_factory()
        try:
            count = StaleWorkflowRecoveryService(session, now=self.now).enqueue_due()
            session.commit()
            return count
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _enqueue_due_analytics_windows(self) -> int:
        session = self.session_factory()
        try:
            count = LongFormAnalyticsScheduler(
                session, now=self.now
            ).enqueue_due_windows()
            session.commit()
            return count
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _record_failure(
        self, claim: ClaimedWorkflowEvent, error: Exception
    ) -> WorkerRunResult:
        session = self.session_factory()
        try:
            disposition = self._dispatcher(session).record_failure(
                event_id=claim.event_id,
                worker_id=self.worker_id,
                error=error,
            )
            session.commit()
            return WorkerRunResult(
                status=(
                    "CANCELED"
                    if disposition.workflow_canceled
                    else (
                        "RETRY_SCHEDULED"
                        if disposition.retry_scheduled
                        else "DEAD_LETTERED"
                    )
                ),
                event_id=claim.event_id,
                workflow_run_id=claim.workflow_run_id,
                command_id=claim.command_id,
                retry_scheduled=disposition.retry_scheduled,
                dead_letter_job_id=disposition.dead_letter_job_id,
            )
        except OutboxLeaseLostError:
            session.rollback()
            return WorkerRunResult(
                status="LEASE_LOST",
                event_id=claim.event_id,
                workflow_run_id=claim.workflow_run_id,
                command_id=claim.command_id,
            )
        finally:
            session.close()

    def _dispatcher(self, session: Session) -> DurableOutboxDispatcher:
        return DurableOutboxDispatcher(
            session,
            lease_seconds=self.lease_seconds,
            max_execution_seconds=self.max_execution_seconds,
            now=self.now,
        )


class _HeartbeatPump:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        event_id: uuid.UUID,
        worker_id: str,
        lease_seconds: int,
        max_execution_seconds: int,
        interval_seconds: float,
        now: Callable[[], datetime],
    ) -> None:
        self.session_factory = session_factory
        self.event_id = event_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.max_execution_seconds = max_execution_seconds
        self.interval_seconds = interval_seconds
        self.now = now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.error: Exception | None = None

    def start(self) -> None:
        self.heartbeat_now()
        self._thread = threading.Thread(
            target=self._run,
            name=f"workflow-heartbeat-{self.event_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if (
            self._thread is not None
            and self._thread.is_alive()
            and self._thread is not threading.current_thread()
        ):
            self._thread.join(timeout=min(5.0, self.interval_seconds + 1.0))

    def heartbeat_now(self) -> None:
        if self.error is not None:
            raise self.error
        with self._lock:
            session = self.session_factory()
            try:
                DurableOutboxDispatcher(
                    session,
                    lease_seconds=self.lease_seconds,
                    max_execution_seconds=self.max_execution_seconds,
                    now=self.now,
                ).heartbeat(
                    event_id=self.event_id,
                    worker_id=self.worker_id,
                )
                session.commit()
            except Exception as exc:
                session.rollback()
                self.error = exc
                raise
            finally:
                session.close()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.heartbeat_now()
            except Exception:
                self._stop.set()
                return


def _trusted_system_worker_actor() -> ActorContext:
    """Only worker runtime code may mint this allowlisted identity."""

    return _system_worker_actor(
        "vcos-durable-worker",
        permissions={
            "editorial.manage",
            "production.workflow.execute",
            "production.start",
            "production.cancel",
        },
    )


def _default_worker_id() -> str:
    host = re_safe_worker_component(socket.gethostname())
    return f"{host}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


def re_safe_worker_component(value: str) -> str:
    sanitized = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in value
    )
    return (sanitized or "worker")[:80]


def main() -> None:
    """Run the durable outbox worker as a supervised process."""

    worker = ProductionWorkflowWorker(
        post_readiness_gateway=load_post_readiness_gateway_from_env(),
        worker_id=os.getenv("VCOS_WORKFLOW_WORKER_ID") or None,
        lease_seconds=_env_int("VCOS_WORKFLOW_LEASE_SECONDS", 60),
        max_execution_seconds=_env_int("VCOS_WORKFLOW_MAX_EXECUTION_SECONDS", 3600),
        heartbeat_interval_seconds=_env_float_or_none(
            "VCOS_WORKFLOW_HEARTBEAT_INTERVAL_SECONDS"
        ),
        poll_interval_seconds=_env_float("VCOS_WORKFLOW_POLL_INTERVAL_SECONDS", 1.0),
        cadence_scan_interval_seconds=_env_float(
            "VCOS_CADENCE_SCAN_INTERVAL_SECONDS",
            60.0,
        ),
    )

    def request_stop(_signum: int, _frame: object) -> None:
        worker.stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    worker.run_forever()


def load_post_readiness_gateway_from_env(
    value: str | None = None,
) -> PostReadinessProductionGateway | None:
    """Resolve one explicitly configured production gateway factory.

    The value is ``python.module:factory``.  No gateway is guessed from
    installed packages, and an invalid configuration fails worker startup
    rather than silently falling back to recovery-only behavior.
    """

    configured = (
        value if value is not None else os.getenv("VCOS_POST_READINESS_GATEWAY_FACTORY")
    )
    if configured is None or not configured.strip():
        return None
    configured = configured.strip()
    if not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*",
        configured,
    ):
        raise RuntimeError(
            "VCOS_POST_READINESS_GATEWAY_FACTORY must be python.module:factory"
        )
    module_name, factory_name = configured.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
        gateway = factory()
    except Exception as exc:
        raise RuntimeError(
            "VCOS_POST_READINESS_GATEWAY_FACTORY could not be loaded"
        ) from exc
    if not isinstance(gateway, PostReadinessProductionGateway):
        raise RuntimeError(
            "VCOS_POST_READINESS_GATEWAY_FACTORY returned an invalid gateway"
        )
    # Registry construction performs the complete safety-declaration check.
    build_default_stage_handler_registry(post_readiness_gateway=gateway)
    return gateway


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc


def _env_float_or_none(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc


if __name__ == "__main__":
    main()
