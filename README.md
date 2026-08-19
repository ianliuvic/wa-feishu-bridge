# WA-Feishu Bridge

Replaces the old n8n workflow `whatsapp接收消息发送到飞书`:

```
WhatsApp  →  Evolution API (webhook MESSAGES_UPSERT)  →  this service  →  Feishu group chat
```

## Behavior

- `POST /webhook/evolution` — Evolution webhook receiver.
  - Ignores events other than `messages.upsert`.
  - Drops own messages (`data.key.fromMe == true`).
  - Extracts `pushName`, `remoteJid`, message text (conversation / extendedTextMessage),
    instance name; formats a readable notice and sends it to the configured Feishu chat.
  - Keeps `WA_NUMBER:` / `INSTANCE:` lines in the message so a future
    Feishu → WhatsApp reply handler can parse them.
- `GET /health` — liveness check.

## Env vars

| Variable | Required | Description |
|---|---|---|
| `FEISHU_APP_ID` | yes | Feishu bot app id (`cli_...`) |
| `FEISHU_APP_SECRET` | yes | Feishu bot app secret |
| `FEISHU_CHAT_ID` | yes | Target group chat id (`oc_...`) |
| `FEISHU_API_BASE` | no | Default `https://open.feishu.cn` |
| `EVOLUTION_BASE_URL` | no | Reserved for future LLM auto-reply |
| `EVOLUTION_API_KEY` | no | Reserved for future LLM auto-reply |
| `DEEPSEEK_API_KEY` | no | DeepSeek API key (OpenAI-compatible). Capability only — not used by the forwarding flow yet |
| `DEEPSEEK_BASE_URL` | no | Default `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | no | Default `deepseek-v4-flash` |

## Deploy

Docker image listens on port `8000`:

```
docker build -t wa-feishu-bridge .
docker run -p 8000:8000 \
  -e FEISHU_APP_ID=... -e FEISHU_APP_SECRET=... -e FEISHU_CHAT_ID=... \
  wa-feishu-bridge
```

Deployed on Coolify (project `N8N`, environment `test`) at
`https://wa-bridge.yiswim.cloud`; the Evolution instance webhook points to
`https://wa-bridge.yiswim.cloud/webhook/evolution`.
