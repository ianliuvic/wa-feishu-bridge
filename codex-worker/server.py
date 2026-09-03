"""Authenticated HTTP wrapper around ``codex exec`` for the Feishu bridge."""

from __future__ import annotations

import asyncio
import base64
import hmac
import hashlib
import html
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field


WORKER_TOKEN = os.getenv("CODEX_WORKER_TOKEN", "").strip()
DEFAULT_WORKSPACE = Path(os.getenv("CODEX_WORKSPACE", "/workspace")).resolve()
MAX_CONCURRENT_RUNS = max(1, int(os.getenv("CODEX_MAX_CONCURRENT_RUNS", "1")))
RUN_TIMEOUT_SECONDS = max(60, int(os.getenv("CODEX_RUN_TIMEOUT_SECONDS", "1800")))
MAX_INPUT_BYTES = max(1024 * 1024, int(os.getenv("CODEX_MAX_INPUT_BYTES", str(50 * 1024 * 1024))))
INPUT_ROOT = (DEFAULT_WORKSPACE / "codex-inputs").resolve()
LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "").strip()
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "").strip()
LINKEDIN_REDIRECT_URI = os.getenv(
    "LINKEDIN_REDIRECT_URI",
    "https://codex-worker.yiswim.cloud/linkedin/oauth/callback",
).strip()
LINKEDIN_STATE_SECRET = os.getenv("LINKEDIN_STATE_SECRET", "").strip()
LINKEDIN_OAUTH_SCOPES = os.getenv(
    "LINKEDIN_OAUTH_SCOPES", "openid profile w_member_social"
).strip()
LINKEDIN_TOKEN_PATH = Path(
    os.getenv("LINKEDIN_TOKEN_PATH", "/root/.codex/linkedin/oauth.json")
).resolve()
LINKEDIN_STATE_TTL_SECONDS = 10 * 60

app = FastAPI(title="Codex Worker API", version="1.0.0")
_run_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
_active_runs = 0
logger = logging.getLogger("codex-worker")


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _linkedin_ready() -> bool:
    return bool(
        LINKEDIN_CLIENT_ID
        and LINKEDIN_CLIENT_SECRET
        and LINKEDIN_REDIRECT_URI
        and LINKEDIN_STATE_SECRET
    )


def _linkedin_state() -> str:
    payload = json.dumps(
        {"iat": int(time.time()), "nonce": secrets.token_urlsafe(18)},
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = _b64url_encode(payload)
    signature = hmac.new(
        LINKEDIN_STATE_SECRET.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded}.{_b64url_encode(signature)}"


def _validate_linkedin_state(value: str) -> None:
    try:
        encoded, supplied = value.split(".", 1)
        expected = hmac.new(
            LINKEDIN_STATE_SECRET.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_b64url_decode(supplied), expected):
            raise ValueError("signature")
        payload = json.loads(_b64url_decode(encoded))
        issued_at = int(payload["iat"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid OAuth state") from exc
    age = int(time.time()) - issued_at
    if age < -60 or age > LINKEDIN_STATE_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="expired OAuth state")


def _linkedin_json_request(
    url: str,
    *,
    method: str = "GET",
    data: dict[str, str] | None = None,
    access_token: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    headers = {"Accept": "application/json", "User-Agent": "HongxiuCodexWorker/1.0"}
    body = None
    if data is not None:
        body = urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = UrlRequest(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise HTTPException(
            status_code=502, detail=f"LinkedIn API returned HTTP {exc.code}: {detail}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=502, detail="LinkedIn API is unavailable") from exc
    try:
        value = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="LinkedIn returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=502, detail="LinkedIn returned an unexpected response")
    return value, response_headers


def _save_linkedin_token(token: dict[str, Any], profile: dict[str, Any]) -> None:
    subject = str(profile.get("sub") or profile.get("id") or "").strip()
    if not subject:
        raise HTTPException(
            status_code=502,
            detail="LinkedIn did not return a member ID; enable Sign In with LinkedIn using OpenID Connect",
        )
    now = int(time.time())
    expires_in = max(0, int(token.get("expires_in") or 0))
    record = {
        "access_token": str(token.get("access_token") or ""),
        "refresh_token": str(token.get("refresh_token") or ""),
        "expires_at": now + expires_in,
        "refresh_token_expires_in": int(token.get("refresh_token_expires_in") or 0),
        "scope": str(token.get("scope") or LINKEDIN_OAUTH_SCOPES),
        "person_urn": f"urn:li:person:{subject}",
        "name": str(profile.get("name") or "").strip(),
        "authorized_at": now,
    }
    if not record["access_token"]:
        raise HTTPException(status_code=502, detail="LinkedIn returned no access token")
    LINKEDIN_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LINKEDIN_TOKEN_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(LINKEDIN_TOKEN_PATH)


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


@app.post(
    "/v1/linkedin/oauth/url", dependencies=[Depends(require_auth)]
)
async def linkedin_oauth_url() -> dict[str, Any]:
    if not _linkedin_ready():
        raise HTTPException(status_code=503, detail="LinkedIn OAuth is not configured")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": LINKEDIN_CLIENT_ID,
            "redirect_uri": LINKEDIN_REDIRECT_URI,
            "state": _linkedin_state(),
            "scope": LINKEDIN_OAUTH_SCOPES,
        }
    )
    return {
        "authorize_url": f"https://www.linkedin.com/oauth/v2/authorization?{query}",
        "redirect_uri": LINKEDIN_REDIRECT_URI,
        "scope": LINKEDIN_OAUTH_SCOPES,
    }


@app.get("/linkedin/oauth/callback", response_class=HTMLResponse)
async def linkedin_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> HTMLResponse:
    if not _linkedin_ready():
        raise HTTPException(status_code=503, detail="LinkedIn OAuth is not configured")
    if error:
        message = html.escape((error_description or error)[:300])
        return HTMLResponse(
            f"<h1>LinkedIn authorization failed</h1><p>{message}</p>", status_code=400
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing OAuth code or state")
    _validate_linkedin_state(state)
    token, _ = _linkedin_json_request(
        "https://www.linkedin.com/oauth/v2/accessToken",
        method="POST",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": LINKEDIN_CLIENT_ID,
            "client_secret": LINKEDIN_CLIENT_SECRET,
            "redirect_uri": LINKEDIN_REDIRECT_URI,
        },
    )
    profile, _ = _linkedin_json_request(
        "https://api.linkedin.com/v2/userinfo",
        access_token=str(token.get("access_token") or ""),
    )
    _save_linkedin_token(token, profile)
    name = html.escape(str(profile.get("name") or "LinkedIn member"))
    return HTMLResponse(
        "<h1>LinkedIn authorization complete</h1>"
        f"<p>{name} is now connected to the Hongxiu Codex publishing workflow.</p>"
        "<p>You can close this page.</p>"
    )


@app.get("/v1/linkedin/status", dependencies=[Depends(require_auth)])
async def linkedin_status() -> dict[str, Any]:
    result: dict[str, Any] = {
        "configured": _linkedin_ready(),
        "authorized": False,
        "redirect_uri": LINKEDIN_REDIRECT_URI,
    }
    if not LINKEDIN_TOKEN_PATH.is_file():
        return result
    try:
        record = json.loads(LINKEDIN_TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result["token_file_valid"] = False
        return result
    expires_at = int(record.get("expires_at") or 0)
    result.update(
        {
            "authorized": bool(record.get("access_token") and record.get("person_urn")),
            "token_file_valid": True,
            "name": record.get("name") or "",
            "expires_at": expires_at,
            "expired": bool(expires_at and expires_at <= int(time.time())),
            "refresh_available": bool(record.get("refresh_token")),
        }
    )
    return result


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
