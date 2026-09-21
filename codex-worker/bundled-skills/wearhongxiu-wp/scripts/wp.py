#!/usr/bin/env python3
"""Site-locked WordPress REST client for wearhongxiu.com."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SKILL_DIR = Path(__file__).resolve().parent.parent
ALLOWED_HOSTS = {"wearhongxiu.com", "www.wearhongxiu.com"}
CONFIG_KEYS = (
    "WEARHONGXIU_WP_URL",
    "WEARHONGXIU_WP_USER",
    "WEARHONGXIU_WP_APP_PASSWORD",
    "WEARHONGXIU_WP_TIMEOUT",
)


class ApiError(Exception):
    pass


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return values


def config(path: str | None) -> dict[str, str]:
    cfg_path = Path(path) if path else Path(
        os.environ.get("WEARHONGXIU_WP_CONFIG", SKILL_DIR / "config.env")
    )
    cfg = load_env(cfg_path)
    for key in CONFIG_KEYS:
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    missing = [
        key for key in (
            "WEARHONGXIU_WP_URL",
            "WEARHONGXIU_WP_USER",
            "WEARHONGXIU_WP_APP_PASSWORD",
        ) if not cfg.get(key)
    ]
    if missing:
        raise ApiError("missing dedicated Wearhongxiu configuration: " + ", ".join(missing))
    parsed = urllib.parse.urlparse(cfg["WEARHONGXIU_WP_URL"])
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise ApiError("refusing configuration: the WordPress URL is not the canonical Wearhongxiu HTTPS host")
    return cfg


class LockedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
            raise ApiError("refusing cross-site redirect from Wearhongxiu")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, cfg: dict[str, str]):
        self.base = cfg["WEARHONGXIU_WP_URL"].rstrip("/")
        self.timeout = float(cfg.get("WEARHONGXIU_WP_TIMEOUT") or 30)
        raw = f'{cfg["WEARHONGXIU_WP_USER"]}:{cfg["WEARHONGXIU_WP_APP_PASSWORD"]}'
        token = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        self.headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "User-Agent": "Codex-Wearhongxiu-WP/1.0",
        }
        self.opener = urllib.request.build_opener(LockedRedirectHandler())

    @staticmethod
    def normalize_route(route: str) -> str:
        parsed = urllib.parse.urlsplit(route)
        path = parsed.path
        if path.startswith("/wp-json/"):
            path = path[len("/wp-json"):]
        elif path == "/wp-json":
            path = "/"
        if not path.startswith("/"):
            path = "/" + path
        return path

    def url(self, route: str, params: dict[str, str] | None = None, fallback=False) -> str:
        path = self.normalize_route(route)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(route).query))
        query.update(params or {})
        if fallback:
            query = {"rest_route": path, **query}
            return self.base + "/?" + urllib.parse.urlencode(query)
        suffix = ("?" + urllib.parse.urlencode(query)) if query else ""
        return self.base + "/wp-json" + path + suffix

    def request(self, method: str, route: str, params=None, data=None, headers=None):
        req_headers = dict(self.headers)
        body = data
        if data is not None and not isinstance(data, (bytes, bytearray)):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        if headers:
            req_headers.update(headers)
        last_error = None
        for fallback in (False, True):
            url = self.url(route, params=params, fallback=fallback)
            req = urllib.request.Request(url, data=body, method=method, headers=req_headers)
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    raw = response.read()
                    hdrs = {k.lower(): v for k, v in response.headers.items()}
                    if not raw:
                        return None, hdrs
                    text = raw.decode("utf-8", "replace")
                    try:
                        return json.loads(text), hdrs
                    except json.JSONDecodeError:
                        return text, hdrs
            except urllib.error.HTTPError as error:
                if error.code == 404 and not fallback:
                    last_error = error
                    continue
                detail = error.read().decode("utf-8", "replace")
                try:
                    payload = json.loads(detail)
                    detail = payload.get("message") or payload.get("code") or detail
                except Exception:
                    pass
                raise ApiError(f"HTTP {error.code}: {detail[:500]}") from None
            except urllib.error.URLError as error:
                raise ApiError(f"cannot reach Wearhongxiu WordPress: {error.reason}") from None
        raise ApiError(f"HTTP {last_error.code if last_error else 404}: route not found")


def deep_get(value, dotted: str):
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def emit(value, fields: str | None = None):
    if fields:
        names = [name.strip() for name in fields.split(",") if name.strip()]
        if isinstance(value, list):
            value = [{name: deep_get(item, name) for name in names} for item in value]
        elif isinstance(value, dict):
            value = {name: deep_get(value, name) for name in names}
    print(json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value)


def pairs(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ApiError(f"expected KEY=VALUE, got {value}")
        key, item = value.split("=", 1)
        result[key] = item
    return result


def read_json(args):
    if args.data_file:
        return json.loads(Path(args.data_file).read_text(encoding="utf-8"))
    if args.data_json:
        return json.loads(args.data_json)
    return None


def cmd_info(client: Client, args):
    root, _ = client.request("GET", "/")
    me, _ = client.request("GET", "/wp/v2/users/me", params={"context": "edit"})
    emit({
        "name": root.get("name"),
        "url": root.get("url"),
        "authenticated_user": {"id": me.get("id"), "name": me.get("name")},
        "has_wp_v2": "wp/v2" in (root.get("namespaces") or []),
        "has_hx_v1": "hx/v1" in (root.get("namespaces") or []),
    })


def cmd_routes(client: Client, args):
    data, _ = client.request("GET", "/" + args.namespace.strip("/"))
    rows = [
        {"route": route, "methods": sorted(info.get("methods") or [])}
        for route, info in (data.get("routes") or {}).items()
    ]
    emit(sorted(rows, key=lambda row: row["route"]), args.fields)


def cmd_get(client: Client, args):
    params = pairs(args.param)
    if not args.all:
        data, _ = client.request("GET", args.route, params=params)
        emit(data, args.fields)
        return
    page, output = 1, []
    while True:
        page_params = {**params, "page": str(page), "per_page": params.get("per_page", "100")}
        data, headers = client.request("GET", args.route, params=page_params)
        if not isinstance(data, list):
            raise ApiError("--all requires a paginated list endpoint")
        output.extend(data)
        total_pages = int(headers.get("x-wp-totalpages") or page)
        if page >= total_pages:
            break
        page += 1
    emit(output, args.fields)


def cmd_write(client: Client, args, method: str):
    data, _ = client.request(method, args.route, params=pairs(args.param), data=read_json(args))
    emit(data, args.fields)


def cmd_media_upload(client: Client, args):
    path = Path(args.file)
    if not path.is_file():
        raise ApiError(f"file not found: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    created, _ = client.request(
        "POST", "/wp/v2/media", data=path.read_bytes(),
        headers={"Content-Type": mime, "Content-Disposition": f'attachment; filename="{path.name}"'},
    )
    metadata = {k: v for k, v in {
        "title": args.title, "alt_text": args.alt_text,
        "caption": args.caption, "description": args.description,
    }.items() if v is not None}
    if metadata:
        created, _ = client.request("POST", f'/wp/v2/media/{created["id"]}', data=metadata)
    emit(created, args.fields)


def cmd_pod(client: Client, args):
    if args.action == "schema":
        data, _ = client.request("GET", "/hx/v1/pod-products/schema")
    elif args.action == "get":
        item = urllib.parse.quote(args.external_id, safe="")
        data, _ = client.request("GET", f"/hx/v1/pod-products/by-external-id/{item}")
    else:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        data, _ = client.request("POST", "/hx/v1/pod-products/sync", data=payload)
    emit(data, args.fields)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Wearhongxiu-locked WordPress REST client")
    root.add_argument("--config", help="Dedicated Wearhongxiu config.env path")
    sub = root.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info")
    info.set_defaults(func=cmd_info)

    routes = sub.add_parser("routes")
    routes.add_argument("--namespace", default="wp/v2")
    routes.add_argument("--fields")
    routes.set_defaults(func=cmd_routes)

    get = sub.add_parser("get")
    get.add_argument("route")
    get.add_argument("--param", action="append", default=[])
    get.add_argument("--all", action="store_true")
    get.add_argument("--fields")
    get.set_defaults(func=cmd_get)

    for name, method in (("post", "POST"), ("delete", "DELETE")):
        item = sub.add_parser(name)
        item.add_argument("route")
        item.add_argument("--param", action="append", default=[])
        item.add_argument("--data-json")
        item.add_argument("--data-file")
        item.add_argument("--fields")
        item.set_defaults(func=lambda client, args, verb=method: cmd_write(client, args, verb))

    media = sub.add_parser("media")
    media_sub = media.add_subparsers(dest="action", required=True)
    upload = media_sub.add_parser("upload")
    upload.add_argument("file")
    upload.add_argument("--title")
    upload.add_argument("--alt-text")
    upload.add_argument("--caption")
    upload.add_argument("--description")
    upload.add_argument("--fields")
    upload.set_defaults(func=cmd_media_upload)

    pod = sub.add_parser("pod")
    pod_sub = pod.add_subparsers(dest="action", required=True)
    schema = pod_sub.add_parser("schema")
    schema.add_argument("--fields")
    schema.set_defaults(func=cmd_pod)
    pod_get = pod_sub.add_parser("get")
    pod_get.add_argument("external_id")
    pod_get.add_argument("--fields")
    pod_get.set_defaults(func=cmd_pod)
    sync = pod_sub.add_parser("sync")
    sync.add_argument("file")
    sync.add_argument("--fields")
    sync.set_defaults(func=cmd_pod)
    return root


def main():
    args = parser().parse_args()
    try:
        args.func(Client(config(args.config)), args)
    except (ApiError, json.JSONDecodeError, OSError, ValueError) as error:
        sys.exit(f"error: {error}")


if __name__ == "__main__":
    main()

