"""Configure the Google Ads MCP connection from runtime-only environment secrets."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re


BEGIN = "# BEGIN CODEX GOOGLE ADS MCP"
END = "# END CODEX GOOGLE ADS MCP"
CODEX_HOME = Path(os.environ.get("CODEX_HOME", "/root/.codex"))


def main() -> None:
    developer_token = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip()
    login_customer_id = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").strip()
    encoded_credentials = os.environ.get("GOOGLE_ADS_SERVICE_ACCOUNT_B64", "").strip()
    if not all((developer_token, login_customer_id, encoded_credentials)):
        print("Google Ads MCP not configured: required runtime values are missing")
        return

    try:
        credentials_bytes = base64.b64decode(encoded_credentials, validate=True)
        credentials = json.loads(credentials_bytes.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Invalid Google Ads service-account payload") from exc
    if credentials.get("type") != "service_account" or not credentials.get("private_key"):
        raise RuntimeError("Google Ads credentials are not a service account")

    credential_dir = CODEX_HOME / "google-ads"
    credential_dir.mkdir(parents=True, exist_ok=True)
    credential_path = credential_dir / "service-account.json"
    credential_path.write_bytes(credentials_bytes)
    credential_path.chmod(0o600)

    config_path = CODEX_HOME / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    current = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    marker_pattern = re.compile(
        rf"\n?{re.escape(BEGIN)}.*?{re.escape(END)}\n?", re.DOTALL
    )
    current = marker_pattern.sub("\n", current).rstrip()
    block = f'''{BEGIN}
[mcp_servers.google-ads-mcp]
command = "/usr/local/bin/google-ads-mcp"
startup_timeout_sec = 60
tool_timeout_sec = 180

[mcp_servers.google-ads-mcp.env]
GOOGLE_ADS_DEVELOPER_TOKEN = {json.dumps(developer_token)}
GOOGLE_ADS_LOGIN_CUSTOMER_ID = {json.dumps(login_customer_id)}
GOOGLE_APPLICATION_CREDENTIALS = {json.dumps(str(credential_path))}
FASTMCP_CHECK_FOR_UPDATES = "false"
FASTMCP_LOG_LEVEL = "WARNING"
FASTMCP_SHOW_SERVER_BANNER = "false"
{END}
'''
    config_path.write_text((current + "\n\n" + block).lstrip(), encoding="utf-8")
    config_path.chmod(0o600)
    print("Google Ads MCP configured")


if __name__ == "__main__":
    main()
