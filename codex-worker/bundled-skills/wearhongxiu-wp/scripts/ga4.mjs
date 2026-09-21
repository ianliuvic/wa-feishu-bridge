#!/usr/bin/env node
/** Read-only GA4 reporting for the Wearhongxiu property. */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PROPERTY_ID = "512505206";
const HOSTS = new Set(["wearhongxiu.com", "shop.wearhongxiu.com"]);
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const skillDir = path.dirname(scriptDir);
loadEnv(path.join(skillDir, "config.env"));

function loadEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const [key, ...rest] = line.split("=");
    if (!(key in process.env)) process.env[key] = rest.join("=").trim().replace(/^['"]|['"]$/g, "");
  }
}

function parseArgs(argv) {
  const [command = "help", ...rest] = argv;
  const flags = {};
  for (let index = 0; index < rest.length; index += 1) {
    if (rest[index].startsWith("--")) flags[rest[index].slice(2)] = rest[++index];
  }
  return { command, flags };
}

function help() {
  console.log(`Wearhongxiu GA4 (read-only; property ${PROPERTY_ID})

node scripts/ga4.mjs info
node scripts/ga4.mjs metadata [--output FILE]
node scripts/ga4.mjs report --dimensions hostName,pagePath --metrics sessions,activeUsers [--days 28] [--hostname wearhongxiu.com] [--limit 10000] [--output FILE]
node scripts/ga4.mjs realtime [--hostname wearhongxiu.com] [--output FILE]
node scripts/ga4.mjs full-report [--days 28] [--output FILE]

Only wearhongxiu.com and shop.wearhongxiu.com host filters are accepted.`);
}

function credentialPath() {
  const configured = process.env.WEARHONGXIU_GA4_CREDENTIALS || process.env.WEARHONGXIU_GSC_CREDENTIALS || process.env.GOOGLE_APPLICATION_CREDENTIALS;
  if (configured) {
    const resolved = path.resolve(configured);
    if (!fs.existsSync(resolved)) throw new Error(`Configured GA4 credential does not exist: ${resolved}`);
    return resolved;
  }
  const legacy = "E:\\cc\\wearhongxiu\\wordpress\\codex-wp-rest-connector";
  if (fs.existsSync(legacy)) {
    for (const name of fs.readdirSync(legacy).filter((item) => item.endsWith(".json"))) {
      const file = path.join(legacy, name);
      try {
        const data = JSON.parse(fs.readFileSync(file, "utf8"));
        if (data.type === "service_account" && data.client_email && data.private_key) return file;
      } catch {}
    }
  }
  throw new Error("GA4 service account is not configured");
}

function account() {
  const file = credentialPath();
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  if (data.type !== "service_account" || !data.client_email || !data.private_key) throw new Error("Invalid GA4 service account");
  return { ...data, __path: file };
}

const b64 = (value) => Buffer.from(value).toString("base64url");
let cachedToken = null;
async function token() {
  if (cachedToken && Date.now() < cachedToken.expires - 60_000) return cachedToken.value;
  const service = account(), now = Math.floor(Date.now() / 1000);
  const input = `${b64(JSON.stringify({ alg: "RS256", typ: "JWT" }))}.${b64(JSON.stringify({
    iss: service.client_email,
    scope: "https://www.googleapis.com/auth/analytics.readonly",
    aud: service.token_uri || "https://oauth2.googleapis.com/token",
    iat: now,
    exp: now + 3600,
  }))}`;
  const assertion = `${input}.${crypto.sign("RSA-SHA256", Buffer.from(input), service.private_key).toString("base64url")}`;
  const response = await fetch(service.token_uri || "https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer", assertion }),
    signal: AbortSignal.timeout(30_000),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(`Google OAuth failed (${response.status}): ${data.error_description || data.error}`);
  cachedToken = { value: data.access_token, expires: Date.now() + Number(data.expires_in || 3600) * 1000 };
  return cachedToken.value;
}

async function google(url, { method = "GET", body } = {}) {
  const response = await fetch(url, {
    method,
    headers: { Authorization: `Bearer ${await token()}`, Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(90_000),
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(`GA4 request failed (${response.status}): ${data?.error?.message || response.statusText}`);
  return data;
}

function hostnameFilter(hostname) {
  if (!hostname) return undefined;
  if (!HOSTS.has(hostname)) throw new Error(`Unsupported hostname: ${hostname}`);
  return { filter: { fieldName: "hostName", stringFilter: { matchType: "EXACT", value: hostname, caseSensitive: false } } };
}

function rows(data) {
  const dimensions = (data.dimensionHeaders || []).map((item) => item.name);
  const metrics = (data.metricHeaders || []).map((item) => item.name);
  return (data.rows || []).map((row) => Object.fromEntries([
    ...dimensions.map((name, index) => [name, row.dimensionValues?.[index]?.value || ""]),
    ...metrics.map((name, index) => [name, Number(row.metricValues?.[index]?.value || 0)]),
  ]));
}

async function runReport({ dimensions, metrics, startDate, endDate, hostname, limit = 10_000, offset = 0 }) {
  const body = {
    dateRanges: [{ startDate, endDate, name: "report_period" }],
    dimensions: dimensions.map((name) => ({ name })),
    metrics: metrics.map((name) => ({ name })),
    limit: String(Math.min(250_000, Math.max(1, Number(limit)))),
    offset: String(Math.max(0, Number(offset))),
    keepEmptyRows: false,
    returnPropertyQuota: true,
  };
  const filter = hostnameFilter(hostname);
  if (filter) body.dimensionFilter = filter;
  const data = await google(`https://analyticsdata.googleapis.com/v1beta/properties/${PROPERTY_ID}:runReport`, { method: "POST", body });
  return { row_count: Number(data.rowCount || 0), sampled: Boolean(data.metadata?.samplingMetadatas?.length), rows: rows(data), property_quota: data.propertyQuota || {} };
}

async function realtime(hostname) {
  const body = {
    dimensions: [{ name: "hostName" }, { name: "unifiedScreenName" }, { name: "country" }],
    metrics: [{ name: "activeUsers" }, { name: "screenPageViews" }, { name: "eventCount" }],
    limit: "1000",
    returnPropertyQuota: true,
  };
  const filter = hostnameFilter(hostname);
  if (filter) body.dimensionFilter = filter;
  const data = await google(`https://analyticsdata.googleapis.com/v1beta/properties/${PROPERTY_ID}:runRealtimeReport`, { method: "POST", body });
  return { row_count: Number(data.rowCount || 0), rows: rows(data), property_quota: data.propertyQuota || {} };
}

const csv = (value) => String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
const iso = (date) => date.toISOString().slice(0, 10);
function periods(days) {
  const end = new Date(); end.setUTCDate(end.getUTCDate() - 1);
  const start = new Date(end); start.setUTCDate(start.getUTCDate() - days + 1);
  const previousEnd = new Date(start); previousEnd.setUTCDate(previousEnd.getUTCDate() - 1);
  const previousStart = new Date(previousEnd); previousStart.setUTCDate(previousStart.getUTCDate() - days + 1);
  return { current: { start_date: iso(start), end_date: iso(end) }, previous: { start_date: iso(previousStart), end_date: iso(previousEnd) } };
}

const sections = [
  { key: "overview_by_host", dimensions: ["hostName"], metrics: ["sessions", "activeUsers", "newUsers", "screenPageViews", "engagedSessions", "engagementRate", "averageSessionDuration", "keyEvents", "totalRevenue"] },
  { key: "daily_trend", dimensions: ["date", "hostName"], metrics: ["sessions", "activeUsers", "screenPageViews", "engagedSessions", "keyEvents"] },
  { key: "channel_acquisition", dimensions: ["hostName", "sessionDefaultChannelGroup"], metrics: ["sessions", "activeUsers", "newUsers", "engagedSessions", "engagementRate", "keyEvents"] },
  { key: "source_medium", dimensions: ["hostName", "sessionSourceMedium"], metrics: ["sessions", "activeUsers", "newUsers", "engagedSessions", "keyEvents"] },
  { key: "landing_pages", dimensions: ["hostName", "landingPagePlusQueryString"], metrics: ["sessions", "activeUsers", "newUsers", "engagedSessions", "engagementRate", "averageSessionDuration", "keyEvents"] },
  { key: "content_pages", dimensions: ["hostName", "pagePathPlusQueryString", "pageTitle"], metrics: ["screenPageViews", "activeUsers", "userEngagementDuration", "eventCount", "keyEvents"] },
  { key: "events", dimensions: ["hostName", "eventName"], metrics: ["eventCount", "totalUsers", "keyEvents"] },
  { key: "devices", dimensions: ["hostName", "deviceCategory"], metrics: ["sessions", "activeUsers", "engagedSessions", "engagementRate", "keyEvents"] },
  { key: "countries", dimensions: ["hostName", "country"], metrics: ["sessions", "activeUsers", "newUsers", "engagedSessions", "keyEvents"] },
  { key: "referrers", dimensions: ["hostName", "pageReferrer"], metrics: ["screenPageViews", "activeUsers", "eventCount"] },
  { key: "wordpress_post_types", dimensions: ["hostName", "customEvent:googlesitekit_post_type"], metrics: ["screenPageViews", "activeUsers", "userEngagementDuration", "keyEvents"] },
  { key: "ecommerce_items", dimensions: ["hostName", "itemName"], metrics: ["itemsViewed", "itemsAddedToCart", "itemsPurchased", "itemRevenue"] },
];

async function fullReport(days) {
  const range = periods(days), output = {
    generated_at: new Date().toISOString(), property_id: PROPERTY_ID, property_name: "wearhongxiu.com",
    timezone: "Asia/Shanghai", currency: "USD",
    site_mapping: { "wearhongxiu.com": "WordPress", "shop.wearhongxiu.com": "Shopify" },
    periods: range, sections: {},
  };
  const tasks = [];
  for (const section of sections) {
    output.sections[section.key] = {};
    for (const [periodName, period] of Object.entries(range)) tasks.push({ section, periodName, period });
  }
  let cursor = 0;
  await Promise.all(Array.from({ length: 4 }, async () => {
    while (cursor < tasks.length) {
      const task = tasks[cursor++], { section, periodName, period } = task;
      try {
        output.sections[section.key][periodName] = await runReport({ dimensions: section.dimensions, metrics: section.metrics, startDate: period.start_date, endDate: period.end_date, limit: section.key === "content_pages" || section.key === "events" ? 25_000 : 10_000 });
      } catch (error) {
        output.sections[section.key][periodName] = { error: error.message, rows: [] };
      }
    }
  }));
  return output;
}

function save(value, file) {
  const text = `${JSON.stringify(value, null, 2)}\n`;
  if (file) {
    const resolved = path.resolve(file);
    fs.mkdirSync(path.dirname(resolved), { recursive: true });
    fs.writeFileSync(resolved, text, "utf8");
    console.log(JSON.stringify({ saved: resolved, property_id: PROPERTY_ID, generated_at: value.generated_at || new Date().toISOString() }, null, 2));
  } else process.stdout.write(text);
}

async function main() {
  const { command, flags } = parseArgs(process.argv.slice(2));
  if (flags.property && String(flags.property) !== PROPERTY_ID) throw new Error(`This skill is locked to GA4 property ${PROPERTY_ID}`);
  if (command === "help") return help();
  if (command === "info") { const service = account(); return save({ configured: true, property_id: PROPERTY_ID, property_name: "wearhongxiu.com", timezone: "Asia/Shanghai", currency: "USD", allowed_hosts: [...HOSTS], credential_file: service.__path, service_account_email: service.client_email }, flags.output); }
  if (command === "metadata") return save(await google(`https://analyticsdata.googleapis.com/v1beta/properties/${PROPERTY_ID}/metadata`), flags.output);
  if (command === "report") {
    const dimensions = csv(flags.dimensions), metrics = csv(flags.metrics);
    if (!dimensions.length || !metrics.length) throw new Error("report requires --dimensions and --metrics");
    const days = Math.min(480, Math.max(1, Number(flags.days || 28))), range = periods(days).current;
    return save(await runReport({ dimensions, metrics, startDate: flags["start-date"] || range.start_date, endDate: flags["end-date"] || range.end_date, hostname: flags.hostname, limit: flags.limit || 10_000, offset: flags.offset || 0 }), flags.output);
  }
  if (command === "realtime") return save(await realtime(flags.hostname), flags.output);
  if (command === "full-report") return save(await fullReport(Math.min(480, Math.max(1, Number(flags.days || 28)))), flags.output);
  throw new Error(`Unknown command: ${command}`);
}

main().catch((error) => { console.error(`error: ${error.message}`); process.exit(1); });
