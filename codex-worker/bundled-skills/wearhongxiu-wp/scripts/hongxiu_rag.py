#!/usr/bin/env python3
"""hongxiu-rag: query and manage the Hongxiu (红绣) RAG knowledge base via its MCP server.

Talks MCP JSON-RPC over HTTP (streamable) to https://rag-mcp.yiswim.cloud/mcp
(the "hongxiu-rag" MCP server, deployed on Coolify). Pure Python standard library.

Commands:
  search "query" [filters]          Search knowledge base; answer + citations
  list [--limit N]                  List recent documents
  stats [filters] [--no-docs]       Count documents/chunks with metadata filters
  get <document_id> [--max-chunks N] Read one document
  analyze "content"|--file F [...]  Analyze text without committing to the KB
  ingest "content"|--file F [...]   Ingest text into the KB (commit by default)
  update <document_id> [...]        Update document metadata/tags/visibility
  delete <document_id> [--yes]      Delete a document (DESTRUCTIVE)
  tools                             List available MCP tools
  call <tool> <json-args>           Raw call to any MCP tool (e.g. chunk_updates)

Token (never printed): HONGXIU_RAG_TOKEN env, or ~/.hongxiu-rag/.env
(HONGXIU_RAG_TOKEN=...), or auto-read from the Authorization header of
[mcp_servers.hongxiu-rag.http_headers] in ~/.codex/config.toml.
URL: HONGXIU_RAG_URL env, default https://rag-mcp.yiswim.cloud/mcp
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_URL = "https://rag-mcp.yiswim.cloud/mcp"
DEFAULT_ENV_FILE = os.path.join(os.path.expanduser("~"), ".hongxiu-rag", ".env")
CODEX_CONFIG = os.path.join(os.path.expanduser("~"), ".codex", "config.toml")
WEAR_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.env")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def load_token():
    token = os.environ.get("HONGXIU_RAG_TOKEN", "")
    if token:
        return token
    for env_file in (DEFAULT_ENV_FILE, WEAR_CONFIG):
        if os.path.exists(env_file):
            try:
                for line in open(env_file, encoding="utf-8-sig"):
                    line = line.strip()
                    if line.startswith("HONGXIU_RAG_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if token:
                            return token
            except Exception:
                pass
    if os.path.exists(CODEX_CONFIG):
        try:
            text = open(CODEX_CONFIG, encoding="utf-8").read()
            m = re.search(
                r'\[mcp_servers\.hongxiu-rag\.http_headers\]\s*Authorization\s*=\s*"(Bearer [^"]+)"',
                text,
            )
            if m:
                return m.group(1)
        except Exception:
            pass
    return ""


def parse_mcp_response(body):
    """MCP streamable HTTP returns SSE (event/data lines) or plain JSON."""
    body = body.strip()
    if body.startswith("event:") or "data: {" in body:
        objs = []
        for line in body.splitlines():
            if line.startswith("data: "):
                try:
                    objs.append(json.loads(line[6:].strip()))
                except Exception:
                    pass
        for obj in reversed(objs):
            if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                return obj
        return objs[-1] if objs else {}
    try:
        return json.loads(body)
    except Exception:
        return {"raw": body}


class RagMcp:
    def __init__(self, url, auth):
        self.url = url
        self.auth = auth
        self.session = None
        self._rpc_id = 0

    def _next_id(self):
        self._rpc_id += 1
        return self._rpc_id

    def _post(self, payload, timeout=240):
        headers = {
            "Authorization": self.auth,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": UA,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                session = resp.headers.get("Mcp-Session-Id")
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            die(f"HTTP {exc.code}: {detail[:600]}")
        if session:
            self.session = session
        return parse_mcp_response(body)

    def initialize(self):
        self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "hongxiu-rag-dsh", "version": "1.0"},
            },
        })

    def call(self, name, arguments, timeout=240):
        payload = {
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        response = self._post(payload, timeout=timeout)
        if "error" in response:
            return {"mcp_error": response["error"]}
        result = response.get("result", response)
        if isinstance(result, dict) and "content" in result:
            texts = []
            for block in result.get("content", []):
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                elif isinstance(block, str):
                    texts.append(block)
            combined = "\n".join(texts).strip()
            if not combined:
                return {"is_error": bool(result.get("isError")), "empty": True}
            try:
                parsed = json.loads(combined)
            except Exception:
                return {"is_error": bool(result.get("isError")), "raw": combined}
            if isinstance(parsed, dict) and result.get("isError"):
                parsed["is_error"] = True
            return parsed
        return result

    def list_tools(self):
        response = self._post({"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}})
        return response.get("result", {}).get("tools", [])


def _split_csv(value):
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _print_result(res, args):
    if isinstance(res, dict) and "answer" in res and res.get("answer"):
        print(res["answer"])
        citations = res.get("citations") or []
        if citations:
            print("\n引用来源:")
            for i, c in enumerate(citations, 1):
                title = c.get("document_title", "")
                score = c.get("score", "")
                excerpt = (c.get("excerpt") or "")[:140]
                print(f"  [{i}] {title} (score={score})")
                if excerpt:
                    print(f"      {excerpt}")
        if args and getattr(args, "debug", False) and res.get("debug_retrieval"):
            print("\n检索详情:", json.dumps(res["debug_retrieval"], ensure_ascii=False)[:2500])
        return
    print(json.dumps(res, ensure_ascii=False, indent=2))


def _read_content(args):
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            return fh.read()
    return args.content or ""


# ---------------- commands ----------------

def cmd_search(args, mcp):
    arguments = {"query": args.query, "top_k": max(1, min(args.top_k, 30))}
    for key, value in [
        ("visibility", args.visibility), ("document_id", args.document_id),
        ("source_type", args.source_type), ("category", args.category),
        ("tag", args.tag), ("topic_id", args.topic_id),
        ("created_after", args.created_after), ("created_before", args.created_before),
        ("updated_after", args.updated_after), ("updated_before", args.updated_before),
    ]:
        if value:
            arguments[key] = value
    for key, values in [
        ("document_ids", args.document_ids), ("source_types", args.source_types),
        ("categories", args.categories), ("tags", args.tags), ("topic_ids", args.topic_ids),
    ]:
        if values:
            arguments[key] = _split_csv(values)
    if args.debug:
        arguments["debug_retrieval"] = True
    if args.llm_provider:
        arguments["llm_provider"] = args.llm_provider
    if args.llm_model:
        arguments["llm_model"] = args.llm_model
    _print_result(mcp.call("search_knowledge", arguments), args)


def cmd_list(args, mcp):
    res = mcp.call("list_documents", {"limit": max(1, min(args.limit, 100))})
    _print_result(res, args)


def cmd_stats(args, mcp):
    arguments = {"include_documents": not args.no_docs, "limit": max(0, min(args.limit, 200))}
    for key, value in [
        ("tag", args.tag), ("category", args.category),
        ("source_type", args.source_type), ("visibility", args.visibility),
        ("created_after", args.created_after), ("created_before", args.created_before),
        ("updated_after", args.updated_after), ("updated_before", args.updated_before),
    ]:
        if value:
            arguments[key] = value
    for key, values in [
        ("tags", args.tags), ("categories", args.categories),
        ("source_types", args.source_types), ("visibilities", args.visibilities),
    ]:
        if values:
            arguments[key] = _split_csv(values)
    _print_result(mcp.call("document_stats", arguments), args)


def cmd_get(args, mcp):
    res = mcp.call("get_document", {
        "document_id": args.document_id,
        "max_chunks": max(1, min(args.max_chunks, 20)),
    })
    _print_result(res, args)


def _analyze_arguments(args, content):
    arguments = {"content": content, "visibility": args.visibility or "internal"}
    for key, value in [("title", args.title), ("description", args.description), ("category", args.category)]:
        if value:
            arguments[key] = value
    if args.tags:
        arguments["tags"] = _split_csv(args.tags)
    if args.chunk_size:
        arguments["chunk_size"] = max(200, min(args.chunk_size, 3000))
    if args.chunk_overlap:
        arguments["chunk_overlap"] = max(0, min(args.chunk_overlap, 1500))
    return arguments


def cmd_analyze(args, mcp):
    content = _read_content(args)
    if not content:
        die("content or --file is required")
    _print_result(mcp.call("analyze_text_knowledge", _analyze_arguments(args, content), timeout=200), args)


def cmd_ingest(args, mcp):
    content = _read_content(args)
    if not content:
        die("content or --file is required")
    arguments = _analyze_arguments(args, content)
    arguments["commit"] = not args.no_commit
    if args.allow_duplicates:
        arguments["allow_duplicates"] = True
    if args.allow_review:
        arguments["allow_review"] = True
    _print_result(mcp.call("ingest_text_knowledge", arguments, timeout=240), args)


def cmd_update(args, mcp):
    arguments = {"document_id": args.document_id}
    for key, value in [("title", args.title), ("category", args.category), ("visibility", args.visibility)]:
        if value:
            arguments[key] = value
    if args.tags:
        arguments["tags"] = _split_csv(args.tags)
    _print_result(mcp.call("update_document", arguments, timeout=200), args)


def cmd_delete(args, mcp):
    if not args.yes:
        print(f"Will DELETE document {args.document_id} (and its chunks/embeddings).")
        print("This is destructive. Re-run with --yes to confirm.")
        sys.exit(0)
    _print_result(mcp.call("delete_document", {"document_id": args.document_id}), args)


def cmd_tools(args, mcp):
    for tool in mcp.list_tools():
        print(f"- {tool.get('name')}: {tool.get('description') or ''}")


def cmd_call(args, mcp):
    try:
        arguments = json.loads(args.json_args)
    except json.JSONDecodeError as exc:
        die(f"--json is not valid JSON: {exc}")
    _print_result(mcp.call(args.tool, arguments), args)


# ---------------- argparse ----------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="Query/manage the Hongxiu RAG knowledge base via its MCP server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="Search knowledge base")
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=6)
    p.add_argument("--visibility")
    p.add_argument("--document-id")
    p.add_argument("--document-ids")
    p.add_argument("--source-type")
    p.add_argument("--source-types")
    p.add_argument("--category")
    p.add_argument("--categories")
    p.add_argument("--tag")
    p.add_argument("--tags")
    p.add_argument("--topic-id")
    p.add_argument("--topic-ids")
    p.add_argument("--created-after")
    p.add_argument("--created-before")
    p.add_argument("--updated-after")
    p.add_argument("--updated-before")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--llm-provider")
    p.add_argument("--llm-model")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("list", help="List recent documents")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("stats", help="Document/chunk stats with filters")
    p.add_argument("--tag")
    p.add_argument("--tags")
    p.add_argument("--category")
    p.add_argument("--categories")
    p.add_argument("--source-type")
    p.add_argument("--source-types")
    p.add_argument("--visibility")
    p.add_argument("--visibilities")
    p.add_argument("--created-after")
    p.add_argument("--created-before")
    p.add_argument("--updated-after")
    p.add_argument("--updated-before")
    p.add_argument("--no-docs", action="store_true", help="Do not include matching documents")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("get", help="Read one document")
    p.add_argument("document_id")
    p.add_argument("--max-chunks", type=int, default=8)
    p.set_defaults(func=cmd_get)

    for name in ("analyze", "ingest"):
        p = sub.add_parser(name, help=f"{name} text into/for the knowledge base")
        p.add_argument("content", nargs="?", default="")
        p.add_argument("--file", help="Read content from a UTF-8 file instead")
        p.add_argument("--title")
        p.add_argument("--description")
        p.add_argument("--category")
        p.add_argument("--tags", help="Comma-separated")
        p.add_argument("--visibility", default="internal")
        p.add_argument("--chunk-size", type=int)
        p.add_argument("--chunk-overlap", type=int)
        p.set_defaults(func=cmd_analyze if name == "analyze" else cmd_ingest)
        if name == "ingest":
            p.add_argument("--no-commit", action="store_true", help="Analyze only, do not write")
            p.add_argument("--allow-duplicates", action="store_true")
            p.add_argument("--allow-review", action="store_true")

    p = sub.add_parser("update", help="Update document metadata/tags/visibility")
    p.add_argument("document_id")
    p.add_argument("--title")
    p.add_argument("--category")
    p.add_argument("--tags", help="Comma-separated")
    p.add_argument("--visibility")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("delete", help="Delete a document (DESTRUCTIVE)")
    p.add_argument("document_id")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_delete)

    sub.add_parser("tools", help="List MCP tools").set_defaults(func=cmd_tools)

    p = sub.add_parser("call", help="Raw call to any MCP tool")
    p.add_argument("tool")
    p.add_argument("json_args", metavar="json-args")
    p.set_defaults(func=cmd_call)

    return parser


def main():
    args = build_parser().parse_args()
    url = os.environ.get("HONGXIU_RAG_URL", DEFAULT_URL)
    auth = load_token()
    if not auth:
        die("No token. Set HONGXIU_RAG_TOKEN, add ~/.hongxiu-rag/.env, or ensure ~/.codex/config.toml has [mcp_servers.hongxiu-rag.http_headers].")
    mcp = RagMcp(url, auth)
    mcp.initialize()
    args.func(args, mcp)


if __name__ == "__main__":
    main()
