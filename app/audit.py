"""Audit log API endpoints."""
import os
from datetime import datetime
from fastapi import APIRouter, Depends, Request, Response

from . import database

ENABLE_USER_AUTH = os.getenv("ENABLE_USER_AUTH", "1") == "1"
if ENABLE_USER_AUTH:
    from .dependencies import get_current_user
else:  # pragma: no cover
    def get_current_user():  # type: ignore[override]
        return "anonymous"

router = APIRouter(prefix="/audit", tags=["audit"])


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@router.get("/logs")
def list_logs(
    start: str | None = None,
    end: str | None = None,
    user: str | None = None,
    current_user: str = Depends(get_current_user),
):
    return database.query_audit_logs(_parse_dt(start), _parse_dt(end), user)


@router.get("/logs/export")
def export_logs(
    start: str | None = None,
    end: str | None = None,
    user: str | None = None,
    current_user: str = Depends(get_current_user),
):
    csv_data = database.export_audit_logs_csv(_parse_dt(start), _parse_dt(end), user)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit.csv"},
    )
