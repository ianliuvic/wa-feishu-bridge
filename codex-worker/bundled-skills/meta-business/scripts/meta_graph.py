#!/usr/bin/env python3
"""Shared Meta Graph API transport and validation helpers."""

from __future__ import annotations

import json
import http.client
import os
import re
import secrets
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_VERSION = "v26.0"
DEFAULT_CONFIG_FILE = Path.home() / ".meta-business" / "credentials.json"
ID_PATTERN = re.compile(r"^[A-Za-z0-9_:-]+$")


class MetaGraphError(RuntimeError):
    """A safe Graph API error that never contains the access token."""


def _config_file() -> Path:
    override = os.environ.get("META_BUSINESS_CONFIG_FILE", "").strip()
    return Path(override).expanduser() if override else DEFAULT_CONFIG_FILE


def _credential_config() -> dict[str, Any]:
    path = _config_file()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise MetaGraphError(f"could not read a valid Meta credential file at {path}") from exc
    if not isinstance(value, dict):
        raise MetaGraphError(f"Meta credential file at {path} must contain a JSON object")
    return value


def _configured_value(env_name: str, config_key: str, default: str = "") -> str:
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return env_value
    file_value = _credential_config().get(config_key, default)
    if file_value is None:
        return default
    if not isinstance(file_value, str):
        raise MetaGraphError(f"{config_key} in the Meta credential file must be a string")
    return file_value.strip()


def _api_version() -> str:
    value = _configured_value("META_GRAPH_API_VERSION", "graph_api_version", DEFAULT_VERSION)
    if not re.fullmatch(r"v\d+\.\d+", value):
        raise MetaGraphError("META_GRAPH_API_VERSION must look like v26.0")
    return value


def _access_token() -> str:
    value = _configured_value("META_BUSINESS_ACCESS_TOKEN", "access_token")
    if not value:
        raise MetaGraphError(
            "Meta access token is not configured; add access_token to the credential file "
            "or set META_BUSINESS_ACCESS_TOKEN"
        )
    return value


def require_object_id(value: str | None, env_name: str, label: str) -> str:
    config_keys = {
        "META_BUSINESS_PAGE_ID": "page_id",
        "META_BUSINESS_IG_USER_ID": "ig_user_id",
        "META_BUSINESS_AD_ACCOUNT_ID": "ad_account_id",
        "META_BUSINESS_BUSINESS_ID": "business_id",
        "META_BUSINESS_CATALOG_ID": "catalog_id",
        "META_THREADS_USER_ID": "threads_user_id",
    }
    resolved = (value or _configured_value(env_name, config_keys.get(env_name, env_name.lower()))).strip()
    if not resolved:
        raise MetaGraphError(
            f"{label} is required; configure it in the credential file, set {env_name}, "
            "or pass its CLI option"
        )
    if not ID_PATTERN.fullmatch(resolved):
        raise MetaGraphError(f"invalid {label}")
    return resolved


def require_public_https_url(value: str, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise MetaGraphError(f"{label} must be a public HTTPS URL without embedded credentials")
    return value


def require_positive_int(value: int, label: str, maximum: int = 100) -> int:
    if not 1 <= value <= maximum:
        raise MetaGraphError(f"{label} must be between 1 and {maximum}")
    return value


def normalize_post_text(value: str | None) -> str | None:
    """Normalize marketing-copy line breaks before sending them to Meta."""
    if value is None:
        return None
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")


def require_publish_confirmation(confirm: bool, dry_run: bool) -> None:
    if confirm and dry_run:
        raise MetaGraphError("use either --confirm-publish or --dry-run, not both")
    if not confirm and not dry_run:
        raise MetaGraphError("live publishing requires --confirm-publish; use --dry-run to preview")


def require_write_confirmation(confirm: bool, dry_run: bool, action: str = "write") -> None:
    if confirm and dry_run:
        raise MetaGraphError("use either --confirm-write or --dry-run, not both")
    if not confirm and not dry_run:
        raise MetaGraphError(f"live {action} requires --confirm-write; use --dry-run to preview")


def load_json_value(path: str) -> Any:
    source = Path(path).expanduser()
    try:
        with source.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise MetaGraphError(f"could not read valid JSON from {source}") from exc
    if _contains_secret_key(value):
        raise MetaGraphError("payload files must not contain access tokens or secrets")
    return value


def load_json_object(path: str) -> dict[str, Any]:
    value = load_json_value(path)
    if not isinstance(value, dict):
        raise MetaGraphError("payload file must contain a JSON object")
    return value


def require_allowed_fields(payload: dict[str, Any], allowed: set[str], label: str) -> dict[str, Any]:
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise MetaGraphError(f"unsupported {label} fields: {', '.join(unexpected)}")
    return payload


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower()
            if "access_token" in lowered or lowered in {"token", "secret", "password"}:
                return True
            if _contains_secret_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


def compact_params(params: dict[str, Any] | None) -> dict[str, Any]:
    return {key: value for key, value in (params or {}).items() if value is not None}


def encode_params(params: dict[str, Any] | None) -> dict[str, Any]:
    encoded: dict[str, Any] = {}
    for key, value in compact_params(params).items():
        if isinstance(value, (dict, list)):
            encoded[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        elif isinstance(value, bool):
            encoded[key] = "true" if value else "false"
        else:
            encoded[key] = value
    return encoded


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def dry_run_result(action: str, target_id: str, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "dry_run": True,
        "action": action,
        "target_id": target_id,
        "endpoint": endpoint,
        "payload": payload,
    }


class GraphClient:
    def __init__(self, token: str | None = None, version: str | None = None, timeout: int = 60):
        self._token = token or _access_token()
        self.version = version or _api_version()
        self.timeout = timeout
        self.base_url = f"https://graph.facebook.com/{self.version}"

    def request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        clean_path = path.strip().lstrip("/")
        if not clean_path or ".." in clean_path:
            raise MetaGraphError("invalid Graph API path")
        clean_params = encode_params(params)
        method = method.upper()
        url = f"{self.base_url}/{clean_path}"
        body = None
        if method == "GET":
            if clean_params:
                url = f"{url}?{urlencode(clean_params, doseq=True)}"
        elif method in {"POST", "DELETE"}:
            body = urlencode(clean_params, doseq=True).encode("utf-8")
        else:
            raise MetaGraphError(f"unsupported HTTP method: {method}")
        request = Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "meta-business-skill/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = {"message": raw[-1000:]}
            raise MetaGraphError(_safe_error(exc.code, detail)) from exc
        except (URLError, TimeoutError) as exc:
            raise MetaGraphError(f"Meta Graph request failed: {exc}") from exc
        if isinstance(result, dict) and result.get("error"):
            raise MetaGraphError(_safe_error(None, result))
        if not isinstance(result, dict):
            raise MetaGraphError("Meta Graph returned an unexpected response")
        return result

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", path, params)

    def post(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("POST", path, params)

    def delete(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("DELETE", path, params)

    def upload_file(
        self,
        path: str,
        file_path: str | Path,
        file_field: str,
        mime_type: str,
        params: dict[str, Any] | None = None,
        host: str = "graph.facebook.com",
    ) -> dict[str, Any]:
        """Stream one multipart file to a Graph API endpoint without exposing the token."""
        clean_path = path.strip().lstrip("/")
        if not clean_path or ".." in clean_path or host not in {"graph.facebook.com", "graph-video.facebook.com"}:
            raise MetaGraphError("invalid Graph upload endpoint")
        source = Path(file_path).expanduser().resolve()
        if not source.is_file():
            raise MetaGraphError(f"upload source is not a file: {source}")
        if not file_field or any(char in file_field for char in '\r\n"'):
            raise MetaGraphError("invalid multipart file field")
        boundary = f"----meta-business-{secrets.token_hex(16)}"
        chunks: list[bytes] = []
        for key, value in encode_params(params).items():
            if any(char in str(key) for char in '\r\n"'):
                raise MetaGraphError("invalid multipart parameter name")
            chunks.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8")
            )
        filename = source.name.replace("\\", "_").replace('"', "_").replace("\r", "_").replace("\n", "_")
        file_header = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        trailer = f"\r\n--{boundary}--\r\n".encode("ascii")
        content_length = sum(len(chunk) for chunk in chunks) + len(file_header) + source.stat().st_size + len(trailer)
        connection = http.client.HTTPSConnection(host, timeout=self.timeout)
        try:
            connection.putrequest("POST", f"/{self.version}/{clean_path}")
            connection.putheader("Authorization", f"Bearer {self._token}")
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(content_length))
            connection.putheader("User-Agent", "meta-business-skill/1.0")
            connection.endheaders()
            for chunk in chunks:
                connection.send(chunk)
            connection.send(file_header)
            with source.open("rb") as handle:
                while data := handle.read(1024 * 1024):
                    connection.send(data)
            connection.send(trailer)
            response = connection.getresponse()
            status = response.status
            raw = response.read().decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException, TimeoutError) as exc:
            raise MetaGraphError(f"Meta Graph file upload failed: {exc}") from exc
        finally:
            connection.close()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MetaGraphError(f"Meta Graph upload returned HTTP {status} with an unexpected response") from exc
        if status >= 400 or (isinstance(result, dict) and result.get("error")):
            raise MetaGraphError(_safe_error(status, result))
        if not isinstance(result, dict):
            raise MetaGraphError("Meta Graph upload returned an unexpected response")
        return result

    def upload_reel_from_url(self, video_id: str, file_url: str) -> dict[str, Any]:
        """Tell Meta's resumable Page Reels upload host to fetch a public video URL."""
        video_id = require_object_id(video_id, "META_UNUSED", "video ID")
        file_url = require_public_https_url(file_url, "video URL")
        request = Request(
            f"https://rupload.facebook.com/video-upload/{self.version}/{video_id}",
            data=b"",
            method="POST",
            headers={
                "Authorization": f"OAuth {self._token}",
                "file_url": file_url,
                "User-Agent": "meta-business-skill/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = {"message": raw[-1000:]}
            raise MetaGraphError(_safe_error(exc.code, detail)) from exc
        except (URLError, TimeoutError) as exc:
            raise MetaGraphError(f"Meta Page Reel upload failed: {exc}") from exc
        if not isinstance(result, dict) or result.get("error"):
            raise MetaGraphError(_safe_error(None, result))
        return result

    def page_client(self, page_id: str) -> "GraphClient":
        """Derive an in-memory Page access token without printing or persisting it."""
        page_id = require_object_id(page_id, "META_UNUSED", "Page ID")
        value = self.get(page_id, {"fields": "access_token"}).get("access_token")
        if not isinstance(value, str) or not value.strip():
            raise MetaGraphError("could not derive a Page access token for the configured Page")
        return GraphClient(token=value.strip(), version=self.version, timeout=self.timeout)

    def get_pages(self, path: str, params: dict[str, Any] | None = None, max_pages: int = 5) -> list[dict[str, Any]]:
        max_pages = require_positive_int(max_pages, "max_pages", 20)
        response = self.get(path, params)
        items: list[dict[str, Any]] = []
        for _ in range(max_pages):
            data = response.get("data") or []
            if not isinstance(data, list):
                raise MetaGraphError("expected a paginated data array")
            items.extend(item for item in data if isinstance(item, dict))
            next_url = ((response.get("paging") or {}).get("next"))
            if not next_url:
                break
            response = self._get_absolute(next_url)
        return items

    def _get_absolute(self, url: str) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "graph.facebook.com":
            raise MetaGraphError("refused an unexpected pagination URL")
        request = Request(url, method="GET", headers={
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "meta-business-skill/1.0",
        })
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = {"message": raw[-1000:]}
            raise MetaGraphError(_safe_error(exc.code, detail)) from exc
        except (URLError, TimeoutError) as exc:
            raise MetaGraphError(f"Meta Graph pagination failed: {exc}") from exc
        if not isinstance(result, dict):
            raise MetaGraphError("Meta Graph returned an unexpected pagination response")
        return result


def _safe_error(http_status: int | None, payload: Any) -> str:
    error = payload.get("error", payload) if isinstance(payload, dict) else {}
    message = str(error.get("message", "Meta Graph API error"))
    code = error.get("code")
    subcode = error.get("error_subcode")
    trace = error.get("fbtrace_id")
    parts: Iterable[str] = (
        f"HTTP {http_status}" if http_status else "",
        f"code {code}" if code is not None else "",
        f"subcode {subcode}" if subcode is not None else "",
        message,
        f"trace {trace}" if trace else "",
    )
    return "Meta Graph error: " + "; ".join(part for part in parts if part)
