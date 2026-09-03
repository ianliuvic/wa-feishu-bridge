# Zoho Mail API

## Base URLs and auth

- China: `https://mail.zoho.com.cn/api`
- Global: `https://mail.zoho.com/api`
- Header: `Authorization: Zoho-oauthtoken <access_token>`
- Scopes: see `references/setup.md` (user scopes `ZohoMail.messages.ALL`, `ZohoMail.folders.ALL`, `ZohoMail.labels.ALL`, `ZohoMail.tasks.ALL`, `ZohoMail.accounts.READ`, `ZohoMail.settings.ALL`; admin scopes for org-level operations).
- Official docs: https://www.zoho.com/mail/help/api/overview.html

## User-level endpoints

First call `GET /api/accounts` to get the user's `accountId`, then use it in paths.

| Operation | Endpoint | Method |
|---|---|---|
| List accounts | `/api/accounts` | GET |
| Account details | `/api/accounts/{accountId}` | GET |
| List folders | `/api/accounts/{accountId}/folders` | GET |
| Folder details | `/api/accounts/{accountId}/folders/{folderId}` | GET |
| Create folder | `/api/accounts/{accountId}/folders` | POST |
| Rename/move folder | `/api/accounts/{accountId}/folders/{folderId}` | PUT |
| Delete folder | `/api/accounts/{accountId}/folders/{folderId}` | DELETE |
| List messages in folder | `/api/accounts/{accountId}/messages/view?folderId={folderId}` | GET |
| Message details (body) | `/api/accounts/{accountId}/messages/{messageId}` | GET |
| Search messages | `/api/accounts/{accountId}/messages/search?searchKey=...` | GET |
| Send email | `/api/accounts/{accountId}/messages` | POST |
| Move messages | `/api/accounts/{accountId}/messages/move` | PUT |
| Delete message | `/api/accounts/{accountId}/messages/{messageId}` | DELETE |
| Archive / unarchive | `/api/accounts/{accountId}/messages/archive` (or `/unarchive`) | PUT |
| List labels | `/api/accounts/{accountId}/labels` | GET |
| Create label | `/api/accounts/{accountId}/labels` | POST |
| Delete label | `/api/accounts/{accountId}/labels/{labelId}` | DELETE |
| Apply/remove labels | `/api/accounts/{accountId}/messages/applyLabels` (or `/removeLabels`) | POST |
| Vacation reply | `/api/accounts/{accountId}/settings/vacation` | GET/POST/PUT/DELETE |
| Email forwarding | `/api/accounts/{accountId}/settings/forward` | GET/POST/PUT/DELETE |
| Personal/group tasks | `/api/accounts/{accountId}/tasks` | GET/POST |
| Task update/delete | `/api/accounts/{accountId}/tasks/{taskId}` | PUT/DELETE |

## Admin-level endpoints (org admins)

| Operation | Endpoint |
|---|---|
| Org details | `GET /api/organization/{zoid}` |
| List/add users | `GET /api/organization/{zoid}/users` / `POST /api/organization/{zoid}/users` |
| Modify/disable/delete user | `/api/organization/{zoid}/users/{userId}` (PUT/DELETE) |
| Domains | `/api/organization/{zoid}/domains` (GET/POST) |
| Groups | `/api/organization/{zoid}/groups` (GET/POST) |
| Policies | `/api/organization/{zoid}/policy` (GET/POST/PUT) |

## Notes

- Send email body fields include `fromAddress`, `toAddress`, `subject`, `content`, `mailFormat` (`html` or `plaintext`); attachments require `attachmentName`/`attachmentContent` pairs or `attachments` array (see official docs).
- Move requires `messageId` or `threadId`, plus `fromFolderId` and `toFolderId`.
- Admin endpoints use org-level scopes and only work for users with admin privileges in the org.
- Parameter and field names vary by API version; verify against the official Mail API docs before constructing requests.
