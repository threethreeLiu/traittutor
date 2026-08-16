"""Immutable BKT artifact writing and atomic activation helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from uuid import uuid4

from .parameters import (
    BKTParameterArtifact,
    load_bkt_parameter_artifact,
    require_production_bkt_artifact,
)

_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def artifact_path(directory: Path, version: str) -> Path:
    normalized = version.strip()
    if _VERSION.fullmatch(normalized) is None:
        raise ValueError("BKT artifact version is not a safe immutable filename")
    return directory.resolve() / f"{normalized}.json"


def write_immutable_artifact(directory: Path, artifact: BKTParameterArtifact) -> Path:
    """Create a version file exactly once and refuse byte-changing overwrites."""
    directory.mkdir(parents=True, exist_ok=True)
    target = artifact_path(directory, artifact.parameters.version)
    payload = (
        json.dumps(artifact.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False)
        + "\n"
    )
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        existing = target.read_text(encoding="utf-8")
        if existing != payload:
            raise FileExistsError(
                f"refusing to overwrite immutable BKT artifact: {target}"
            ) from None
        return target
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return target


def activate_artifact(directory: Path, version: str) -> str | None:
    """Atomically point ``current.json`` at one validated immutable version."""
    directory = directory.resolve()
    target = artifact_path(directory, version)
    artifact = require_production_bkt_artifact(load_bkt_parameter_artifact(target))
    if artifact.parameters.version != version or not artifact.parameters.calibrated:
        raise ValueError("artifact filename, version, and calibrated marker must agree")
    current = directory / "current.json"
    if current.exists() and not current.is_symlink():
        raise ValueError("refusing to replace a non-symlink BKT current.json")
    previous = os.readlink(current) if current.is_symlink() else None
    temporary = directory / f".current-{uuid4().hex}.json"
    try:
        os.symlink(target.name, temporary)
        os.replace(temporary, current)
    finally:
        if temporary.is_symlink():
            temporary.unlink()
    return previous


def restore_activation(directory: Path, previous_target: str | None) -> None:
    """Restore the exact pre-activation symlink, or remove a newly introduced one."""
    directory = directory.resolve()
    current = directory / "current.json"
    if current.exists() and not current.is_symlink():
        raise ValueError("refusing to replace a non-symlink BKT current.json")
    if previous_target is None:
        if current.is_symlink():
            current.unlink()
        return
    restored_path = (directory / previous_target).resolve()
    if restored_path.parent != directory or not restored_path.is_file():
        raise ValueError("previous BKT artifact target is invalid")
    load_bkt_parameter_artifact(restored_path)
    temporary = directory / f".current-restore-{uuid4().hex}.json"
    try:
        os.symlink(previous_target, temporary)
        os.replace(temporary, current)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


__all__ = [
    "activate_artifact",
    "artifact_path",
    "restore_activation",
    "write_immutable_artifact",
]
