from fastapi import APIRouter, status
from app.core.config import settings
from app.core.database import check_database_connection

router = APIRouter(tags=["Health"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    """Basic API health check endpoint."""
    return {
        "status": "online",
        "service": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT
    }


@router.get("/health/db", status_code=status.HTTP_200_OK)
async def db_health_check():
    """Database connectivity health check endpoint."""
    db_status = await check_database_connection()
    if db_status["status"] != "connected":
        return {
            "status": "degraded",
            "database": db_status
        }
    return {
        "status": "healthy",
        "database": db_status
    }
