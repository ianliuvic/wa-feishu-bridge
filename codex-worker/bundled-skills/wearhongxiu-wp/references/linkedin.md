# LinkedIn personal-post distribution

Read this reference when a Wearhongxiu WordPress post should also be published
to the authorized LinkedIn member account. This integration calls LinkedIn
directly and does not use Postiz.

## Preconditions

- The LinkedIn app must have `Share on LinkedIn` and the authorization must
  grant `w_member_social`.
- The OAuth flow also requests `openid profile` so the callback can record the
  member URN. If LinkedIn rejects those scopes, enable `Sign In with LinkedIn
  using OpenID Connect` before authorizing.
- The exact redirect URL is
  `https://codex-worker.yiswim.cloud/linkedin/oauth/callback`.
- Tokens are stored only on the persistent Codex Worker volume at
  `/root/.codex/linkedin/oauth.json`, mode `0600`. Never print or copy them into
  prompts, reports, artifacts, WordPress, or Git.

Check readiness without exposing credentials:

```powershell
python scripts/linkedin.py status
```

## Copy and publishing

Write as Yin Liu, Operations Manager at Hongxiu Clothing Co., Ltd. The personal
account is for useful professional observations and long-term personal
credibility, not corporate ad copy. Use a first-person operations perspective
only where the article supports it, such as what the author pays attention to
when evaluating production choices. Never invent a customer story, result,
factory incident, number, or personal experience.

Create one concise English LinkedIn post from the final article, not an excerpt
copied verbatim. Normally use 250 to 700 characters before the link and two to
four short paragraphs. Lead with a specific observation or opinion, explain
why it matters to swimwear brand operators, and leave enough of the answer for
the article. Avoid promotional company claims, engagement bait, canned hooks,
emoji, and a repeated job-title signature. Use at most three useful hashtags;
zero is acceptable.

Load the `humanizer` skill and apply its draft, still-AI audit, and final pass
to this LinkedIn copy. If the standalone skill is unavailable, use this skill's
bundled `references/humanizer.md`. Preserve the professional point while removing generic
LinkedIn phrasing, fake intimacy, slogan-like conclusions, and uniform rhythm.
The final copy must contain no em dash or en dash.

Do not type the article URL into the authored text. For normal article
distribution, publish a native LinkedIn article preview using the WordPress
featured image, final article title, and excerpt. The publishing script uses
the canonical URL exactly once as `content.article.source`, with
`utm_source=linkedin`, `utm_medium=organic_social`,
`utm_campaign=blog_distribution`, and a slug-based `utm_content`; it does not
repeat that URL in the commentary. Use actual line breaks in the UTF-8 text
file. The visible characters `\n` or `\r\n` must never appear in the final
post; the script converts them to real line breaks and rejects any escaped
newline that remains.

Only publish after WordPress publication and RAG synchronization succeed and
the canonical URL returns successfully. Save the copy to a UTF-8 text file and
run:

```powershell
python scripts/linkedin.py publish --text-file C:\path\linkedin.txt --url https://wearhongxiu.com/example/ --thumbnail-file C:\path\featured.webp --article-title "Article title" --article-description "Article excerpt" --yes
```

The script converts a WebP featured image to JPEG before uploading it to
LinkedIn. It fails closed when the three article-preview options are absent.
Only an explicit exceptional request for a plain-link post may bypass the card,
and that call must include `--plain-link`.

LinkedIn publishing is a separate external mutation and requires explicit
authorization. A scheduled task whose prompt explicitly requires LinkedIn
distribution supplies that authorization for its own successful WordPress
posts. If authorization is missing or expired, keep the WordPress result,
skip LinkedIn without retry loops, and report the exact reason to Feishu.
