# GA4 reporting

Use this reference for Wearhongxiu traffic, engagement, content, acquisition,
lead-action and Shopify funnel analysis. The integration is read-only and is
hard-locked to GA4 property `512505206`.

## Site separation

The property contains two websites. Always use `hostName` as the first
dimension or an exact hostname filter:

- `wearhongxiu.com` = WordPress manufacturing and lead-generation website.
- `shop.wearhongxiu.com` = Shopify storefront.

Never combine the two sites into one performance conclusion. Report a property
total only as context, then provide separate WordPress and Shopify sections.
Cross-site movement belongs in its own section using landing pages, referrers
and relevant events.

## Commands

```powershell
node scripts/ga4.mjs info
node scripts/ga4.mjs metadata --output C:\path\ga4-metadata.json
node scripts/ga4.mjs report --dimensions hostName,sessionDefaultChannelGroup --metrics sessions,activeUsers,keyEvents --days 28
node scripts/ga4.mjs report --dimensions pagePathPlusQueryString --metrics screenPageViews,activeUsers,userEngagementDuration --hostname wearhongxiu.com --days 28
node scripts/ga4.mjs realtime --hostname wearhongxiu.com
node scripts/ga4.mjs full-report --days 28 --output C:\path\ga4-full-report.json
```

`full-report` collects current and previous equal-length periods for hostname
overview, daily trend, channel, source/medium, landing pages, content pages,
events, devices, countries, referrers, WordPress post types and ecommerce
items. A section-level compatibility error is recorded without discarding the
rest of the report. Generic `report` supports any compatible standard or custom
dimension/metric returned by `metadata`, up to 250,000 rows per request.

## Owner report

For a complete management report, analyze at least these questions:

1. Is WordPress attracting qualified B2B manufacturer traffic, and which
   landing pages, content clusters and channels create lead actions?
2. Do inquiry actions form a coherent funnel from product/service view to form
   start, quote open, WhatsApp/email click and successful submit?
3. Which articles create engaged visits and movement toward service, product,
   sample or quote pages, rather than pageviews alone?
4. Is Shopify attracting product shoppers, and where do users drop between
   view item, add to cart, checkout and purchase?
5. How much useful movement occurs from WordPress to Shopify and back?
6. Which countries and devices produce engagement or key events, not just
   sessions?
7. Which traffic sources are growing, declining, low quality or over-dependent
   on brand/direct traffic?
8. Are key events configured consistently? Flag business actions generating
   events but zero `keyEvents`, duplicate lead events, missing purchase revenue,
   self-referrals, internal traffic and suspicious bot-like patterns.

Compare the current period with the immediately preceding equal period. Give
absolute values and percentage change, but avoid percentage claims when the
baseline is too small. Separate observations from hypotheses. Recommend the
smallest actions that could improve content, acquisition, conversion tracking
or funnel performance. GA4 cannot prove lead quality or revenue unless those
outcomes are instrumented.

GA4 reporting is read-only. Do not modify events, key events, audiences,
streams, links, retention or attribution settings without a separate explicit
request and a supported Admin API workflow.
