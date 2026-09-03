#!/usr/bin/env python3
"""Zoho REST API helper (Mail / CRM / Campaigns).

Commands:
  token      Print a fresh access token (refreshes via refresh_token grant).
  call       Make an authenticated API call (auto-refreshes on 401).
  auth-url   Print the OAuth authorization URL for a set of scopes.
  exchange   Exchange a Self Client authorization code for tokens and save the
             refresh token into the credentials file (.env or JSON).

Credentials are read from, in order of priority:
  1. --env-file PATH            (explicit .env file)
  2. ~/.zoho-api/.env           (default .env file)
  3. --config PATH              (JSON config, default ~/.zoho-api/config.json)

.env example (~/.zoho-api/.env):
  ZOHO_REGION=cn
  ZOHO_CLIENT_ID=1000.xxx
  ZOHO_CLIENT_SECRET=xxx
  ZOHO_REFRESH_TOKEN=1000.xxx

Examples:
  python zoho_api.py exchange --code <authorization-code>
  python zoho_api.py token
  python zoho_api.py call --app mail --endpoint /accounts
  python zoho_api.py call --app crm --endpoint /Leads --params "per_page=5"
  python zoho_api.py call --app campaigns --endpoint /lists
  python zoho_api.py auth-url --app mail
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_CONFIG = os.path.join(os.path.expanduser("~"), ".zoho-api", "config.json")
DEFAULT_ENV_FILE = os.path.join(os.path.expanduser("~"), ".zoho-api", ".env")

BASE_URLS = {
    "mail": {
        "com": "https://mail.zoho.com/api",
        "cn": "https://mail.zoho.com.cn/api",
    },
    "crm": {
        "com": "https://www.zohoapis.com/crm/v2",
        "cn": "https://www.zohoapis.com.cn/crm/v2",
    },
    "campaigns": {
        "com": "https://campaigns.zoho.com/api/v1.1",
        "cn": "https://campaigns.zoho.com.cn/api/v1.1",
    },
}

TOKEN_URLS = {
    "com": "https://accounts.zoho.com/oauth/v2/token",
    "cn": "https://accounts.zoho.com.cn/oauth/v2/token",
}

AUTH_URLS = {
    "com": "https://accounts.zoho.com/oauth/v2/auth",
    "cn": "https://accounts.zoho.com.cn/oauth/v2/auth",
}

ENV_KEY_MAP = {
    "region": "ZOHO_REGION",
    "client_id": "ZOHO_CLIENT_ID",
    "client_secret": "ZOHO_CLIENT_SECRET",
    "refresh_token": "ZOHO_REFRESH_TOKEN",
}

DEFAULT_SCOPES = {
    "mail": [
        "ZohoMail.messages.ALL",
        "ZohoMail.folders.ALL",
        "ZohoMail.tags.ALL",
        "ZohoMail.tasks.ALL",
        "ZohoMail.accounts.READ",
        "ZohoMail.attachments.READ",
        "ZohoMail.settings.ALL",
    ],
    "crm": [
        "ZohoCRM.modules.ALL",
        "ZohoCRM.settings.ALL",
        "ZohoCRM.users.READ",
        "ZohoCRM.org.READ",
        "ZohoCRM.bulk.ALL",
    ],
    "campaigns": [
        "ZohoCampaigns.campaign.ALL",
        "ZohoCampaigns.contact.ALL",
    ],
}


def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def pick_credentials_path(args):
    """Return (kind, path) for credentials: ('env', ...) or ('json', ...)."""
    if getattr(args, "env_file", None):
        return "env", args.env_file
    if os.path.exists(DEFAULT_ENV_FILE):
        return "env", DEFAULT_ENV_FILE
    return "json", args.config


def load_env_file(path):
    if not os.path.exists(path):
        die(
            f"Env file not found: {path}\n"
            "Create it from assets/.env.example (see references/setup.md)."
        )
    cfg = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                for field, var in ENV_KEY_MAP.items():
                    if var == key:
                        cfg[field] = value
    except Exception as exc:
        die(f"Cannot read env file {path}: {exc}")
    return cfg


def load_json_config(path):
    if not os.path.exists(path):
        die(
            f"Config file not found: {path}\n"
            "Create it from assets/config.example.json (see references/setup.md)."
        )
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception as exc:
        die(f"Cannot read config {path}: {exc}")


def load_credentials(path, kind, need_refresh_token=True):
    cfg = load_env_file(path) if kind == "env" else load_json_config(path)
    for field, var in ENV_KEY_MAP.items():
        if not cfg.get(field) and os.environ.get(var):
            cfg[field] = os.environ[var]
    cfg.setdefault("region", "cn")
    if cfg["region"] not in ("cn", "com"):
        die(f"region must be 'cn' or 'com', got: {cfg['region']}")
    missing = [k for k in ("client_id", "client_secret") if not cfg.get(k)]
    if need_refresh_token and not cfg.get("refresh_token"):
        missing.append("refresh_token")
    if missing:
        die(f"{path} is missing: {', '.join(missing)}")
    return cfg


def token_cache_path(credentials_path):
    return credentials_path + ".token.json"


def load_cached_token(cache_path):
    try:
        with open(cache_path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        if data.get("expires_at", 0) > time.time() + 30:
            return data["access_token"]
    except Exception:
        pass
    return None


def save_token(cache_path, access_token, expires_in):
    payload = {
        "access_token": access_token,
        "expires_at": time.time() + int(expires_in or 3600) - 60,
    }
    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except Exception as exc:
        print(f"WARNING: could not cache token at {cache_path}: {exc}", file=sys.stderr)


def refresh_access_token(cfg):
    data = urllib.parse.urlencode(
        {
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": cfg["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    req = urllib.request.Request(TOKEN_URLS[cfg["region"]], data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        die(f"Token refresh failed ({exc.code}): {exc.read().decode('utf-8', 'replace')}")
    except Exception as exc:
        die(f"Token refresh failed: {exc}")
    if "access_token" not in body:
        die(f"Token refresh failed: {json.dumps(body)}")
    return body


def get_access_token(cfg, credentials_path):
    cached = load_cached_token(token_cache_path(credentials_path))
    if cached:
        return cached
    body = refresh_access_token(cfg)
    save_token(token_cache_path(credentials_path), body["access_token"], body.get("expires_in"))
    return body["access_token"]


def parse_params(pairs):
    out = {}
    for pair in pairs:
        if "=" not in pair:
            die(f"Invalid --params '{pair}'; expected k=v")
        key, value = pair.split("=", 1)
        out[key] = value
    return out


def http_request(method, url, token, data=None, auth_header="zoho"):
    scheme = "Zoho-oauthtoken" if auth_header == "zoho" else "Bearer"
    headers = {
        "Authorization": f"{scheme} {token}",
        "Accept": "application/json",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        die(f"Request failed: {exc}")


def print_response(status, raw):
    try:
        payload = json.loads(raw)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(raw)
    if status >= 400:
        sys.exit(1)


def save_refresh_token(kind, path, token):
    if kind == "env":
        lines = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8-sig") as fh:
                lines = fh.readlines()
        updated = False
        out = []
        for line in lines:
            if line.strip().startswith("ZOHO_REFRESH_TOKEN="):
                out.append(f"ZOHO_REFRESH_TOKEN={token}\n")
                updated = True
            else:
                out.append(line)
        if not updated:
            out.append(f"ZOHO_REFRESH_TOKEN={token}\n")
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(out)
    else:
        cfg = load_json_config(path)
        cfg["refresh_token"] = token
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)


def cmd_exchange(args):
    kind, path = pick_credentials_path(args)
    cfg = load_credentials(path, kind, need_refresh_token=False)
    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "code": args.code,
            "redirect_uri": args.redirect_uri,
        }
    ).encode("utf-8")
    req = urllib.request.Request(TOKEN_URLS[cfg["region"]], data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        die(f"Code exchange failed ({exc.code}): {exc.read().decode('utf-8', 'replace')}")
    except Exception as exc:
        die(f"Code exchange failed: {exc}")
    if "refresh_token" not in body:
        die(f"Code exchange failed: {json.dumps(body)}")
    if args.no_save:
        print(f"refresh_token: {body['refresh_token']}")
    else:
        save_refresh_token(kind, path, body["refresh_token"])
        print(f"Refresh token saved to {path}")
    print(f"access_token: {body['access_token']}")
    print(f"expires_in: {body.get('expires_in')}")


def cmd_token(args):
    kind, path = pick_credentials_path(args)
    cfg = load_credentials(path, kind)
    body = refresh_access_token(cfg)
    save_token(token_cache_path(path), body["access_token"], body.get("expires_in"))
    print(body["access_token"])


def cmd_call(args):
    kind, path = pick_credentials_path(args)
    cfg = load_credentials(path, kind)
    token = get_access_token(cfg, path)
    params = parse_params(args.params)
    if args.url:
        url = args.url
        if params:
            sep = "&" if "?" in url else "?"
            url += sep + urllib.parse.urlencode(params)
    else:
        if not args.app or not args.endpoint:
            die("Provide --app and --endpoint, or --url")
        url = BASE_URLS[args.app][cfg["region"]] + (
            args.endpoint if args.endpoint.startswith("/") else "/" + args.endpoint
        )
        if params:
            url += "?" + urllib.parse.urlencode(params)

    data = None
    if args.data:
        try:
            data = json.loads(args.data)
        except json.JSONDecodeError as exc:
            die(f"--data is not valid JSON: {exc}")

    status, raw = http_request(args.method, url, token, data, args.auth_header)
    if status == 401:
        body = refresh_access_token(cfg)
        save_token(token_cache_path(path), body["access_token"], body.get("expires_in"))
        status, raw = http_request(args.method, url, body["access_token"], data, args.auth_header)
    print_response(status, raw)


def cmd_auth_url(args):
    kind, path = pick_credentials_path(args)
    cfg = load_credentials(path, kind, need_refresh_token=False)
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()] if args.scopes else DEFAULT_SCOPES.get(args.app, [])
    if not scopes:
        die("Provide --scopes or a valid --app (mail, crm, campaigns)")
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": args.redirect_uri,
        "response_type": "code",
        "scope": ",".join(scopes),
        "access_type": "offline",
    }
    print(AUTH_URLS[cfg["region"]] + "?" + urllib.parse.urlencode(params))


def add_common_flags(parser):
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"JSON config path (default: {DEFAULT_CONFIG})")
    parser.add_argument("--env-file", default=None, help=".env credentials file (default: ~/.zoho-api/.env)")


def main():
    parser = argparse.ArgumentParser(
        description="Zoho API helper (Mail / CRM / Campaigns)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_exchange = sub.add_parser("exchange", help="Exchange a Self Client code for tokens and save the refresh token")
    add_common_flags(p_exchange)
    p_exchange.add_argument("--code", required=True, help="Authorization code from the API Console (Self Client > Generate Code)")
    p_exchange.add_argument("--redirect-uri", default="http://localhost:8080/", help="Redirect URI used when generating the code")
    p_exchange.add_argument("--no-save", action="store_true", help="Print the refresh token instead of saving it")
    p_exchange.set_defaults(func=cmd_exchange)

    p_token = sub.add_parser("token", help="Print a fresh access token")
    add_common_flags(p_token)
    p_token.set_defaults(func=cmd_token)

    p_call = sub.add_parser("call", help="Make an authenticated API call")
    add_common_flags(p_call)
    p_call.add_argument("--app", choices=["mail", "crm", "campaigns"])
    p_call.add_argument("--endpoint", help="API path, e.g. /accounts or /Leads")
    p_call.add_argument("--url", help="Full API URL (overrides --app/--endpoint)")
    p_call.add_argument("--method", default="GET")
    p_call.add_argument("--params", action="append", default=[], help="k=v query parameter (repeatable)")
    p_call.add_argument("--data", help="JSON request body")
    p_call.add_argument("--auth-header", choices=["zoho", "bearer"], default="zoho")
    p_call.set_defaults(func=cmd_call)

    p_auth = sub.add_parser("auth-url", help="Print the OAuth authorization URL")
    add_common_flags(p_auth)
    p_auth.add_argument("--app", choices=["mail", "crm", "campaigns"])
    p_auth.add_argument("--scopes", help="Comma-separated custom scopes")
    p_auth.add_argument("--redirect-uri", default="http://localhost:8080/")
    p_auth.set_defaults(func=cmd_auth_url)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
