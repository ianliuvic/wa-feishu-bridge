---
name: hongxiu-weekly-product-email
description: Create Hongxiu's weekly new-product email from the 1688 collector, host it on email-campaign, create a Zoho Campaigns draft, and notify the Feishu Marketing group. Use for weekly product roundup emails; never use it to send a campaign.
---

# Hongxiu Weekly Product Email

Build one idempotent weekly B2B product-roundup draft from collector-managed products.

## Required workflow

1. Run `python3 /root/.codex/skills/hongxiu-weekly-product-email/scripts/weekly_product_email.py check` before a live run.
2. Use `discover` to inspect the exact candidate set when the user asks for a preview or when troubleshooting.
3. Use `run` only when the user has authorized creating the hosted page, Zoho draft, and Feishu notification.
4. Report the selected week, eligible product count, hosted URL, Zoho draft key, and Feishu result.

The script treats a product as eligible only when the collector confirms all of these: its official 1688 listing timestamp is inside the requested week, the source listing remains active and ingestion-eligible, and its wearhongxiu WordPress publication is public. Never substitute capture time or first-seen time for the official listing timestamp.

## Safety and idempotency

- This workflow creates a Zoho Campaigns **draft only**. Never call a send or schedule endpoint.
- A campaign slug is stable per ISO week. If the repository already records a Zoho draft key for that week, do not create another draft or rewrite its imported content.
- If the week contains no eligible products, do not create or deploy HTML and do not create a Zoho campaign; send only the no-products Feishu notification.
- Keep `$[FNAME|friend]$` and `$[LI:UNSUBSCRIBE]$` in every email.
- Use the canonical footer returned by `render_footer()` in the bundled script. It must stay visually and textually aligned with `https://email.wearhongxiu.com/campaigns/2026-08-wholesale-swimwear/`: Hongxiu Clothing Co., Ltd.; `10-8A Tiexi Rd, Xingcheng, Liaoning, China`; wearhongxiu.com; service@wearhongxiu.com; WhatsApp `+86 177 1101 4152`; Privacy, Shipping, Refund, and Zoho unsubscribe links. Do not substitute an older phone number or shorten this footer.
- Keep the hosted email table-based, mobile-friendly, and limited to publicly accessible wearhongxiu image and product URLs.
- Never print tokens, OAuth secrets, API keys, or complete credential responses.

## Commands

```text
python3 /root/.codex/skills/hongxiu-weekly-product-email/scripts/weekly_product_email.py check
python3 /root/.codex/skills/hongxiu-weekly-product-email/scripts/weekly_product_email.py discover
python3 /root/.codex/skills/hongxiu-weekly-product-email/scripts/weekly_product_email.py run
```

`--week-start YYYY-MM-DD` selects a specific Monday in `Asia/Shanghai`; omission selects the current Shanghai week. Use `--dry-run` with `run` to generate a local HTML artifact without external writes.

Read [references/configuration.md](references/configuration.md) only when configuration is missing or a connection fails.
