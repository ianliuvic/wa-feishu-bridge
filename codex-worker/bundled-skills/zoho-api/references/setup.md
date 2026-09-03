# Zoho API Setup

## 1. Create an OAuth app

Open the Zoho API Console for your region and sign in with the Zoho account that owns the data:

| Region | API Console | Accounts domain |
|---|---|---|
| China | https://api-console.zoho.com.cn/ | accounts.zoho.com.cn |
| Global | https://api-console.zoho.com/ | accounts.zoho.com |

Create a client:

- **Self Client** - simplest for personal use; the client can generate authorization codes itself.
- **Server-based Application** - for broader/delegated use; requires a redirect URI.

Save the `Client ID` and `Client Secret`.

## 2. Scopes

Minimum scope sets per app. The Generate Code box requires **comma-separated** scopes (no spaces).

### Zoho Mail (user)

- `ZohoMail.messages.ALL` - read/send/search/delete emails
- `ZohoMail.folders.ALL` - folders
- `ZohoMail.tags.ALL` - labels/tags
- `ZohoMail.tasks.ALL` - personal/group tasks
- `ZohoMail.accounts.READ` - account list
- `ZohoMail.attachments.READ` - attachments
- `ZohoMail.settings.ALL` - vacation reply, forwarding
- Admin only (verify exact names in the official Mail API docs): `ZohoMail.organization.accounts.ALL`, `ZohoMail.organization.domains`, `ZohoMail.organization.groups`, `ZohoMail.organization.policy`

### Zoho CRM

- `ZohoCRM.modules.ALL` - module records CRUD
- `ZohoCRM.settings.ALL` - module metadata/settings
- `ZohoCRM.users.READ` - users
- `ZohoCRM.org.READ` - org info
- `ZohoCRM.bulk.ALL` - bulk API

### Zoho Campaigns (v1.1)

- `ZohoCampaigns.campaign.ALL` - create/send/manage campaigns (also covers campaign reports)
- `ZohoCampaigns.contact.ALL` - contacts and mailing lists
- Note: v1.1 has no separate list/template/report scopes; those resources are covered by the campaign and contact scopes above.

## 3. Create the credentials file (.env, recommended)

Create `C:\Users\<you>\.zoho-api\.env` (i.e. `~/.zoho-api/.env`):

```
ZOHO_REGION=cn
ZOHO_CLIENT_ID=<your client id>
ZOHO_CLIENT_SECRET=<your client secret>
```

`ZOHO_REGION` must match the account's data center: `cn` for accounts.zoho.com.cn, `com` for accounts.zoho.com.

The helper also supports a JSON config at `~/.zoho-api/config.json` (see `assets/config.example.json`) if preferred; the `.env` file takes priority when it exists.

## 4. Obtain a refresh token (Self Client, no local server needed)

1. In the Zoho API Console, open your **Self Client** and go to the **Generate Code** tab.
2. Paste the scopes you need (from section 2 above), e.g. for Mail:

   ```
   ZohoMail.messages.ALL,ZohoMail.folders.ALL,ZohoMail.tags.ALL,ZohoMail.accounts.READ
   ```

3. Set duration to 10 minutes, use redirect URI `http://localhost:8080/`, and click **Create**. An authorization code is displayed in the console (it is not actually sent to the redirect URI).
4. Run the exchange command (it reads client id/secret from the `.env` file and saves the refresh token back into it):

   ```
   python scripts/zoho_api.py exchange --code <paste-the-code>
   ```

   If you used a different redirect URI when generating the code, pass `--redirect-uri <same-uri>`.

The script exchanges the code at `https://accounts.zoho.com.cn/oauth/v2/token` (China) and automatically appends `ZOHO_REFRESH_TOKEN=...` to the `.env` file.

## 5. Verify

```
python scripts/zoho_api.py token
```

If it prints an access token, everything is ready.

## Troubleshooting

- **"Invalid scope(s) detected"** - scope name is wrong or scopes are not comma-separated. Use the exact scope names in section 2.
- **401 invalid token** - region mismatch, expired refresh token, or wrong scope. Re-issue the refresh token.
- **400 invalid_grant** - refresh token was revoked or client mismatch.
- **No data visible** - the Zoho account used for OAuth lacks permission on the target org/data.
