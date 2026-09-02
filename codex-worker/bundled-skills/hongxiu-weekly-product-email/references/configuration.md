# Configuration

The live workflow reads secrets only from the Coolify Codex Worker environment.

Required secret variables:

- `COLLECTOR_API_KEY`
- `GITHUB_TOKEN`
- `COOLIFY_API_TOKEN`
- `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`
- `FEISHU_APP_ID`, `FEISHU_APP_SECRET`

Required or defaulted routing variables:

- `COLLECTOR_API_URL` (default `https://collector.yiswim.cloud`)
- `GITHUB_API_BASE_URL` (default `https://api.github.com`)
- `EMAIL_CAMPAIGN_REPO` (default `ianliuvic/email-campaign`)
- `EMAIL_CAMPAIGN_BRANCH` (default `main`)
- `EMAIL_CAMPAIGN_BASE_URL` (default `https://email.wearhongxiu.com`)
- `COOLIFY_BASE_URL` (the API root ending in `/api/v1`)
- `EMAIL_CAMPAIGN_COOLIFY_UUID` (default `e1ps8v0988ns004bqyz330ct`)
- `ZOHO_REGION` (`cn` or `com`)
- `ZOHO_CAMPAIGNS_LIST_NAME` (default `CAM-03`)
- `ZOHO_CAMPAIGNS_TOPIC_NAME` (default `Marketing`)
- `ZOHO_CAMPAIGNS_FROM_EMAIL` (default `service@wearhongxiu.com`)
- `ZOHO_CAMPAIGNS_FROM_NAME` (default `Hongxiu Swim`)
- `FEISHU_MARKETING_CHAT_ID`

`check` reports only whether variables are configured. It must never output their values.
