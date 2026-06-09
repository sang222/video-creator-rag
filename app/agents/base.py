from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import AgentRun
from app.services.cost import CostTrackingService


class Agent(ABC):
    name = "Agent"
    node_name = "agent"
    estimated_cost = 0.001

    def __init__(self, cost_service: CostTrackingService | None = None) -> None:
        self.cost_service = cost_service or CostTrackingService()

    def __call__(
        self,
        db: Session,
        *,
        company_id: str,
        workspace_id: str,
        project_id: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        output = self.run(payload)
        db.add(
            AgentRun(
                company_id=company_id,
                workspace_id=workspace_id,
                project_id=project_id,
                agent_name=self.name,
                node_name=self.node_name,
                input_json=payload,
                output_json=output,
                status="SUCCESS",
            )
        )
        self.cost_service.record(
            db,
            company_id=company_id,
            workspace_id=workspace_id,
            project_id=project_id,
            agent_name=self.name,
            node_name=self.node_name,
            provider="mock",
            model="phase1-mock",
            input_tokens=len(str(payload).split()),
            output_tokens=len(str(output).split()),
            estimated_cost=self.estimated_cost,
        )
        return output

    @abstractmethod
    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

