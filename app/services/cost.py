from sqlalchemy.orm import Session

from app.models.entities import CostEvent


class CostTrackingService:
    def record(
        self,
        db: Session,
        *,
        company_id: str,
        workspace_id: str,
        project_id: str | None,
        agent_name: str,
        node_name: str,
        provider: str = "mock",
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        media_units: float = 0.0,
        estimated_cost: float = 0.0,
        raw_usage_json: dict | None = None,
    ) -> CostEvent:
        event = CostEvent(
            company_id=company_id,
            workspace_id=workspace_id,
            project_id=project_id,
            agent_name=agent_name,
            node_name=node_name,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            media_units=media_units,
            estimated_cost=estimated_cost,
            raw_usage_json=raw_usage_json or {},
        )
        db.add(event)
        return event
