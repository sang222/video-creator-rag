"""Review or apply the versioned editorial-specificity cleanup.

This utility never mutates rows directly.  ``--apply`` delegates every legal
GREENLIT -> REJECTED transition to the editorial domain service; without it,
the output is a read-only cleanup plan.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.actor import _system_worker_actor
from app.core.db import get_session_factory
from app.services.editorial_specificity import EditorialSpecificityMaintenanceService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    session = get_session_factory()()
    try:
        service = EditorialSpecificityMaintenanceService(session)
        plan = service.plan()
        report = service.report(plan)
        cleanup_plan_valid = all(
            item["proposed_maintenance_action"]
            in {"KEEP", "REJECT", "PRESERVE_CONFLICT", "PRESERVE_NON_GREENLIT"}
            for item in report
        )
        payload = {
            "CLEANUP_PLAN_VALID": cleanup_plan_valid,
            "APPLY_REQUESTED": args.apply,
            "actions": report,
        }
        if args.apply:
            actor = _system_worker_actor(
                "vcos-durable-worker", permissions={"editorial.manage"}
            )
            service.apply(actions=plan, actor=actor)
            session.commit()
            payload["APPLIED"] = True
        else:
            session.rollback()
            payload["APPLIED"] = False
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
