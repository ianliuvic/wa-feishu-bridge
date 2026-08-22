"""Authenticated HTTP wrapper around ``codex exec`` for the Feishu bridge."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


WORKER_TOKEN = os.getenv("CODEX_WORKER_TOKEN", "").strip()
DEFAULT_WORKSPACE = Path(os.getenv("CODEX_WORKSPACE", "/workspace")).resolve()
MAX_CONCURRENT_RUNS = max(1, int(os.getenv("CODEX_MAX_CONCURRENT_RUNS", "1")))
RUN_TIMEOUT_SECONDS = max(60, int(os.getenv("CODEX_RUN_TIMEOUT_SECONDS", "1800")))

app = FastAPI(title="Codex Worker API", version="1.0.0")
_run_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
_active_runs = 0
logger = logging.getLogger("codex-worker")


class RunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=200_000)
    session_id: str | None = None
    workspace: str | None = None
    ephemeral: bool = False


class ArtifactInfo(BaseModel):
    path: str
    name: str
    mime_type: str
    size: int


class RunResponse(BaseModel):
    session_id: str | None
    response: str
    resumed: bool
    artifacts: list[ArtifactInfo] = Field(default_factory=list)


def require_auth(authorization: str | None = Header(default=None)) -> None:
    if not WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="CODEX_WORKER_TOKEN is not configured")
    prefix = "Bearer "
    supplied = authorization[len(prefix) :] if authorization and authorization.startswith(prefix) else ""
    if not hmac.compare_digest(supplied, WORKER_TOKEN):
        raise HTTPException(status_code=401, detail="invalid bearer token")


def _resolve_workspace(value: str | None) -> Path:
    workspace = Path(value or DEFAULT_WORKSPACE).resolve()
    allowed_root = DEFAULT_WORKSPACE
    if workspace != allowed_root and allowed_root not in workspace.parents:
        raise HTTPException(status_code=400, detail="workspace must be inside CODEX_WORKSPACE")
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _extract_result(stdout: str) -> tuple[str | None, str]:
    session_id: str | None = None
    messages: list[str] = []
    for raw in stdout.splitlines():
        try:
            event: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type in {"thread.started", "session.started"}:
            session_id = event.get("thread_id") or event.get("session_id") or session_id
        item = event.get("item") or {}
        if event_type == "item.completed" and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                messages.append(text.strip())
        if event_type in {"turn.completed", "response.completed"}:
            session_id = event.get("thread_id") or event.get("session_id") or session_id
    return session_id, (messages[-1] if messages else "")


async def _execute(req: RunRequest) -> RunResponse:
    started_at = time.monotonic()
    workspace = _resolve_workspace(req.workspace)
    artifact_root = workspace / "codex-artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    before = {
        path.resolve(): (path.stat().st_mtime_ns, path.stat().st_size)
        for path in artifact_root.rglob("*")
        if path.is_file()
    }
    base = [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-c",
        'approval_policy="never"',
        "-c",
        "shell_environment_policy.inherit=all",
        "-C",
        str(workspace),
    ]
    if req.ephemeral:
        base.append("--ephemeral")
    resumed = bool(req.session_id)
    if req.session_id:
        command = [*base, "resume", req.session_id, req.prompt]
    else:
        command = [*base, req.prompt]

    logger.info("starting Codex run resumed=%s workspace=%s", resumed, workspace)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            process.communicate(), timeout=RUN_TIMEOUT_SECONDS
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise HTTPException(status_code=504, detail="Codex execution timed out") from None

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if process.returncode != 0:
        logger.error("Codex run failed returncode=%s", process.returncode)
        detail = (stderr or stdout or "Codex execution failed")[-4000:]
        raise HTTPException(status_code=502, detail=detail)

    session_id, response = _extract_result(stdout)
    if not session_id and req.session_id:
        session_id = req.session_id
    if not response:
        raise HTTPException(status_code=502, detail="Codex returned no final message")

    artifacts: list[ArtifactInfo] = []
    changed: list[Path] = []
    for path in artifact_root.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        state = (path.stat().st_mtime_ns, path.stat().st_size)
        if before.get(resolved) != state:
            changed.append(path)
    for path in sorted(changed, key=lambda item: item.stat().st_mtime_ns, reverse=True)[:20]:
        relative = path.resolve().relative_to(workspace).as_posix()
        artifacts.append(
            ArtifactInfo(
                path=relative,
                name=path.name,
                mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                size=path.stat().st_size,
            )
        )
    logger.info(
        "completed Codex run resumed=%s artifacts=%s elapsed_seconds=%.1f",
        resumed,
        len(artifacts),
        time.monotonic() - started_at,
    )
    return RunResponse(
        session_id=session_id,
        response=response,
        resumed=resumed,
        artifacts=artifacts,
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "token_configured": bool(WORKER_TOKEN),
        "workspace": str(DEFAULT_WORKSPACE),
        "max_concurrent_runs": MAX_CONCURRENT_RUNS,
        "active_runs": _active_runs,
    }


@app.post("/v1/runs", response_model=RunResponse, dependencies=[Depends(require_auth)])
async def run_codex(req: RunRequest) -> RunResponse:
    global _active_runs
    async with _run_slots:
        _active_runs += 1
        try:
            return await _execute(req)
        finally:
            _active_runs -= 1


@app.get("/v1/artifacts/{artifact_path:path}", dependencies=[Depends(require_auth)])
async def download_artifact(artifact_path: str) -> FileResponse:
    artifact_root = (DEFAULT_WORKSPACE / "codex-artifacts").resolve()
    path = (DEFAULT_WORKSPACE / artifact_path).resolve()
    if path == artifact_root or artifact_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)
