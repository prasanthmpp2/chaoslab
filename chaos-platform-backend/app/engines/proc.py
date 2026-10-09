"""Safe child-process execution: argument arrays only, hard timeout, bounded + redacted output."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.core.security import redact_text


@dataclass
class ProcResult:
    argv: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


def _bounded(data: bytes, limit: int) -> str:
    text = data[:limit].decode(errors="replace")
    if len(data) > limit:
        text += "\n[truncated]"
    return redact_text(text)


async def run_argv(argv: list[str], timeout: float, env: dict[str, str] | None = None, max_bytes: int = 65_536,
                   cwd: str | None = None) -> ProcResult:
    if not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError("argv must be a non-empty list of strings")
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, cwd=cwd,
        start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return ProcResult(argv, proc.returncode, _bounded(out, max_bytes), _bounded(err, max_bytes), False)
    except TimeoutError:
        await terminate(proc)
        return ProcResult(argv, proc.returncode, "", "", True)


async def terminate(proc: asyncio.subprocess.Process, grace: float = 5.0) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=grace)
    except (TimeoutError, ProcessLookupError):
        try:
            proc.kill()
        except ProcessLookupError:
            return
        await proc.wait()
