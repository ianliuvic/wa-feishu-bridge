---
name: zoho-api
description: Operate Zoho Mail, Zoho CRM, and Zoho Campaigns through their REST APIs (including China .com.cn domains) with OAuth2 token management, bypassing Zoho MCP. Use when the user asks to connect to or operate Zoho apps via API, when Zoho MCP is unavailable (e.g. mcp.zoho.com.cn not working), or for tasks like reading/sending email, managing CRM records, creating/sending campaigns, and managing contacts or lists.
---

# Zoho API

Call Zoho REST APIs (Mail / CRM / Campaigns) with automatic OAuth2 token refresh. Supports China (`cn`) and global (`com`) regions.

## Before first use

1. Read `references/setup.md` and create an OAuth app (Self Client) in the Zoho API Console for your region. Collect `client_id` and `client_secret`.
2. Create `~/.zoho-api/.env` (Windows: `C:\Users\<you>\.zoho-api\.env`) using `assets/.env.example` as a template. Set `ZOHO_REGION=cn` for China accounts, `com` otherwise.
3. Generate an authorization code in the API Console (Self Client > Generate Code) with the scopes you need, then run:

   ```
   python scripts/zoho_api.py exchange --code <code>
   ```

   This saves `ZOHO_REFRESH_TOKEN` into the `.env` file automatically.
4. Verify auth:

   ```
   python scripts/zoho_api.py token
   ```

   This prints a fresh access token. Tokens are cached next to the credentials file and refreshed automatically on expiry or HTTP 401.

## Making API calls

```
python scripts/zoho_api.py call --app mail --endpoint /accounts
python scripts/zoho_api.py call --app crm --endpoint /Leads --params "per_page=5"
python scripts/zoho_api.py call --app campaigns --endpoint /lists
python scripts/zoho_api.py call --url <full-url> --method POST --data '{"data":[...]}'
```

Options:

- `--app` + `--endpoint`: build the URL from the app base (region-aware). `--url` overrides.
- `--method`: default `GET`; supports POST/PUT/DELETE.
- `--params`: repeatable `k=v` query parameters.
- `--data`: JSON request body (sets `Content-Type: application/json`).
- `--auth-header`: `zoho` (default, `Zoho-oauthtoken`) or `bearer`.

## Per-app endpoints

- Zoho Mail: `references/mail.md`
- Zoho CRM: `references/crm.md`
- Zoho Campaigns: `references/campaigns.md`

Read the relevant reference before calling an app; each includes base URLs, scopes, and common endpoints.

## Rules

- Prefer read-only calls first. Before any write/send/delete operation, confirm the action and target data with the user.
- Match the region to the Zoho account's data center; a mismatched region returns 401.
- Respect API rate limits; use Zoho bulk APIs for large CRM operations.
- Never print `client_secret` or `refresh_token`; report only the access token when explicitly needed.
