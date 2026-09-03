# Zoho Campaigns API

## Base URLs and auth

- China: `https://campaigns.zoho.com.cn/api/v1.1`
- Global: `https://campaigns.zoho.com/api/v1.1`
- Header: `Authorization: Zoho-oauthtoken <access_token>` (OAuth2); legacy calls may use `authtoken` + `scope` query params.
- Scopes: `ZohoCampaigns.campaign.ALL`, `ZohoCampaigns.contact.ALL`, `ZohoCampaigns.list.ALL`, `ZohoCampaigns.template.ALL`, `ZohoCampaigns.report.ALL`.
- Official docs: https://www.zoho.com/campaigns/help/developers/

## Common endpoints

| Operation | Endpoint | Method |
|---|---|---|
| List mailing lists | `/getmailinglists` | GET |
| Create list + contacts | `/addlistandcontacts` | POST |
| List campaigns | `/campaigns` | GET |
| Create campaign | `/createCampaign` | POST |
| Send campaign | `/sendcampaign` | POST |
| Clone campaign | `/cloneCampaign` | POST |
| Campaign reports | `/campaignreports` | GET |
| Campaign summary | `/campaignsummary` | GET |
| Templates | `/templates` | GET |
| Template contents | `/templatecontents/{templateId}` | GET |

## Notes

- Campaign sending requires a target list (e.g. `mailingList` / `listKey`) and template or content; parameter names vary, verify against official docs before sending.
- Contacts can be added in bulk with `addlistandcontacts` (also supports `resubscribe`, `unsubscription` options).
- Reports endpoints return opens/clicks/bounces per campaign.
- Response format is JSON for API v1.1 (XML available on request).
