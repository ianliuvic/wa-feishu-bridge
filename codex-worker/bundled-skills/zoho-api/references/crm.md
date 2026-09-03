# Zoho CRM API

## Base URLs and auth

- China: `https://www.zohoapis.com.cn/crm/v2`
- Global: `https://www.zohoapis.com/crm/v2`
- Header: `Authorization: Zoho-oauthtoken <access_token>`
- Scopes: `ZohoCRM.modules.ALL` for record CRUD; `ZohoCRM.settings.ALL`, `ZohoCRM.users.READ`, `ZohoCRM.org.READ`, `ZohoCRM.bulk.ALL` as needed.
- Official docs: https://www.zoho.com/crm/developer/docs/api/v2/

## Common module names

`Leads`, `Accounts`, `Contacts`, `Deals`, `Campaigns`, `Tasks`, `Cases`, `Events`, `Calls`, `Solutions`, `Products`, `Quotes`, `Sales_Orders`, `Purchase_Orders`, `Invoices`, plus custom modules.

## Core endpoints

| Operation | Endpoint | Method |
|---|---|---|
| List records | `/crm/v2/{module}` | GET |
| Create records | `/crm/v2/{module}` | POST |
| Get record | `/crm/v2/{module}/{id}` | GET |
| Update record | `/crm/v2/{module}/{id}` | PUT |
| Delete record | `/crm/v2/{module}/{id}` | DELETE |
| Search records | `/crm/v2/{module}/search?criteria=...` | GET |
| Module metadata | `/crm/v2/settings/modules` | GET |
| Bulk read/write | `/crm/bulk/v2/{module}/read` / `/write` | POST (async) |
| Users | `/crm/v2/users?type=ActiveUsers` | GET |

## Notes

- Request body shape: `{"data": [{field_API_name: value, ...}]}`; use API names, not display labels.
- List/search support query params such as `per_page`, `page`, `fields`, `sort_order`, `criteria`.
- Bulk operations are asynchronous: submit the job, poll `GET /crm/bulk/v2/{module}/read/{job_id}`, then download results from the returned URL.
- Respect per-API-call and daily rate limits; throttle batch loops.
