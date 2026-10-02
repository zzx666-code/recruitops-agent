"""Local same-origin API for the shared schedule-item snapshot table."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, NoReturn
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from apps.api.local_ui import _storage, local_ui_request
from packages.config import get_settings
from packages.domain.models import ScheduleEvent
from packages.storage import Storage
from packages.storage.models import ScheduleEventSnapshot
from packages.tools.schedule_manage import (
    ScheduleConflictError,
    ScheduleEventCreateFields,
    ScheduleEventPatchFields,
    ScheduleManageData,
    ScheduleManager,
    ScheduleNotFoundError,
    ScheduleValidationError,
)


router = APIRouter(prefix="/api/local-ui", tags=["local-ui"])


class ScheduleEventPatchRequest(ScheduleEventPatchFields):
    expected_updated_at: datetime


class ScheduleEventMutationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["created", "updated"]
    event: ScheduleEvent


def _map_error(error: Exception) -> NoReturn:
    if isinstance(error, ScheduleNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, ScheduleConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, ScheduleValidationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error


@router.post(
    "/events",
    response_model=ScheduleEventMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule_event(body: ScheduleEventCreateFields) -> ScheduleEventMutationResponse:
    try:
        data = ScheduleManager(_storage()).create(
            body,
            source="local_ui",
            source_ref="local-ui:" + uuid4().hex,
        )
    except (ScheduleNotFoundError, ScheduleConflictError, ScheduleValidationError) as error:
        _map_error(error)
    return ScheduleEventMutationResponse(status="created", event=data.event)


@router.patch(
    "/events/{event_id}",
    response_model=ScheduleEventMutationResponse,
)
def update_schedule_event(
    event_id: str,
    body: ScheduleEventPatchRequest,
) -> ScheduleEventMutationResponse:
    try:
        data = ScheduleManager(_storage()).update(
            event_id,
            body,
            expected_updated_at=body.expected_updated_at,
        )
    except (ScheduleNotFoundError, ScheduleConflictError, ScheduleValidationError) as error:
        _map_error(error)
    return ScheduleEventMutationResponse(status="updated", event=data.event)


@router.patch("/mail-tasks/{event_id}/status", response_model=ScheduleEventMutationResponse)
def update_mail_task_status(event_id: str, body: ScheduleEventPatchRequest) -> ScheduleEventMutationResponse:
    """Update only completion state on a mail-sourced task."""
    settings = get_settings()
    if not local_ui_request.get() or not (settings.write_enabled or settings.local_mail_tasks_enabled):
        raise HTTPException(403, "招聘邮箱待办状态更新未启用")
    if body.model_fields_set != {"status", "expected_updated_at"} or body.status not in {"pending", "completed", "ignored"}:
        raise HTTPException(422, "只能更新邮件待办的状态")
    storage = Storage.from_url(settings.database_url)
    with storage.session() as session:
        row = session.get(ScheduleEventSnapshot, event_id)
        if row is None or row.source != "recruitment_mail_schedule":
            raise HTTPException(404, "邮件待办不存在")
    try:
        data = ScheduleManager(storage).update(event_id, body, expected_updated_at=body.expected_updated_at)
    except (ScheduleNotFoundError, ScheduleConflictError, ScheduleValidationError) as error:
        _map_error(error)
    return ScheduleEventMutationResponse(status="updated", event=data.event)


__all__ = [
    "ScheduleEventMutationResponse",
    "ScheduleEventPatchRequest",
    "create_schedule_event",
    "router",
    "update_schedule_event",
]
