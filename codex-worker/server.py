"""Authenticated HTTP wrapper around ``codex exec`` for the Feishu bridge."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


WORKER_TOKEN = os.getenv("CODEX_WORKER_TOKEN", "").strip()
DEFAULT_WORKSPACE = Path(os.getenv("CODEX_WORKSPACE", "/workspace")).resolve()
MAX_CONCURRENT_RUNS = max(1, int(os.getenv("CODEX_MAX_CONCURRENT_RUNS", "1")))
RUN_TIMEOUT_SECONDS = max(60, int(os.getenv("CODEX_RUN_TIMEOUT_SECONDS", "1800")))

app = FastAPI(title="Codex Worker API", version="1.0.0")
_run_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)


class RunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=200_000)
    session_id: str | None = None
    workspace: str | None = None
    ephemeral: bool = False


class RunResponse(BaseModel):
    session_id: str | None
    response: str
    resumed: bool


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
    workspace = _resolve_workspace(req.workspace)
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
        detail = (stderr or stdout or "Codex execution failed")[-4000:]
        raise HTTPException(status_code=502, detail=detail)

    session_id, response = _extract_result(stdout)
    if not session_id and req.session_id:
        session_id = req.session_id
    if not response:
        raise HTTPException(status_code=502, detail="Codex returned no final message")
    return RunResponse(session_id=session_id, response=response, resumed=resumed)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "token_configured": bool(WORKER_TOKEN),
        "workspace": str(DEFAULT_WORKSPACE),
        "max_concurrent_runs": MAX_CONCURRENT_RUNS,
    }


@app.post("/v1/runs", response_model=RunResponse, dependencies=[Depends(require_auth)])
async def run_codex(req: RunRequest) -> RunResponse:
    async with _run_slots:
        return await _execute(req)
