"""Authenticated HTTP wrapper around ``dsh --profile headless`` for automation.

This is the DSH sibling of ``codex-worker/server.py`` and deliberately keeps the
same HTTP contract, so a caller can target either worker:

* ``GET  /health``                        — unauthenticated liveness + capacity
* ``POST /v1/runs``                       — run one task, return the final answer
* ``GET  /v1/artifacts/{path}``           — download a file a run produced

Executor difference worth knowing: DSH's ``headless`` profile answers exactly
one task per process and exposes no resume argument, so every request is a
fresh one-shot run. ``session_id`` is therefore always ``null`` and ``resumed``
is always ``false``; callers that need multi-turn state must keep it in the
workspace (for example a ``workflow-state.json``) rather than in a session.

DSH streams provider reasoning to stderr and the final assistant message to
stdout, and exits 0 only when the task completed. The runner inherits that
contract: stdout becomes ``response``, stderr is retained as run-log evidence.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

WORKER_TOKEN = os.getenv("DSH_WORKER_TOKEN", "").strip()
DEFAULT_WORKSPACE = Path(os.getenv("DSH_WORKSPACE", "/workspace")).resolve()
DSH_HOME = Path(os.getenv("DSH_HOME", "/root/.dsh")).resolve()
DSH_PROFILE = os.getenv("DSH_PROFILE", "headless").strip() or "headless"
DSH_BIN = os.getenv("DSH_BIN", "dsh").strip() or "dsh"
# The directory the harness reads skills from. The image installs the Codex
# skill set here and the entrypoint points skill-filesystem at it, so both
# executors run the same tree and the paths inside the skills stay valid.
SKILL_ROOT = Path(os.getenv("DSH_SKILL_ROOT", "/root/.codex/skills"))
EXTERNAL_SKILL_DIRS = os.getenv("DSH_EXTERNAL_SKILL_DIRS", "/root/.codex/skills").split(":")
PERMISSION_MODE = os.getenv("DSH_PERMISSION_MODE", "danger-full-access").strip()
MAX_CONCURRENT_RUNS = max(1, int(os.getenv("DSH_MAX_CONCURRENT_RUNS", "2")))
RUN_TIMEOUT_SECONDS = max(60, int(os.getenv("DSH_RUN_TIMEOUT_SECONDS", "1800")))
# Linux caps a single argv entry at MAX_ARG_STRLEN (128 KiB). The task travels as
# one positional argument, so reject anything that cannot survive that limit.
MAX_PROMPT_BYTES = max(1024, int(os.getenv("DSH_MAX_PROMPT_BYTES", "120000")))
ARTIFACT_DIR_NAME = os.getenv("DSH_ARTIFACT_DIR_NAME", "dsh-artifacts").strip() or "dsh-artifacts"

app = FastAPI(title="DSH Worker API", version="1.0.0")
_run_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
_active_runs = 0
logger = logging.getLogger("dsh-worker")


class RunRequest(BaseModel):
    prompt: str = Field(min_length=1)
    workspace: str | None = None
    artifact_dir: str | None = Field(default=None, max_length=500)
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,160}$")


class ArtifactInfo(BaseModel):
    path: str
    name: str
    mime_type: str
    size: int


class RunResponse(BaseModel):
    session_id: str | None = None
    response: str
    resumed: bool = False
    artifacts: list[ArtifactInfo] = Field(default_factory=list)


def require_auth(authorization: str | None = Header(default=None)) -> None:
    if not WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="DSH_WORKER_TOKEN is not configured")
    prefix = "Bearer "
    supplied = authorization[len(prefix) :] if authorization and authorization.startswith(prefix) else ""
    if not hmac.compare_digest(supplied, WORKER_TOKEN):
        raise HTTPException(status_code=401, detail="invalid bearer token")


_DSH_VERSION: str | None = None


def _dsh_version() -> str:
    """CLI version for the health payload, resolved once and then cached.

    Coolify polls /health every few seconds, and `dsh --version` spawns a whole
    Node process. Doing that per request made health latency 0.4-1.4s and would
    let a busy box trip the 5s healthcheck timeout. The version cannot change
    while a container is alive, so one lookup is enough. Never fatal.
    """
    global _DSH_VERSION
    if _DSH_VERSION is not None:
        return _DSH_VERSION

    import subprocess

    try:
        finished = subprocess.run(
            [DSH_BIN, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
        resolved = (finished.stdout or finished.stderr or "").strip()
    except (OSError, subprocess.SubprocessError):
        resolved = ""
    # Only cache a real answer, so a transient failure can still recover.
    if resolved:
        _DSH_VERSION = resolved
        return resolved
    return "unavailable"


def _resolve_workspace(value: str | None) -> Path:
    workspace = Path(value or DEFAULT_WORKSPACE).resolve()
    if workspace != DEFAULT_WORKSPACE and DEFAULT_WORKSPACE not in workspace.parents:
        raise HTTPException(status_code=400, detail="workspace must be inside DSH_WORKSPACE")
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _resolve_artifact_root(workspace: Path, value: str | None) -> Path:
    """Resolve a run-owned artifact directory below the shared artifacts root.

    A caller that omits ``artifact_dir`` keeps the legacy shared-directory
    behaviour; a caller that supplies one gets an exclusive directory, so
    concurrent runs can never claim one another's deliverables.
    """
    shared_root = (workspace / ARTIFACT_DIR_NAME).resolve()
    if not value:
        artifact_root = shared_root
    else:
        candidate = Path(value)
        artifact_root = (
            candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
        )
        if artifact_root == shared_root or shared_root not in artifact_root.parents:
            raise HTTPException(
                status_code=400,
                detail=f"artifact_dir must be a run-owned directory inside {ARTIFACT_DIR_NAME}",
            )
    artifact_root.mkdir(parents=True, exist_ok=True)
    return artifact_root


def _write_run_logs(
    workspace: Path, run_id: str, *, stdout: str, stderr: str, metadata: dict[str, Any]
) -> str:
    root = (workspace / "dsh-run-logs" / run_id).resolve()
    allowed = (workspace / "dsh-run-logs").resolve()
    if allowed not in root.parents:
        raise ValueError("invalid run log path")
    root.mkdir(parents=True, exist_ok=True)
    (root / "stdout.txt").write_text(stdout, encoding="utf-8")
    (root / "stderr.log").write_text(stderr, encoding="utf-8")
    (root / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root.relative_to(workspace).as_posix()


def _failure_message(stderr: str, stdout: str) -> str:
    """Pick the most informative failure text from a failed DSH invocation.

    DSH writes ``dsh: <code>: <message>`` to stderr for a provider or boot
    error, and provider reasoning also lands there, so prefer the last line
    that carries the ``dsh:`` marker before falling back to the raw tail.
    """
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("dsh:"):
            return line
    tail = (stderr or stdout or "DSH execution failed").strip()
    return tail[-4000:]


def _is_retryable_failure(message: str) -> bool:
    """Classify transport-level failures the caller may safely re-issue.

    Only stream/connection drops qualify. A completed-but-unsuccessful task is
    never retryable, because DSH may already have written to the outside world.
    """
    normalized = message.lower()
    return any(
        marker in normalized
        for marker in (
            "stream disconnected before completion",
            "stream closed before response.completed",
            "connection reset by peer",
            "connection closed",
            "transport",
        )
    )


async def _execute(req: RunRequest) -> RunResponse:
    global _active_runs

    started_at = time.monotonic()
    workspace = _resolve_workspace(req.workspace)
    run_id = req.run_id or uuid.uuid4().hex
    artifact_root = _resolve_artifact_root(workspace, req.artifact_dir)

    encoded_prompt = req.prompt.encode("utf-8")
    if len(encoded_prompt) > MAX_PROMPT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"prompt is {len(encoded_prompt)} bytes; the task travels as one argv "
                f"entry and must stay under {MAX_PROMPT_BYTES}"
            ),
        )

    before = {
        path.resolve(): (path.stat().st_mtime_ns, path.stat().st_size)
        for path in artifact_root.rglob("*")
        if path.is_file()
    }

    run_prompt = req.prompt
    if req.artifact_dir:
        run_prompt += (
            "\n\nSYSTEM ARTIFACT ISOLATION REQUIREMENT: This run has an exclusive output "
            f"directory: {artifact_root}. Save or copy every file intended for delivery "
            "into that exact directory, including files produced by scripts with older "
            "defaults. Do not place deliverable files directly in the shared "
            f"{workspace / ARTIFACT_DIR_NAME} root. In the final response, reference the "
            "files from the exclusive directory."
        )

    command = [DSH_BIN, "--profile", DSH_PROFILE, run_prompt]
    logger.info("starting DSH run run_id=%s workspace=%s profile=%s", run_id, workspace, DSH_PROFILE)

    process_env = os.environ.copy()
    process_env["DSH_HOME"] = str(DSH_HOME)
    process_env["DSH_PERMISSION_MODE"] = PERMISSION_MODE
    process_env["DSH_ARTIFACT_DIR"] = str(artifact_root)
    process_env["DSH_RUN_ARTIFACT_DIR"] = str(artifact_root)

    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(workspace),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=process_env,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            process.communicate(), timeout=RUN_TIMEOUT_SECONDS
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        _write_run_logs(
            workspace,
            run_id,
            stdout="",
            stderr="",
            metadata={
                "run_id": run_id,
                "returncode": None,
                "retryable": False,
                "error": "timeout",
                "timeout_seconds": RUN_TIMEOUT_SECONDS,
                "elapsed_seconds": round(time.monotonic() - started_at, 3),
            },
        )
        raise HTTPException(status_code=504, detail="DSH execution timed out") from None

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    response = stdout.strip()

    if process.returncode != 0:
        message = _failure_message(stderr, stdout)
        retryable = _is_retryable_failure(message)
        log_dir = _write_run_logs(
            workspace,
            run_id,
            stdout=stdout,
            stderr=stderr,
            metadata={
                "run_id": run_id,
                "returncode": process.returncode,
                "retryable": retryable,
                "error": message,
                "elapsed_seconds": round(time.monotonic() - started_at, 3),
            },
        )
        logger.error(
            "DSH run failed run_id=%s returncode=%s retryable=%s log_dir=%s error=%s",
            run_id,
            process.returncode,
            retryable,
            log_dir,
            message[:500],
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": message,
                "session_id": None,
                "retryable": retryable,
                "run_id": run_id,
                "log_dir": log_dir,
                "returncode": process.returncode,
            },
        )

    _write_run_logs(
        workspace,
        run_id,
        stdout=stdout,
        stderr=stderr,
        metadata={
            "run_id": run_id,
            "returncode": process.returncode,
            "retryable": False,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
        },
    )
    if not response:
        raise HTTPException(status_code=502, detail="DSH returned no final message")

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
        "completed DSH run run_id=%s artifacts=%s elapsed_seconds=%.1f",
        run_id,
        len(artifacts),
        time.monotonic() - started_at,
    )
    return RunResponse(response=response, artifacts=artifacts)


def _skill_state() -> dict[str, Any]:
    """Report the skill tree and which generated config files are in place.

    Presence and mode only - never a value or a byte of content.
    """

    def describe(path: Path) -> str:
        try:
            return oct(path.stat().st_mode & 0o777)[2:]
        except OSError:
            return "missing"

    skills = 0
    try:
        skills = sum(1 for entry in SKILL_ROOT.iterdir() if (entry / "SKILL.md").is_file())
    except OSError:
        pass
    return {
        "skill_root": str(SKILL_ROOT),
        "skills_with_manifest": skills,
        "external_skill_dirs": EXTERNAL_SKILL_DIRS,
        "generated_config": {
            "wearhongxiu-wp/config.env": describe(SKILL_ROOT / "wearhongxiu-wp" / "config.env"),
            "zoho-api/.env": describe(Path.home() / ".zoho-api" / ".env"),
            "meta-business/credentials.json": describe(
                Path.home() / ".meta-business" / "credentials.json"
            ),
            "codex/config.toml": describe(Path.home() / ".codex" / "config.toml"),
            "google-ads/service-account.json": describe(
                Path.home() / ".codex" / "google-ads" / "service-account.json"
            ),
            # The fallback for skills whose credentials the harness scrubs out of
            # the shell environment; read by the skill_credentials helper.
            "dsh-skill-credentials.env": describe(
                Path(os.getenv("DSH_SKILL_CREDENTIALS", "/root/.dsh-skill-credentials.env"))
            ),
        },
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "engine": "dsh",
        "dsh_version": _dsh_version(),
        "profile": DSH_PROFILE,
        "permission_mode": PERMISSION_MODE,
        "token_configured": bool(WORKER_TOKEN),
        "api_key_configured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
        "dsh_home": str(DSH_HOME),
        "workspace": str(DEFAULT_WORKSPACE),
        "max_concurrent_runs": MAX_CONCURRENT_RUNS,
        "active_runs": _active_runs,
        "run_timeout_seconds": RUN_TIMEOUT_SECONDS,
        "skills": _skill_state(),
    }


@app.post("/v1/runs", response_model=RunResponse, dependencies=[Depends(require_auth)])
async def run_dsh(req: RunRequest) -> RunResponse:
    global _active_runs

    async with _run_slots:
        _active_runs += 1
        try:
            return await _execute(req)
        finally:
            _active_runs -= 1


@app.get("/v1/artifacts/{artifact_path:path}", dependencies=[Depends(require_auth)])
async def download_artifact(artifact_path: str) -> FileResponse:
    artifact_root = (DEFAULT_WORKSPACE / ARTIFACT_DIR_NAME).resolve()
    candidate = (artifact_root / artifact_path).resolve()
    if artifact_root not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(candidate, filename=candidate.name)
