# Wearhongxiu post creation and publishing

Read this reference when the user asks to create, write, illustrate, publish, or
distribute a normal Wearhongxiu WordPress post from a topic.

## Input and outcome

The minimum input is one topic. Optional inputs include a target keyword,
audience, angle, category, source material, and whether to publish and
distribute socially.

The completed workflow produces:

- an English buyer-focused article in WordPress block HTML;
- a recorded `humanizer` draft-audit-final pass over all public-facing copy;
- one primary keyword and a non-competing search intent;
- an excerpt, slug, Yoast title, description, focus keyword, and BlogPosting schema types;
- one content-matched Crun image with useful media title and alt text, used
  both inside the article and as its featured image;
- reviewed internal links to existing canonical Wearhongxiu pages;
- an idempotent local post-package JSON artifact;
- a verified WordPress draft or published post;
- for a published post, one matching website-RAG document with source key
  `wearhongxiu:wordpress:post:{post_id}`;
- optional channel-specific social copy and, when explicitly requested, social publishing.

## End-to-end workflow

### 1. Research and choose the page's role

Run `wp.py info`, then search existing posts/pages for the topic and close
variants. Query the website RAG for company facts or policies used in the
article. Use GSC query/page data when deciding between competing keyword
angles. For time-sensitive claims, research current authoritative public
sources and retain their URLs in `source_urls`.

Do not create a post that substantially duplicates an existing page's search
intent. Narrow the angle, support an existing pillar, or tell the user that an
update to the existing post is more appropriate. Normal educational posts use
the existing `Blog` category. Use `industry-news` only when the user requests
industry news or the topic is genuinely a news report.

Choose one role: a broad pillar guide, a narrower supporting buyer question, or
a company/news update whose main value is recency or trust.

### 2. Select internal links before drafting

Select two to five canonical, indexable Wearhongxiu URLs. Normally include one
commercial destination and one or more closely related guides/posts. Do not
link to operational pages, noindex pages, search results, redirected URLs, or a
URL merely because it contains a keyword. Anchors must describe the linked
page naturally.

Record every link in `internal_links` and insert the exact canonical URL into
the article body. The validator checks both.

### 3. Draft the article and metadata

Write for swimwear brand owners, wholesalers, sourcing teams, and private-label
buyers. Prefer direct answers, practical evaluation criteria, limitations,
examples, and next steps. Use verified Hongxiu facts only. Do not invent
certifications, customers, prices, capacity, MOQ, lead times, test results, or
case studies.

Use one logical H2/H3 hierarchy. Do not place an H1 in post content because the
site post template owns it. Include an introduction, useful body sections, and
a restrained CTA when relevant. Avoid keyword repetition and generic filler.

Metadata rules:

- slug: lowercase ASCII with hyphens;
- SEO title: distinct and normally 30-65 characters;
- description: specific benefit/problem and normally 120-165 characters;
- focus keyword: the unique primary query assigned to this post;
- excerpt: a human-readable summary, not a copy of the meta description;
- schema page/article types: `WebPage` and `BlogPosting` unless evidence calls
  for a different supported Yoast type.

### 4. Humanize and audit the complete draft

Before generating assets or building the package, load the `humanizer` skill
and apply its complete process to the first finished draft. If that standalone
skill is unavailable, read `humanizer.md` in this reference directory and apply
the same process:

1. rewrite the draft without dropping its facts, intent, internal links, or
   practical coverage;
2. perform the required "still AI" audit on that rewrite;
3. produce the final copy after addressing the remaining tells;
4. apply the same editorial pass to the title, excerpt, SEO title, meta
   description, and prepared social copy;
5. confirm that no em dash or en dash remains.

For this B2B site, "human" means plain, specific business English with varied
sentence rhythm and concrete sourcing situations. Do not add invented
first-person experience, fake opinions, jokes, uncertainty, factory stories,
customers, or production details merely to create personality. Preserve useful
technical tables and lists. Record the completed review in the package:

```json
"humanizer": {
  "skill": "humanizer",
  "applied": true,
  "reviewed_fields": ["title", "excerpt", "content", "seo", "social"],
  "remaining_tells": []
}
```

The post validator rejects a package when this stage is absent or incomplete.

### 5. Plan and generate the article image

Use the `crun-imagen` skill after the article and humanizer pass are complete.
Derive the prompt from the final title, central answer, and the specific body
section the image will support. Default to `openai/gpt-image-2`, 16:9 and 2K.
The prompt must request an editorial image without embedded words, logos,
watermarks, celebrity likenesses, or fabricated factory, product, process, or
certification evidence.

Add exactly one `<!-- hx:featured-image -->` marker to the body at the most
useful semantic position, normally after the introduction or immediately after
the paragraph that introduces the pictured idea. Do not place it mechanically
at the beginning or end. Write alt text from what is actually visible after
inspecting the generated image; do not use alt text as a keyword field. If the
image does not match the article, do not publish it. Revise the prompt and
generate a replacement only with authorization for another paid Crun task.

Record the provider, model, prompt, media title, and alt text in the package,
then generate the file:

```powershell
python scripts/post_workflow.py generate-image C:\path\post-package.json --yes
```

This consumes one Crun task, saves a descriptive file under
`output/posts/{slug}/`, and updates `featured_image.path` in the package. During
WordPress apply, the workflow uploads this file once, inserts it at the marker,
and assigns the same media ID as the post featured image.

### 6. Build and validate the package

Create a scaffold:

```powershell
python scripts/post_workflow.py template "TOPIC" --output C:\path\post-package.json
```

Fill every required field, then run local and live validation:

```powershell
python scripts/post_workflow.py validate C:\path\post-package.json
python scripts/post_workflow.py validate C:\path\post-package.json --live
```

The live check is read-only. It rejects a duplicate slug or unknown category
and checks internal-link status and redirects.

Package shape:

```json
{
  "version": 1,
  "topic": "User topic",
  "title": "Post title",
  "slug": "post-slug",
  "excerpt": "Post excerpt",
  "content": "<!-- wp:paragraph --><p>...</p><!-- /wp:paragraph -->",
  "primary_keyword": "unique primary keyword",
  "search_intent": "informational",
  "seo": {
    "title": "SEO title | Hongxiu Clothing",
    "description": "Meta description",
    "focus_keyword": "unique primary keyword",
    "schema_page_type": "WebPage",
    "schema_article_type": "BlogPosting"
  },
  "humanizer": {
    "skill": "humanizer",
    "applied": true,
    "reviewed_fields": ["title", "excerpt", "content", "seo", "social"],
    "remaining_tells": []
  },
  "category_names": ["Blog"],
  "category_ids": [],
  "tag_names": ["Swimwear Sourcing"],
  "featured_image": {
    "generator": "crun-imagen",
    "model": "openai/gpt-image-2",
    "prompt": "A content-specific editorial image brief based on the final article",
    "path": "hero.webp",
    "title": "Media title",
    "alt_text": "Description of visible image content",
    "caption": ""
  },
  "internal_links": [
    {"url": "https://wearhongxiu.com/services/", "anchor": "swimwear manufacturing services", "purpose": "commercial next step"},
    {"url": "https://wearhongxiu.com/fabric-guide/", "anchor": "swimwear fabric guide", "purpose": "supporting guide"}
  ],
  "source_urls": [],
  "social": {"facebook": "", "instagram": "", "linkedin": ""}
}
```

### 7. Create, publish, and synchronize

Creating even a draft is a live mutation. Obtain authorization for the exact
package and desired status immediately before applying it.

Draft only:

```powershell
python scripts/post_workflow.py apply C:\path\post-package.json --yes
```

Publish and ingest into website RAG in one verified run:

```powershell
python scripts/post_workflow.py apply C:\path\post-package.json --yes --publish --sync-rag
```

The apply command repeats validation, resolves existing categories, creates
only requested missing tags, uploads the image, replaces the authored marker
with an accessible Gutenberg image block, assigns that media as the featured
image, creates a draft with Yoast metadata, optionally publishes it, reads
everything back, then synchronizes only that published post into website RAG.
It refuses duplicate slugs and verifies that the inline image and featured
media use the same media ID.

If a late step fails, do not create a second post; report the created post and
media IDs and resume deliberately. For an already-published post, targeted RAG
repair is:

```powershell
python scripts/rag_sync.py sync-post POST_ID
```

### 8. Social distribution

The package can prepare copy for Facebook, Instagram, and LinkedIn. When the
user or a scheduled-task prompt explicitly authorizes LinkedIn personal-account
distribution, read `linkedin.md`, publish only after the WordPress post and RAG
sync pass, apply the `humanizer` draft-audit-final loop to the short
operations-manager viewpoint copy, and return the LinkedIn post ID with the
WordPress result. Use the
bundled `scripts/linkedin.py`; it calls LinkedIn directly and does not use
Postiz. For every normal WordPress article, pass the final featured-image file,
article title, and excerpt so LinkedIn receives a native article preview card.
The tracked canonical URL belongs in `content.article.source`, not as a bare URL
in the commentary. Treat missing preview inputs as a publication failure; do
not silently publish a plain-link fallback. A WordPress publish request alone
does not authorize social posting.

## Completion report

Report the WordPress post ID, canonical URL, status, featured-media ID, assigned
category/tags, primary keyword, internal-link count, Yoast readback result, RAG
source key/document status, and social results if applicable. Distinguish a
draft from a public post.
