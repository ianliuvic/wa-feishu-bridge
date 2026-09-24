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
from fastapi.responses import FileResponse, JSONResponse
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
# A run streams provider reasoning to stderr, so a long run can produce a lot of
# output. Only the tail is kept: it is what tells you where a killed run stopped.
STREAM_TAIL_LIMIT = max(64 * 1024, int(os.getenv("DSH_STREAM_TAIL_BYTES", str(8 * 1024 * 1024))))


class _TailBuffer:
    """Collect stream output while keeping only the most recent bytes.

    ``asyncio.wait_for(process.communicate())`` loses everything the process had
    already written when the timeout fires, which is why a timed-out run used to
    leave empty logs and no way to see how far it got.
    """

    def __init__(self, limit: int = STREAM_TAIL_LIMIT) -> None:
        self._chunks: list[bytes] = []
        self._size = 0
        self._limit = limit
        self._truncated = False

    def write(self, chunk: bytes) -> None:
        self._chunks.append(chunk)
        self._size += len(chunk)
        while self._size > self._limit and len(self._chunks) > 1:
            self._size -= len(self._chunks.pop(0))
            self._truncated = True

    @property
    def truncated(self) -> bool:
        return self._truncated

    def text(self) -> str:
        body = b"".join(self._chunks).decode("utf-8", errors="replace")
        if self._truncated:
            return "…[earlier output dropped to bound memory]…\n" + body
        return body

    def tail(self, chars: int = 2000) -> str:
        return self.text()[-chars:]

app = FastAPI(title="DSH Worker API", version="1.1.0")
_run_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
_active_runs = 0
logger = logging.getLogger("dsh-worker")

# Completed and in-flight async runs, keyed by run id. Bounded so a long-lived
# worker cannot grow without limit; the oldest finished entry is dropped first.
MAX_TRACKED_RUNS = 200
_runs: dict[str, dict[str, Any]] = {}
_runs_lock = asyncio.Lock()


async def _remember(run_id: str, record: dict[str, Any]) -> None:
    async with _runs_lock:
        _runs[run_id] = record
        if len(_runs) > MAX_TRACKED_RUNS:
            for key in [k for k, v in _runs.items() if v.get("status") != "running"][
                : len(_runs) - MAX_TRACKED_RUNS
            ]:
                _runs.pop(key, None)


class RunRequest(BaseModel):
    prompt: str = Field(min_length=1)
    workspace: str | None = None
    artifact_dir: str | None = Field(default=None, max_length=500)
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,160}$")
    # Return a run id immediately instead of holding the HTTP request open for
    # the whole run. A long task can outlive any proxy's idle timeout - observed
    # at ~12 minutes, where the run finished fine but the caller never got the
    # response - so callers that may wait should poll GET /v1/runs/{run_id}.
    async_mode: bool = False


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


async def _execute(req: RunRequest, run_id: str | None = None) -> RunResponse:
    workspace = _resolve_workspace(req.workspace)
    run_id = run_id or req.run_id or uuid.uuid4().hex
    started_at = time.monotonic()
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

    stdout_buffer = _TailBuffer()
    stderr_buffer = _TailBuffer()

    async def _drain(stream: asyncio.StreamReader | None, buffer: _TailBuffer) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            buffer.write(chunk)

    drains = [
        asyncio.create_task(_drain(process.stdout, stdout_buffer)),
        asyncio.create_task(_drain(process.stderr, stderr_buffer)),
    ]
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=RUN_TIMEOUT_SECONDS)
    except TimeoutError:
        timed_out = True
        process.kill()
    await process.wait()
    await asyncio.gather(*drains, return_exceptions=True)

    stdout = stdout_buffer.text()
    stderr = stderr_buffer.text()
    response = stdout.strip()

    if timed_out:
        elapsed = round(time.monotonic() - started_at, 3)
        log_dir = _write_run_logs(
            workspace,
            run_id,
            stdout=stdout,
            stderr=stderr,
            metadata={
                "run_id": run_id,
                "returncode": None,
                "retryable": False,
                "error": "timeout",
                "timeout_seconds": RUN_TIMEOUT_SECONDS,
                "elapsed_seconds": elapsed,
                "stdout_truncated": stdout_buffer.truncated,
                "stderr_truncated": stderr_buffer.truncated,
            },
        )
        logger.error(
            "DSH run timed out run_id=%s elapsed=%.1fs log_dir=%s stdout_tail=%s stderr_tail=%s",
            run_id,
            elapsed,
            log_dir,
            stdout_buffer.tail(600),
            stderr_buffer.tail(600),
        )
        # The tail travels with the error so a timeout can be diagnosed from the
        # run record alone, without shell access to the worker.
        raise HTTPException(
            status_code=504,
            detail={
                "message": f"DSH execution timed out after {RUN_TIMEOUT_SECONDS}s",
                "session_id": None,
                "retryable": False,
                "run_id": run_id,
                "log_dir": log_dir,
                "returncode": None,
                "elapsed_seconds": elapsed,
                "stdout_tail": stdout_buffer.tail(2000),
                "stderr_tail": stderr_buffer.tail(2000),
            },
        )

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
            "linkedin/oauth.json": describe(
                Path(os.getenv("LINKEDIN_TOKEN_PATH", "/root/.codex/linkedin/oauth.json"))
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


async def _run_slot(req: RunRequest, run_id: str) -> RunResponse:
    """Take a concurrency slot and execute, tracking active count."""
    global _active_runs

    async with _run_slots:
        _active_runs += 1
        try:
            return await _execute(req, run_id)
        finally:
            _active_runs -= 1


async def _background_run(req: RunRequest, run_id: str) -> None:
    """Execute a run whose caller is not waiting on the HTTP response."""
    try:
        result = await _run_slot(req, run_id)
        await _remember(run_id, {"status": "done", "result": result.model_dump()})
    except HTTPException as exc:
        await _remember(run_id, {
            "status": "failed",
            "error": exc.detail if isinstance(exc.detail, (str, dict)) else str(exc.detail),
            "returncode": 502 if exc.status_code >= 500 else exc.status_code,
        })
    except Exception as exc:  # noqa: BLE001 - must never escape a detached task
        logger.exception("async run %s failed", run_id)
        await _remember(run_id, {"status": "failed", "error": f"{type(exc).__name__}: {exc}"})


@app.post("/v1/runs", response_model=None, dependencies=[Depends(require_auth)])
async def run_dsh(req: RunRequest):
    """Run one task. With async_mode the call returns a run id immediately."""
    run_id = req.run_id or uuid.uuid4().hex

    if req.async_mode:
        await _remember(run_id, {"status": "running"})
        asyncio.create_task(_background_run(req, run_id))
        return JSONResponse(status_code=202, content={"run_id": run_id, "status": "running"})

    return await _run_slot(req, run_id)


@app.get("/v1/runs/{run_id}", dependencies=[Depends(require_auth)])
async def get_run(run_id: str):
    """Poll a run started with async_mode."""
    async with _runs_lock:
        record = _runs.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown run_id")
    return record


@app.get("/v1/artifacts/{artifact_path:path}", dependencies=[Depends(require_auth)])
async def download_artifact(artifact_path: str) -> FileResponse:
    """Serve a file a run produced.

    Artifact paths are workspace-relative, so each one already starts with the
    artifacts directory name - and a caller passes that path through verbatim.
    Resolving it under the artifacts root again doubled the prefix and 404'd
    every fetch. Both forms are accepted, and serving stays confined to the
    artifacts root.

    Run transcripts live under ``dsh-run-logs/`` instead, and are served too:
    otherwise a killed run can only be inspected with shell access, and the
    in-memory record is lost when the worker restarts.
    """
    relative = artifact_path.lstrip("/")
    prefix = f"{ARTIFACT_DIR_NAME}/"
    if relative.startswith(prefix):
        relative = relative[len(prefix):]
    if relative.startswith("dsh-run-logs/"):
        logs_root = (DEFAULT_WORKSPACE / "dsh-run-logs").resolve()
        candidate = (DEFAULT_WORKSPACE / relative).resolve()
        if logs_root not in candidate.parents or not candidate.is_file():
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(candidate, filename=candidate.name)
    artifact_root = (DEFAULT_WORKSPACE / ARTIFACT_DIR_NAME).resolve()
    candidate = (artifact_root / relative).resolve()
    if artifact_root not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(candidate, filename=candidate.name)
