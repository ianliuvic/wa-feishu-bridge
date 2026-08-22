"""Authenticated HTTP wrapper around ``codex exec`` for the Feishu bridge."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import mimetypes
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


WORKER_TOKEN = os.getenv("CODEX_WORKER_TOKEN", "").strip()
DEFAULT_WORKSPACE = Path(os.getenv("CODEX_WORKSPACE", "/workspace")).resolve()
MAX_CONCURRENT_RUNS = max(1, int(os.getenv("CODEX_MAX_CONCURRENT_RUNS", "1")))
RUN_TIMEOUT_SECONDS = max(60, int(os.getenv("CODEX_RUN_TIMEOUT_SECONDS", "1800")))
MAX_INPUT_BYTES = max(1024 * 1024, int(os.getenv("CODEX_MAX_INPUT_BYTES", str(50 * 1024 * 1024))))
INPUT_ROOT = (DEFAULT_WORKSPACE / "codex-inputs").resolve()

app = FastAPI(title="Codex Worker API", version="1.0.0")
_run_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
_active_runs = 0
logger = logging.getLogger("codex-worker")


class RunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=200_000)
    session_id: str | None = None
    workspace: str | None = None
    ephemeral: bool = False
    input_files: list[str] = Field(default_factory=list, max_length=20)


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


def _resolve_input_file(value: str) -> Path:
    path = Path(value).resolve()
    if INPUT_ROOT not in path.parents or not path.is_file():
        raise HTTPException(status_code=400, detail="input file is outside CODEX input storage")
    return path


def _cleanup_stale_inputs(max_age_seconds: int = 24 * 60 * 60) -> None:
    if not INPUT_ROOT.exists():
        return
    cutoff = time.time() - max_age_seconds
    for directory in INPUT_ROOT.iterdir():
        try:
            if directory.is_dir() and directory.stat().st_mtime < cutoff:
                shutil.rmtree(directory)
        except OSError:
            logger.warning("failed to clean stale input directory %s", directory)


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
    input_paths = [_resolve_input_file(value) for value in req.input_files]
    image_args: list[str] = []
    for path in input_paths:
        mime_type = mimetypes.guess_type(path.name)[0] or ""
        if mime_type.startswith("image/"):
            image_args.extend(["--image", str(path)])
    resumed = bool(req.session_id)
    if req.session_id:
        command = [*base, "resume", *image_args, req.session_id, req.prompt]
    else:
        command = [*base, *image_args, "--", req.prompt] if image_args else [*base, req.prompt]

    logger.info(
        "starting Codex run resumed=%s workspace=%s inputs=%s images=%s",
        resumed,
        workspace,
        len(input_paths),
        len(image_args) // 2,
    )
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


@app.put("/v1/inputs/{upload_id}/{file_name}", dependencies=[Depends(require_auth)])
async def upload_input(upload_id: str, file_name: str, request: Request) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", upload_id):
        raise HTTPException(status_code=400, detail="invalid upload id")
    safe_name = Path(file_name).name
    if not safe_name or safe_name != file_name or len(safe_name) > 180:
        raise HTTPException(status_code=400, detail="invalid file name")
    data = await request.body()
    if not data or len(data) > MAX_INPUT_BYTES:
        raise HTTPException(status_code=413, detail="input file is empty or too large")
    _cleanup_stale_inputs()
    directory = INPUT_ROOT / upload_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / safe_name
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(data)
    temporary.replace(path)
    mime_type = request.headers.get("content-type") or mimetypes.guess_type(safe_name)[0]
    logger.info("uploaded Codex input name=%s size=%s", safe_name, len(data))
    return {
        "path": str(path),
        "name": safe_name,
        "mime_type": mime_type or "application/octet-stream",
        "size": len(data),
    }


@app.delete("/v1/inputs/{upload_id}", dependencies=[Depends(require_auth)])
async def delete_inputs(upload_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", upload_id):
        raise HTTPException(status_code=400, detail="invalid upload id")
    directory = (INPUT_ROOT / upload_id).resolve()
    if INPUT_ROOT not in directory.parents:
        raise HTTPException(status_code=400, detail="invalid upload id")
    existed = directory.exists()
    if existed:
        shutil.rmtree(directory)
    return {"deleted": existed, "upload_id": upload_id}
