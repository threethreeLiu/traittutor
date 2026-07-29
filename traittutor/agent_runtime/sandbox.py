"""Per-task Docker sandbox contract. It never executes on the app host."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import asyncio
import shutil
import tempfile


class SandboxUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxJob:
    command: tuple[str, ...]
    input_files: tuple[Path, ...] = ()
    timeout_seconds: int = 60
    image: str = "python:3.12-slim"


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str


class ContainerSandbox:
    async def run(self, job: SandboxJob) -> SandboxResult:
        if shutil.which("docker") is None:
            raise SandboxUnavailableError("Container sandbox is unavailable; host execution is intentionally disabled.")
        with tempfile.TemporaryDirectory(prefix="traittutor-sandbox-") as work_dir:
            work = Path(work_dir)
            for source in job.input_files:
                if source.is_file():
                    target = work / source.name
                    target.write_bytes(source.read_bytes())
            command = [
                "docker", "run", "--rm", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--pids-limit", "128", "--memory", "512m",
                "-v", f"{work}:/workspace:ro", "-w", "/workspace", job.image, *job.command,
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=job.timeout_seconds)
            except TimeoutError as exc:
                raise SandboxUnavailableError("Sandbox task exceeded its time limit.") from exc
            return SandboxResult(process.returncode or 0, stdout.decode(), stderr.decode())
