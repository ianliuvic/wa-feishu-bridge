#!/usr/bin/env node
/** Wearhongxiu Google Search Console CLI. Node.js standard library only. */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execFileAsync = promisify(execFile);
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const skillDir = path.dirname(scriptDir);
const cacheDir = path.join(skillDir, "cache");
const cacheFile = path.join(cacheDir, "gsc-index-cache.json");
const legacyDir = "E:\\cc\\wearhongxiu\\wordpress\\codex-wp-rest-connector";
const legacyCache = path.join(legacyDir, "gsc-index-cache.json");
const hosts = new Set(["wearhongxiu.com", "www.wearhongxiu.com"]);
const apiBase = "https://www.googleapis.com/webmasters/v3";
const inspectionApi = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect";
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

function args(argv) {
  const [command = "help", ...rest] = argv;
  const flags = {}, positional = [];
  for (let i = 0; i < rest.length; i += 1) {
    if (!rest[i].startsWith("--")) positional.push(rest[i]);
    else {
      const key = rest[i].slice(2);
      flags[key] = ["yes", "refresh", "details"].includes(key) ? true : rest[++i];
    }
  }
  return { command, flags, positional };
}

const output = (value) => process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
const parseJson = (text, label) => { try { return JSON.parse(text); } catch { throw new Error(`${label} returned invalid JSON.`); } };
const base64Url = (value) => Buffer.from(value).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");

function help() {
  console.log(`Wearhongxiu Google Search Console

node scripts/gsc.mjs info
node scripts/gsc.mjs sites
node scripts/gsc.mjs overview [--days 28]
node scripts/gsc.mjs performance [--days 28] [--dimensions page,query] [--limit 1000] [--page URL] [--query TEXT]
node scripts/gsc.mjs sitemap
node scripts/gsc.mjs inspect URL
node scripts/gsc.mjs audit [--max-age-days 7] [--max-refresh 200] [--workers 3] [--refresh] [--details]
node scripts/gsc.mjs cache [--details]
node scripts/gsc.mjs submit-sitemap --yes`);
}

function publicUrl(value) {
  const url = new URL(value);
  if (url.protocol !== "https:" || !hosts.has(url.hostname)) throw new Error(`Refusing non-Wearhongxiu URL: ${value}`);
  return url.toString();
}

function siteUrl() {
  const value = process.env.WEARHONGXIU_GSC_SITE_URL || "https://wearhongxiu.com/";
  if (value === "sc-domain:wearhongxiu.com") return value;
  const checked = publicUrl(value);
  return checked.endsWith("/") ? checked : `${checked}/`;
}
const sitemapUrl = () => publicUrl(process.env.WEARHONGXIU_GSC_SITEMAP_URL || "https://wearhongxiu.com/sitemap_index.xml");

function credentialPath() {
  const configured = process.env.WEARHONGXIU_GSC_CREDENTIALS || process.env.GOOGLE_APPLICATION_CREDENTIALS || process.env.GSC_SERVICE_ACCOUNT_FILE;
  if (configured) {
    const resolved = path.resolve(configured);
    if (!fs.existsSync(resolved)) throw new Error(`Configured GSC credential does not exist: ${resolved}`);
    return resolved;
  }
  if (fs.existsSync(legacyDir)) {
    for (const name of fs.readdirSync(legacyDir).filter((item) => item.endsWith(".json"))) {
      const file = path.join(legacyDir, name);
      try {
        const data = JSON.parse(fs.readFileSync(file, "utf8"));
        if (data.type === "service_account" && data.client_email && data.private_key) return file;
      } catch {}
    }
  }
  throw new Error("GSC service account not found. Set WEARHONGXIU_GSC_CREDENTIALS in config.env.");
}

function account() {
  const file = credentialPath();
  const data = parseJson(fs.readFileSync(file, "utf8"), "GSC credential");
  if (data.type !== "service_account" || !data.client_email || !data.private_key) throw new Error("Invalid GSC service account.");
  return { ...data, __path: file };
}

const tokens = new Map();
async function token(scope) {
  const cached = tokens.get(scope);
  if (cached && Date.now() < cached.expires - 60_000) return cached.value;
  const service = account(), now = Math.floor(Date.now() / 1000);
  const header = base64Url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const payload = base64Url(JSON.stringify({ iss: service.client_email, scope, aud: service.token_uri || "https://oauth2.googleapis.com/token", iat: now, exp: now + 3600 }));
  const input = `${header}.${payload}`;
  const assertion = `${input}.${crypto.sign("RSA-SHA256", Buffer.from(input), service.private_key).toString("base64url")}`;
  const response = await http(service.token_uri || "https://oauth2.googleapis.com/token", {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
    body: new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer", assertion }).toString(),
  });
  const data = parseJson(response.text, "Google OAuth");
  if (!response.ok) throw new Error(`Google OAuth failed (${response.status}): ${data.error_description || data.error || response.statusText}`);
  tokens.set(scope, { value: data.access_token, expires: Date.now() + Number(data.expires_in || 3600) * 1000 });
  return data.access_token;
}

async function google(url, { method = "GET", body, write = false } = {}) {
  const access = await token(write ? "https://www.googleapis.com/auth/webmasters" : "https://www.googleapis.com/auth/webmasters.readonly");
  const response = await http(url, { method, headers: { Authorization: `Bearer ${access}`, Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) }, body: body ? JSON.stringify(body) : undefined });
  const data = response.text ? parseJson(response.text, "GSC") : {};
  if (!response.ok) throw new Error(`GSC request failed (${response.status}): ${data?.error?.message || response.statusText}`);
  return data;
}

async function http(url, options = {}) {
  if (process.platform === "win32" && /^https:\/\/(www\.googleapis\.com|searchconsole\.googleapis\.com|oauth2\.googleapis\.com)\//i.test(url)) return powershellHttp(url, options);
  const response = await fetch(url, { method: options.method || "GET", headers: options.headers || {}, body: options.body, signal: AbortSignal.timeout(options.timeout || 30_000) });
  return { ok: response.ok, status: response.status, statusText: response.statusText, text: await response.text() };
}

async function powershellHttp(url, options) {
  const script = `$ErrorActionPreference='Stop';$h=@{};if($env:HX_HEADERS){$o=$env:HX_HEADERS|ConvertFrom-Json;foreach($p in $o.PSObject.Properties){$h[$p.Name]=[string]$p.Value}};$r=[ordered]@{ok=$false;status=0;statusText='';text=''};try{$p=@{Uri=$env:HX_URL;Method=$env:HX_METHOD;Headers=$h;TimeoutSec=30;UseBasicParsing=$true};if($env:HX_BODY_SET-eq'1'){$p.Body=$env:HX_BODY};$x=Invoke-WebRequest @p;$r.ok=([int]$x.StatusCode-ge 200-and[int]$x.StatusCode-lt 300);$r.status=[int]$x.StatusCode;$r.statusText=[string]$x.StatusDescription;$r.text=[string]$x.Content}catch{$r.statusText=[string]$_.Exception.Message;if($_.Exception.Response){try{$r.status=[int]$_.Exception.Response.StatusCode;$s=$_.Exception.Response.GetResponseStream();if($s){$q=[IO.StreamReader]::new($s);$r.text=$q.ReadToEnd()}}catch{}}};$r|ConvertTo-Json -Compress -Depth 5`;
  const { stdout } = await execFileAsync("powershell", ["-NoProfile", "-Command", script], {
    timeout: 35_000, maxBuffer: 20 * 1024 * 1024,
    env: { ...process.env, HX_URL: url, HX_METHOD: options.method || "GET", HX_HEADERS: JSON.stringify(options.headers || {}), HX_BODY_SET: options.body === undefined ? "0" : "1", HX_BODY: options.body === undefined ? "" : String(options.body) },
  });
  const result = parseJson(stdout, "PowerShell HTTP");
  return { ok: Boolean(result.ok), status: Number(result.status || 0), statusText: result.statusText || "", text: result.text || "" };
}

function range(days) {
  const count = Math.max(1, Math.min(Number(days || 28), 480));
  const end = new Date(); end.setUTCDate(end.getUTCDate() - 2);
  const start = new Date(end); start.setUTCDate(start.getUTCDate() - count + 1);
  return { startDate: start.toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10) };
}

async function performance(flags) {
  const dates = range(flags.days);
  const allowed = new Set(["date", "query", "page", "country", "device", "searchAppearance"]);
  const dimensions = String(flags.dimensions || "page").split(",").map((item) => item.trim()).filter(Boolean);
  if (!dimensions.length || dimensions.some((item) => !allowed.has(item))) throw new Error("Unsupported Search Analytics dimension.");
  const filters = [];
  if (flags.page) filters.push({ dimension: "page", operator: "equals", expression: publicUrl(flags.page) });
  if (flags.query) filters.push({ dimension: "query", operator: "contains", expression: flags.query });
  const body = { ...dates, dimensions, rowLimit: Math.max(1, Math.min(Number(flags.limit || 1000), 25_000)), ...(filters.length ? { dimensionFilterGroups: [{ groupType: "and", filters }] } : {}) };
  const data = await google(`${apiBase}/sites/${encodeURIComponent(siteUrl())}/searchAnalytics/query`, { method: "POST", body });
  const rows = data.rows || [], clicks = rows.reduce((n, row) => n + Number(row.clicks || 0), 0), impressions = rows.reduce((n, row) => n + Number(row.impressions || 0), 0);
  return { site_url: siteUrl(), date_range: { start_date: dates.startDate, end_date: dates.endDate, note: "Latest two days excluded because GSC data is delayed." }, dimensions, row_count: rows.length, summary_scope: "returned_rows", summary: { clicks, impressions, ctr: impressions ? clicks / impressions : 0, weighted_average_position: impressions ? rows.reduce((n, row) => n + Number(row.position || 0) * Number(row.impressions || 0), 0) / impressions : 0 }, rows };
}

async function inspect(value) {
  const url = publicUrl(value);
  const data = await google(inspectionApi, { method: "POST", body: { inspectionUrl: url, siteUrl: siteUrl() } });
  const result = data.inspectionResult || {}, index = result.indexStatusResult || {}, status = index.coverageState || index.verdict || "Unknown";
  return { url, status, verdict: index.verdict || "", indexed: index.verdict === "PASS" || (/indexed/i.test(status) && !/not indexed|excluded/i.test(status)), robots: index.robotsTxtState || "", indexing: index.indexingState || "", page_fetch: index.pageFetchState || "", last_crawl_time: index.lastCrawlTime || "", crawled_as: index.crawledAs || "", google_canonical: index.googleCanonical || "", user_canonical: index.userCanonical || "", canonical_mismatch: Boolean(index.googleCanonical && index.userCanonical && normalize(index.googleCanonical) !== normalize(index.userCanonical)), sitemap: index.sitemap || [], referring_urls: index.referringUrls || [], mobile_usability: result.mobileUsabilityResult?.verdict || "", rich_results: result.richResultsResult?.verdict || "", inspection_link: result.inspectionResultLink || "", inspected_at: new Date().toISOString() };
}

const normalize = (value) => { try { const url = new URL(value); url.hash = ""; return url.toString().replace(/\/$/, ""); } catch { return String(value || ""); } };
const sitemapStatus = () => google(`${apiBase}/sites/${encodeURIComponent(siteUrl())}/sitemaps/${encodeURIComponent(sitemapUrl())}`);

async function submit(flags) {
  if (!flags.yes) throw new Error("Sitemap submission changes GSC state. Re-run with --yes only after explicit authorization.");
  await google(`${apiBase}/sites/${encodeURIComponent(siteUrl())}/sitemaps/${encodeURIComponent(sitemapUrl())}`, { method: "PUT", write: true });
  return { submitted: true, site_url: siteUrl(), sitemap_url: sitemapUrl(), submitted_at: new Date().toISOString() };
}

function readCache() {
  const source = fs.existsSync(cacheFile) ? cacheFile : (fs.existsSync(legacyCache) ? legacyCache : "");
  if (!source) return { version: 1, updated_at: "", sitemap_url: sitemapUrl(), urls: {}, source: cacheFile };
  const data = parseJson(fs.readFileSync(source, "utf8"), "GSC cache");
  return { version: 1, updated_at: data.updated_at || "", sitemap_url: data.sitemap_url || sitemapUrl(), urls: data.urls || {}, source };
}

function cacheSummary(cache, details) {
  const records = Object.values(cache.urls || {}).filter((item) => item.in_sitemap !== false);
  const groups = {};
  for (const item of records) groups[item.status || "Unknown"] = (groups[item.status || "Unknown"] || 0) + 1;
  const report = { cache_file: cache.source || cacheFile, cache_updated_at: cache.updated_at || "", sitemap_url: cache.sitemap_url || sitemapUrl(), cached_urls: records.length, indexed: records.filter((item) => item.indexed).length, not_indexed: records.filter((item) => !item.indexed).length, status_groups: groups };
  if (details) report.results = records;
  return report;
}

function saveCache(cache) {
  fs.mkdirSync(cacheDir, { recursive: true });
  const saved = { version: 1, updated_at: new Date().toISOString(), sitemap_url: sitemapUrl(), urls: cache.urls, source: cacheFile };
  fs.writeFileSync(cacheFile, `${JSON.stringify({ ...saved, source: undefined }, null, 2)}\n`, "utf8");
  return saved;
}

function decodeXml(value) { return String(value).replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;|&#39;/g, "'").trim(); }

async function sitemapEntries(root, limit = 2000, depth = 0, seen = new Set()) {
  if (seen.has(root) || depth > 3 || seen.size > 250) return [];
  seen.add(root);
  const response = await http(publicUrl(root), { headers: { Accept: "application/xml,text/xml,*/*" } });
  if (!response.ok) throw new Error(`Sitemap fetch failed (${response.status}): ${response.statusText}`);
  const items = [...response.text.matchAll(/<(url|sitemap)\b[^>]*>([\s\S]*?)<\/\1>/gi)].map((match) => ({ type: match[1].toLowerCase(), url: decodeXml(match[2].match(/<loc>\s*([^<]+?)\s*<\/loc>/i)?.[1] || ""), lastmod: decodeXml(match[2].match(/<lastmod>\s*([^<]+?)\s*<\/lastmod>/i)?.[1] || ""), sitemap_url: root })).filter((item) => item.url);
  const children = items.filter((item) => item.type === "sitemap");
  if (!children.length) return items.filter((item) => item.type === "url").slice(0, limit);
  const result = [];
  for (const child of children) { if (result.length >= limit) break; result.push(...await sitemapEntries(child.url, limit - result.length, depth + 1, seen)); }
  return [...new Map(result.map((item) => [normalize(item.url), item])).values()].slice(0, limit);
}

async function mapLimit(items, count, worker) {
  const queue = [...items], result = [];
  await Promise.all(Array.from({ length: Math.max(1, Math.min(count, items.length || 1)) }, async () => { while (queue.length) result.push(await worker(queue.shift())); }));
  return result;
}

async function audit(flags) {
  const limit = Math.max(1, Math.min(Number(flags.limit || 2000), 2000));
  const entries = await sitemapEntries(sitemapUrl(), limit), cache = readCache(), now = new Date().toISOString(), keys = new Set(entries.map((item) => normalize(item.url)));
  for (const entry of entries) { const key = normalize(entry.url), old = cache.urls[key] || {}; cache.urls[key] = { ...old, url: entry.url, sitemap_url: entry.sitemap_url, sitemap_lastmod: entry.lastmod, in_sitemap: true, first_seen_at: old.first_seen_at || now, last_seen_at: now }; }
  if (entries.length < limit) for (const [key, record] of Object.entries(cache.urls)) if (record.in_sitemap !== false && !keys.has(key)) cache.urls[key] = { ...record, in_sitemap: false, removed_from_sitemap_at: now };
  const maxAge = Math.max(0, Number(flags["max-age-days"] ?? 7)) * 86_400_000, maxRefresh = Math.max(0, Math.min(Number(flags["max-refresh"] || 200), 500));
  const candidates = entries.filter((entry) => { const record = cache.urls[normalize(entry.url)] || {}, checked = Date.parse(record.inspected_at || record.last_inspected_at || ""); return flags.refresh || !Number.isFinite(checked) || Date.now() - checked > maxAge; }).slice(0, maxRefresh);
  const refreshed = await mapLimit(candidates, Math.max(1, Math.min(Number(flags.workers || 3), 5)), async (entry) => { const key = normalize(entry.url); try { const value = await inspect(entry.url); cache.urls[key] = { ...cache.urls[key], ...value, in_sitemap: true, last_inspected_at: value.inspected_at }; return { ok: true }; } catch (error) { cache.urls[key] = { ...cache.urls[key], status: "inspection_failed", indexed: false, inspection_error: error.message, last_inspected_at: new Date().toISOString() }; return { ok: false }; } });
  const saved = saveCache(cache);
  return { mode: "live_audit", sitemap_url_count: entries.length, refreshed_count: refreshed.length, failed_count: refreshed.filter((item) => !item.ok).length, ...cacheSummary(saved, Boolean(flags.details)) };
}

async function overview(flags) {
  const [sites, sitemap, pages, queries] = await Promise.all([google(`${apiBase}/sites`), sitemapStatus(), performance({ days: flags.days || 28, dimensions: "page", limit: 10 }), performance({ days: flags.days || 28, dimensions: "query", limit: 10 })]);
  return { site_url: siteUrl(), sitemap_url: sitemapUrl(), sites: sites.siteEntry || [], sitemap_status: sitemap, top_pages: pages, top_queries: queries };
}

async function main() {
  const { command, flags, positional } = args(process.argv.slice(2));
  if (["help", "-h", "--help"].includes(command)) return help();
  if (command === "info") { const service = account(); return output({ configured: true, site_url: siteUrl(), sitemap_url: sitemapUrl(), credentials_found: true, credential_file: service.__path, service_account_project: service.project_id || "", service_account_email: service.client_email || "", cache_file: cacheFile }); }
  if (command === "sites") return output(await google(`${apiBase}/sites`));
  if (command === "overview") return output(await overview(flags));
  if (command === "performance") return output(await performance(flags));
  if (command === "sitemap") return output(await sitemapStatus());
  if (command === "inspect") { if (!positional[0]) throw new Error("inspect requires a Wearhongxiu URL."); return output(await inspect(positional[0])); }
  if (command === "audit") return output(await audit(flags));
  if (command === "cache") return output(cacheSummary(readCache(), Boolean(flags.details)));
  if (command === "submit-sitemap") return output(await submit(flags));
  throw new Error(`Unknown command: ${command}`);
}

main().catch((error) => { console.error(`error: ${error.message}`); process.exitCode = 1; });
