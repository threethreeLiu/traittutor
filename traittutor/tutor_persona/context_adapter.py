"""Typed adapter that keeps Persona expression separate from teaching inputs."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .compiler import TutorPersonaContract, compile_persona
from .models import TutorPersonaProfile


class TutorPersonaContext(BaseModel):
    """A bounded context attachment, not a system prompt or learning state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["tutor_persona"] = "tutor_persona"
    profile_ref: str
    contract_hash: str
    contract: TutorPersonaContract


class TutorPersonaContextAdapter:
    """Create an auditable style attachment for a downstream composition root."""

    @staticmethod
    def adapt(profile: TutorPersonaProfile) -> TutorPersonaContext:
        contract = compile_persona(profile)
        canonical = json.dumps(
            contract.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return TutorPersonaContext(
            profile_ref=f"{profile.persona_id}:v{profile.version}",
            contract_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            contract=contract,
        )


__all__ = ["TutorPersonaContext", "TutorPersonaContextAdapter"]
