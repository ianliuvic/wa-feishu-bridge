---
name: hongxiu-weekly-product-email
description: Create Hongxiu's weekly new-product email from the 1688 collector, host it on email-campaign, create a Zoho Campaigns draft, and notify the Feishu Marketing group. Use for weekly product roundup emails; never use it to send a campaign.
---

# Hongxiu Weekly Product Email

Build one refreshable, idempotent weekly B2B product-roundup draft from collector-managed products.

## Required workflow

1. Run `python3 /root/.codex/skills/hongxiu-weekly-product-email/scripts/weekly_product_email.py check` before a live run.
2. Use `discover` to inspect the exact candidate set when the user asks for a preview or when troubleshooting.
3. Use `run` only when the user has authorized creating the hosted page, Zoho draft, and Feishu notification.
4. Report the selected week, eligible product count, hosted URL, Zoho draft key, and Feishu result.

The default reporting window is the complete seven-day interval from the previous Sunday at 09:00 through the current Sunday at 09:00 in `Asia/Shanghai`. The script treats a product as eligible only when the collector confirms all of these: its official 1688 listing timestamp is inside that half-open interval, the source listing remains active and ingestion-eligible, and its wearhongxiu WordPress publication is public. Never substitute capture time or first-seen time for the official listing timestamp.

## Safety and idempotency

- This workflow creates a Zoho Campaigns **draft only**. Never call a send or schedule endpoint.
- A campaign slug is stable per completed Sunday-to-Sunday window. Compare the newly rendered content with the hosted repository copy on every run.
- If the content is unchanged, reuse the existing Zoho campaign. If it changed and the existing campaign is still a draft, replace it safely with one refreshed draft and update the repository key. If it is sent, in progress, or scheduled, lock it and never modify or replace it.
- If the week contains no eligible products, do not create or deploy HTML and do not create a Zoho campaign; send only the no-products Feishu notification.
- Never include a recipient name, recipient-name merge tag, or personalized salutation; inaccurate contact names can damage trust. Keep `$[LI:UNSUBSCRIBE]$` in every email.
- Use the canonical footer returned by `render_footer()` in the bundled script. It must stay visually and textually aligned with `https://email.wearhongxiu.com/campaigns/2026-08-wholesale-swimwear/`: Hongxiu Clothing Co., Ltd.; `10-8A Tiexi Rd, Xingcheng, Liaoning, China`; wearhongxiu.com; service@wearhongxiu.com; WhatsApp `+86 177 1101 4152`; Privacy, Shipping, Refund, and Zoho unsubscribe links. Do not substitute an older phone number or shorten this footer.
- Keep the hosted email table-based, mobile-friendly, and limited to publicly accessible wearhongxiu image and product URLs.
- Never print tokens, OAuth secrets, API keys, or complete credential responses.

## Commands

```text
python3 /root/.codex/skills/hongxiu-weekly-product-email/scripts/weekly_product_email.py check
python3 /root/.codex/skills/hongxiu-weekly-product-email/scripts/weekly_product_email.py discover
python3 /root/.codex/skills/hongxiu-weekly-product-email/scripts/weekly_product_email.py run
```

`--window-start YYYY-MM-DD` selects a specific Sunday at 09:00 in `Asia/Shanghai`; the legacy alias `--week-start` is accepted with the same Sunday semantics. Omission selects the latest completed Sunday 09:00 cutoff. Use `--dry-run` with `run` to generate a local HTML artifact without external writes.

Read [references/configuration.md](references/configuration.md) only when configuration is missing or a connection fails.
