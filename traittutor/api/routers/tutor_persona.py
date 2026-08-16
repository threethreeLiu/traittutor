"""Owner-derived API for the typed, style-only Tutor Persona profile."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from traittutor.multi_user.context import get_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.tutor_persona.compiler import TutorPersonaContract
from traittutor.tutor_persona.models import TutorPersonaProfile, TutorPersonaSettings
from traittutor.tutor_persona.reminder_outbox import (
    ReminderDeliveryStatus,
    TutorReminder,
    TutorReminderOutbox,
)
from traittutor.tutor_persona.reminders import ReminderEligibility
from traittutor.tutor_persona.service import TutorPersonaService
from traittutor.tutor_persona.store import (
    TutorPersonaIdempotencyConflict,
    TutorPersonaStore,
    TutorPersonaStoreError,
    TutorPersonaVersionConflict,
)

router = APIRouter()


class TutorPersonaProfileResponse(BaseModel):
    """Public allowlist excluding owner identity and persistence internals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    persona_id: str
    version: int
    settings: TutorPersonaSettings
    created_at: str
    updated_at: str


class ReplaceTutorPersonaRequest(BaseModel):
    """A complete typed replacement with explicit concurrency and replay keys."""

    model_config = ConfigDict(extra="forbid")

    settings: TutorPersonaSettings
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=160)


class ResetTutorPersonaRequest(BaseModel):
    """Reset also carries CAS and idempotency controls."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=160)


class PreviewTutorPersonaRequest(BaseModel):
    """Preview is deterministic and never persists candidate settings."""

    model_config = ConfigDict(extra="forbid")

    settings: TutorPersonaSettings | None = None


TutorPersonaStoreFactory = Callable[[CurrentUser], TutorPersonaStore]
TutorReminderOutboxFactory = Callable[[CurrentUser], TutorReminderOutbox]


def default_tutor_persona_store_factory(user: CurrentUser) -> TutorPersonaStore:
    """Bind the profile store to the authenticated identity, never request input."""
    return TutorPersonaStore(user.id)


tutor_persona_store_factory: TutorPersonaStoreFactory = default_tutor_persona_store_factory


def default_tutor_reminder_outbox_factory(user: CurrentUser) -> TutorReminderOutbox:
    return TutorReminderOutbox(user.id)


tutor_reminder_outbox_factory: TutorReminderOutboxFactory = default_tutor_reminder_outbox_factory


def get_tutor_persona_service() -> TutorPersonaService:
    return TutorPersonaService(tutor_persona_store_factory(get_current_user()))


TutorPersonaServiceDependency = Annotated[TutorPersonaService, Depends(get_tutor_persona_service)]


def get_tutor_reminder_outbox() -> TutorReminderOutbox:
    return tutor_reminder_outbox_factory(get_current_user())


TutorReminderOutboxDependency = Annotated[
    TutorReminderOutbox,
    Depends(get_tutor_reminder_outbox),
]


class TutorReminderResponse(BaseModel):
    """Reference-only in-app reminder with no hidden answer or owner field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reminder_id: str
    kind: Literal["review_due"]
    reference_id: str
    learning_path_id: str
    subject_id: str
    kc_id: str
    due_at: str
    status: ReminderDeliveryStatus
    queued_at: str
    delivered_at: str | None
    read_at: str | None
    cancelled_at: str | None


def _reminder_public(reminder: TutorReminder) -> TutorReminderResponse:
    return TutorReminderResponse.model_validate(
        reminder.model_dump(
            mode="json",
            exclude={"owner_id", "profile_version", "attempts"},
        )
    )


def _public(profile: TutorPersonaProfile) -> TutorPersonaProfileResponse:
    return TutorPersonaProfileResponse(
        persona_id=profile.persona_id,
        version=profile.version,
        settings=TutorPersonaSettings.model_validate(
            profile.model_dump(
                exclude={
                    "persona_id",
                    "owner_id",
                    "version",
                    "created_at",
                    "updated_at",
                    "schema_version",
                }
            )
        ),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _raise_store_error(exc: Exception) -> NoReturn:
    if isinstance(exc, TutorPersonaVersionConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "version_conflict",
                "expected_version": exc.expected_version,
                "actual_version": exc.actual_version,
            },
        ) from exc
    if isinstance(exc, TutorPersonaIdempotencyConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="idempotency key conflict"
        ) from exc
    if isinstance(exc, (TutorPersonaStoreError, ValueError)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    raise exc


@router.get("", response_model=TutorPersonaProfileResponse)
async def get_tutor_persona(
    service: TutorPersonaServiceDependency,
) -> TutorPersonaProfileResponse:
    try:
        return _public(service.get_profile())
    except Exception as exc:  # Store errors must not result in a partial default response.
        _raise_store_error(exc)


@router.put("", response_model=TutorPersonaProfileResponse)
async def replace_tutor_persona(
    payload: ReplaceTutorPersonaRequest,
    service: TutorPersonaServiceDependency,
) -> TutorPersonaProfileResponse:
    try:
        return _public(
            service.replace_profile(
                payload.settings,
                expected_version=payload.expected_version,
                idempotency_key=payload.idempotency_key,
            )
        )
    except Exception as exc:
        _raise_store_error(exc)


@router.post("/reset", response_model=TutorPersonaProfileResponse)
async def reset_tutor_persona(
    payload: ResetTutorPersonaRequest,
    service: TutorPersonaServiceDependency,
) -> TutorPersonaProfileResponse:
    try:
        return _public(
            service.reset_profile(
                expected_version=payload.expected_version,
                idempotency_key=payload.idempotency_key,
            )
        )
    except Exception as exc:
        _raise_store_error(exc)


@router.post("/preview", response_model=TutorPersonaContract)
async def preview_tutor_persona(
    payload: PreviewTutorPersonaRequest,
    service: TutorPersonaServiceDependency,
) -> TutorPersonaContract:
    try:
        current = service.get_profile()
        candidate = (
            current.model_copy(update=payload.settings.model_dump(mode="python"))
            if payload.settings is not None
            else current
        )
        return service.preview(candidate)
    except Exception as exc:
        _raise_store_error(exc)


@router.get("/reminder-eligibility", response_model=ReminderEligibility)
async def get_reminder_eligibility(
    service: TutorPersonaServiceDependency,
) -> ReminderEligibility:
    """Expose only the current owner's delivery-free reminder decision."""
    try:
        return service.reminder_eligibility()
    except Exception as exc:
        _raise_store_error(exc)


@router.get("/reminders", response_model=list[TutorReminderResponse])
async def list_tutor_reminders(
    outbox: TutorReminderOutboxDependency,
    reminder_status: Annotated[
        ReminderDeliveryStatus | None,
        Query(alias="status"),
    ] = None,
) -> list[TutorReminderResponse]:
    try:
        return [_reminder_public(item) for item in outbox.list(status=reminder_status)]
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tutor reminder data is unavailable",
        ) from exc


@router.post("/reminders/{reminder_id}/read", response_model=TutorReminderResponse)
async def acknowledge_tutor_reminder(
    reminder_id: str,
    outbox: TutorReminderOutboxDependency,
) -> TutorReminderResponse:
    try:
        return _reminder_public(outbox.acknowledge(reminder_id))
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reminder not found"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/reminders/{reminder_id}", response_model=TutorReminderResponse)
async def cancel_tutor_reminder(
    reminder_id: str,
    outbox: TutorReminderOutboxDependency,
) -> TutorReminderResponse:
    try:
        return _reminder_public(outbox.cancel(reminder_id))
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reminder not found"
        ) from exc


__all__ = [
    "TutorPersonaProfileResponse",
    "TutorReminderResponse",
    "default_tutor_reminder_outbox_factory",
    "default_tutor_persona_store_factory",
    "get_tutor_persona_service",
    "router",
    "tutor_persona_store_factory",
    "tutor_reminder_outbox_factory",
]
