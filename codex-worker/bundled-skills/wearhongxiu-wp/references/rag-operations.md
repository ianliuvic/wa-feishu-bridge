# Wearhongxiu website RAG operations

Read this reference when diagnosing Ask Hongxiu answers or latency, changing
retrieval behavior, or preparing a RAG deployment.

## Runtime boundary

The homepage path is:

1. Ask Hongxiu sends `POST /wp-json/hongxiu-rag/v1/chat` to WordPress.
2. The WordPress plugin proxies the request to the public RAG API.
3. The RAG Backend answers from public/external website documents.

Administrative synchronization and debug queries use the separate RAG MCP
application. The product knowledge base is separate; do not query, ingest,
delete, deploy, or merge product data as part of website RAG work.

Known implementation locations:

- Source checkout: `E:\cc\rag`
- GitHub repository: `ianliuvic/rag`, branch `main`
- RAG Backend Coolify UUID: `gc8ssgg84koogokgokwkkco4`
- RAG MCP Coolify UUID: `ugs0c0kscwoo0gkcsg0oog00`

Do not infer authorization to mutate Coolify from these identifiers.

## Retrieval behavior to preserve

- Short stateless text questions use the fast path: no LLM query rewrite, no
  topic expansion, and no rerank when a strong authoritative keyword match is
  already first.
- Fast-path retrieval considers at most 24 candidates.
- Public FAQ answers for MOQ, samples, lead time, shipping, payment,
  private-label, OEM, and ODM questions are cached in process for five minutes.
- Official company/site files and core pages outrank Blog or Industry News when
  match quality is otherwise equal.
- Keyword matching uses searchable document/chunk metadata, not the complete
  document topic segmentation attached to every chunk.
- The public endpoint forces `visibility=external` and does not expose retrieval
  debug details.

Treat cache hits as a latency optimization, not proof that cold retrieval is
healthy. A backend restart clears the in-process FAQ cache.

## Read-only diagnosis

Check the exact public route used by the homepage:

```powershell
python scripts/rag_sync.py homepage-check "what is your moq"
python scripts/rag_sync.py homepage-check "what is your moq" --repeat 2
```

The second command helps compare a cold request with an immediate FAQ-cache
hit. It caps repeats at three to avoid needless production load.

Inspect internal retrieval and stage timings through the dedicated website RAG,
using the same external-only filter as the public endpoint:

```powershell
python scripts/rag_sync.py query "what is your moq" --debug
```

In `debug_retrieval`, inspect `fast_path`, candidate counts, rerank skipped/error
state, source ordering, and `timings_ms`. Large `query_rewrite`, `rerank`, or
`answer_generation` values identify different bottlenecks. The homepage route
cannot return these internal timings.

If the browser shows `Sorry, the demo endpoint is not available right now.`,
first run `homepage-check`. That text is the frontend catch fallback and usually
means the WordPress proxy request failed, timed out, or returned non-JSON; it is
not a knowledge-base answer.

After a content sync, validate at least one policy/FAQ question and one exact
company-fact question. Confirm citations favor `Hongxiu Clothing Company and
Website Information` or the relevant core page rather than a generic article.

## Backend deployment and verification

Deployment is a live, potentially disruptive action. Before deploying, inspect
the RAG repository status, review the intended commit, and run the relevant
backend tests. Use the `coolify` skill for Coolify calls. Obtain explicit user
authorization for the exact RAG Backend UUID immediately before triggering the
deployment.

Deploy only `gc8ssgg84koogokgokwkkco4` for Backend retrieval/API changes. Do not
deploy the MCP application or any product/POD application unless the requested
change actually affects it and the user separately authorizes that exact UUID.

After the deployment finishes, verify in this order:

1. Backend health and deployment status.
2. `homepage-check` returns HTTP 200 with JSON rather than the frontend fallback.
3. A cold common FAQ query returns the correct answer and authoritative citation.
4. An immediate repeated FAQ query is faster.
5. An uncached company-fact query is correct.

Do not repeatedly restart or deploy to diagnose a failure. Read deployment logs
and isolate the failing layer first.
