"""Materialize skill runtime config from environment variables.

Some skills read a config file beside, or instead of, the process environment:
`wearhongxiu-wp` uses `<skill>/config.env`, `zoho-api` uses `~/.zoho-api/.env`,
and `meta-business` uses `~/.meta-business/credentials.json`. Those files hold
live credentials, so they are deliberately absent from version control and are
rebuilt here at container start from the Coolify environment. Nothing secret is
baked into the image.

This matters more under DSH than it did under Codex: the harness scrubs any
ambient variable whose name matches KEY/PASSWORD/SECRET/TOKEN out of the
environment it gives shell commands, so a skill that only reads `os.environ`
cannot see its credential at all. A file on disk is the supported path.

Run from the entrypoint before the server starts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SKILL_ROOT = Path(os.getenv("DSH_SKILL_ROOT", "/root/.codex/skills"))
WEAR_DIR = SKILL_ROOT / "wearhongxiu-wp"
WEAR_CONFIG = WEAR_DIR / "config.env"

# zoho-api reads ~/.zoho-api/.env (or config.json) rather than the environment,
# exactly as codex-worker's entrypoint arranges.
ZOHO_DIR = Path(os.getenv("ZOHO_CONFIG_DIR", str(Path.home() / ".zoho-api")))
ZOHO_ENV = ZOHO_DIR / ".env"

ZOHO_KEYS = [
    "ZOHO_REGION",
    "ZOHO_CLIENT_ID",
    "ZOHO_CLIENT_SECRET",
    "ZOHO_REFRESH_TOKEN",
    "ZOHO_CAMPAIGNS_LIST_NAME",
    "ZOHO_CAMPAIGNS_TOPIC_NAME",
    "ZOHO_CAMPAIGNS_FROM_EMAIL",
    "ZOHO_CAMPAIGNS_FROM_NAME",
]

# meta-business reads ~/.meta-business/credentials.json (or $META_BUSINESS_CONFIG_FILE).
# Pairs are (environment variable, key in the credential file), taken from
# meta_graph.require_object_id and media_stage's R2 block.
META_CONFIG = Path(
    os.getenv("META_BUSINESS_CONFIG_FILE", str(Path.home() / ".meta-business" / "credentials.json"))
)
META_KEYS = [
    ("META_BUSINESS_ACCESS_TOKEN", "access_token"),
    ("META_GRAPH_API_VERSION", "graph_api_version"),
    ("META_BUSINESS_PAGE_ID", "page_id"),
    ("META_BUSINESS_IG_USER_ID", "ig_user_id"),
    ("META_BUSINESS_AD_ACCOUNT_ID", "ad_account_id"),
    ("META_BUSINESS_BUSINESS_ID", "business_id"),
    ("META_BUSINESS_CATALOG_ID", "catalog_id"),
    ("META_THREADS_USER_ID", "threads_user_id"),
    ("META_MEDIA_R2_ACCOUNT_ID", "media_r2_account_id"),
    ("META_MEDIA_R2_ACCESS_KEY_ID", "media_r2_access_key_id"),
    ("META_MEDIA_R2_SECRET_ACCESS_KEY", "media_r2_secret_access_key"),
    ("META_MEDIA_R2_BUCKET", "media_r2_bucket"),
    ("META_MEDIA_R2_PUBLIC_BASE_URL", "media_r2_public_base_url"),
    ("META_MEDIA_R2_PREFIX", "media_r2_prefix"),
]

# Credentials the harness scrubs out of shell environments, for the skills that
# have no config-file support of their own. Their scripts read this file via the
# `skill_credentials` helper installed beside them.
#
# Curated, not pattern-matched: DSH_WORKER_TOKEN and DEEPSEEK_API_KEY match the
# scrub pattern too, and neither belongs in a file a skill script can read.
CREDENTIAL_FILE = Path(
    os.getenv("DSH_SKILL_CREDENTIALS", "/root/.dsh-skill-credentials.env")
)
CREDENTIAL_KEYS = [
    "COLLECTOR_API_URL",
    "COLLECTOR_API_KEY",
    "CRUN_API_KEY",
    "CRUN_BASE_URL",
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
    "GOOGLE_ADS_SERVICE_ACCOUNT_B64",
    "MARKETING_SCHEDULER_URL",
    "MARKETING_SCHEDULER_TOKEN",
    "REDDIT_OPS_URL",
    "REDDIT_OPS_API_KEY",
    "REPLICATE_API_TOKEN",
    "SHOPIFY_SHOP",
    "SHOPIFY_CLIENT_ID",
    "SHOPIFY_CLIENT_SECRET",
    "SHOPIFY_API_VERSION",
    "POD_API_URL",
    "POD_MONITORING_TOKEN",
    "HONGXIU_RAG_URL",
    "HONGXIU_RAG_TOKEN",
    "LINKEDIN_API_VERSION",
    "LINKEDIN_CLIENT_ID",
    "LINKEDIN_CLIENT_SECRET",
    "LINKEDIN_REDIRECT_URI",
    "LINKEDIN_OAUTH_SCOPES",
    "LINKEDIN_STATE_SECRET",
    "LINKEDIN_TOKEN_PATH",
    "GITHUB_TOKEN",
    "COOLIFY_BASE_URL",
    "COOLIFY_API_TOKEN",
] + [env for env, _key in META_KEYS] + [
    "WEARHONGXIU_WP_URL",
    "WEARHONGXIU_WP_USER",
    "WEARHONGXIU_WP_APP_PASSWORD",
    "WEARHONGXIU_PAGESPEED_API_KEY",
    "WEARHONGXIU_GSC_CREDENTIALS",
    "WEARHONGXIU_GA4_CREDENTIALS",
    "WEARHONGXIU_NEWSAPI_KEY",
    "WEARHONGXIU_JINA_API_TOKEN",
    "WEARHONGXIU_LLM_API_KEY",
    "WEARHONGXIU_VISION_LLM_API_KEY",
    "ZOHO_CLIENT_SECRET",
    "ZOHO_REFRESH_TOKEN",
]

# Written in this order, matching the skill's own config.example.env so the file
# stays familiar. Values come from the process environment.
WEAR_KEYS = [
    "WEARHONGXIU_WP_URL",
    "WEARHONGXIU_WP_USER",
    "WEARHONGXIU_WP_APP_PASSWORD",
    "WEARHONGXIU_WP_TIMEOUT",
    "WEARHONGXIU_HOSTINGER_API_TOKEN",
    "WEARHONGXIU_HOSTINGER_USERNAME",
    "WEARHONGXIU_HOSTINGER_DOMAIN",
    "WEARHONGXIU_HOSTINGER_API_BASE",
    "WEARHONGXIU_NEWSAPI_KEY",
    "WEARHONGXIU_JINA_API_TOKEN",
    "WEARHONGXIU_LLM_BASE_URL",
    "WEARHONGXIU_LLM_API_KEY",
    "WEARHONGXIU_LLM_MODEL",
    "WEARHONGXIU_VISION_LLM_BASE_URL",
    "WEARHONGXIU_VISION_LLM_API_KEY",
    "WEARHONGXIU_VISION_LLM_MODEL",
    "HONGXIU_RAG_TOKEN",
    "WEARHONGXIU_GSC_CREDENTIALS",
    "WEARHONGXIU_GSC_SITE_URL",
    "WEARHONGXIU_GSC_SITEMAP_URL",
    "WEARHONGXIU_GA4_CREDENTIALS",
    "WEARHONGXIU_PAGESPEED_API_KEY",
]


def write_secret_file(path: Path, header: str, values: dict[str, str]) -> None:
    """Write KEY=value lines at 0600, via a temp file so it is never world-readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(header)
        for key, value in values.items():
            handle.write(f"{key}={value}\n")
    temporary.replace(path)
    os.chmod(path, 0o600)


def write_zoho() -> str:
    present = {key: os.environ.get(key, "") for key in ZOHO_KEYS}
    if not any(v.strip() for v in present.values()):
        return "skipped (no ZOHO_* variables are set)"
    required = ("ZOHO_REGION", "ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN")
    missing = [k for k in required if not present[k].strip()]
    if missing:
        return f"skipped (incomplete: {', '.join(missing)} missing)"
    write_secret_file(
        ZOHO_ENV,
        "# Generated by dsh-worker configure_skills.py from the Coolify\n"
        "# environment. Do not edit: it is rebuilt on every start.\n",
        present,
    )
    return f"wrote {ZOHO_ENV}"


def write_wearhongxiu() -> str:
    present = {key: os.environ.get(key, "") for key in WEAR_KEYS}
    if not any(value.strip() for value in present.values()):
        return "skipped (no WEARHONGXIU_* variables are set)"
    if not WEAR_DIR.is_dir():
        return f"skipped ({WEAR_DIR} is not present in this image)"

    # 0600 before any secret lands in the file, never after.
    write_secret_file(
        WEAR_CONFIG,
        "# Generated by dsh-worker configure_skills.py from the\n"
        "# Coolify environment. Do not edit: it is rebuilt on every start.\n",
        present,
    )
    configured = sum(1 for v in present.values() if v.strip())
    return f"wrote {WEAR_CONFIG} with {configured}/{len(WEAR_KEYS)} values set"


def write_meta() -> str:
    present = {key: os.environ.get(env, "").strip() for env, key in META_KEYS}
    if not present.get("access_token"):
        return "skipped (META_BUSINESS_ACCESS_TOKEN is not set)"
    payload = {key: value for key, value in present.items() if value}
    META_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = META_CONFIG.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(META_CONFIG)
    os.chmod(META_CONFIG, 0o600)
    return f"wrote {META_CONFIG} with {len(payload)} key(s)"


def write_credential_file() -> str:
    """Write the fallback file the skill_credentials helper reads."""
    present = {key: os.environ.get(key, "") for key in CREDENTIAL_KEYS}
    configured = {k: v for k, v in present.items() if v.strip()}
    if not configured:
        return "skipped (no skill credentials are set)"
    write_secret_file(
        CREDENTIAL_FILE,
        "# Generated by dsh-worker configure_skills.py from the Coolify\n"
        "# environment. Read by skill scripts through `skill_credentials`.\n"
        "# Do not edit: it is rebuilt on every start.\n",
        {k: present[k] for k in CREDENTIAL_KEYS},
    )
    return f"wrote {CREDENTIAL_FILE} with {len(configured)}/{len(CREDENTIAL_KEYS)} values set"


def write_linkedin_token() -> str:
    """Decode LINKEDIN_TOKEN_B64 into the path linkedin.py reads.

    The token comes from an interactive OAuth flow whose redirect URI is
    registered to the codex-worker domain, so it cannot be re-created from the
    dsh-worker; it is exported once and carried here base64-encoded. Note it is
    an access token with no refresh token, so when it expires it must be
    re-authorized on codex-worker and re-exported - do not try to refresh it
    here. Because of that, exactly one worker should own re-authorization.
    """
    import base64

    encoded = os.environ.get("LINKEDIN_TOKEN_B64", "").strip()
    if not encoded:
        return "skipped (LINKEDIN_TOKEN_B64 is not set)"
    target = Path(
        os.environ.get("LINKEDIN_TOKEN_PATH", str(Path.home() / ".codex" / "linkedin" / "oauth.json"))
    )
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        parsed = json.loads(decoded)
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        return f"FAILED to decode LINKEDIN_TOKEN_B64: {type(exc).__name__}"
    if not parsed.get("access_token"):
        return "FAILED: decoded payload has no access_token"

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(decoded if decoded.endswith("\n") else decoded + "\n")
    temporary.replace(target)
    os.chmod(target, 0o600)
    expires = parsed.get("expires_at")
    return f"wrote {target} (access_token present, expires_at={expires}, refresh_token={bool(parsed.get('refresh_token'))})"


def main() -> None:
    print("configure_skills:", write_zoho())
    print("configure_skills:", write_wearhongxiu())
    print("configure_skills:", write_meta())
    print("configure_skills:", write_credential_file())
    print("configure_skills:", write_linkedin_token())


if __name__ == "__main__":
    main()
