from fastapi import FastAPI, HTTPException, status

from app.core.config import get_settings
from app.core.db import check_database
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    application = FastAPI(title=settings.app_name)

    @application.get("/health")
    def health() -> dict[str, str]:
        try:
            check_database(settings.database_url)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="database unavailable",
            ) from exc
        return {
            "status": "ok",
            "app": settings.app_name,
            "database": "ok",
        }

    return application


app = create_app()
