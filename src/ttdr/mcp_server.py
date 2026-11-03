"""The engine's MCP server: research tools any MCP client (AI-Q, Hermes) can call."""

import hmac
import os
import re
import time
from contextlib import AsyncExitStack, asynccontextmanager

from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

from ttdr.engine import Engine
from ttdr.engine.evidence import Evidence
from ttdr.recipe import Recipe

REPEAT_WINDOW_S = 600
REPEAT_NOTE = (
    "You already ran this exact search; the results below are unchanged. "
    "Search for something different, or stop searching and give your answer.\n\n"
)


PAGES_ENV = "TTDR_PAGES_URL"
CORPUS_URL = re.compile(r"corpus://([^#]+)#page=(\d+)")


def _link(url: str) -> str:
    """corpus://DOC#page=N as a page link a browser can open, when TTDR_PAGES_URL is set."""
    base = os.environ.get(PAGES_ENV)
    return CORPUS_URL.sub(rf"{base.rstrip('/')}/\1/\2", url) if base else url


def _format(items: list[Evidence]) -> str:
    if not items:
        return "No results."
    return "\n\n".join(f"[{e.id}] {e.title}\nURL: {_link(e.url)}\n{e.text}" for e in items)


def _key(*parts) -> str:
    return " ".join(" ".join(str(p).lower().split()) for p in parts)


SEARCH_DOC = """Search the web and private document collections. Use short keyword queries.
sources: "web", "corpus" (the private collections below) or "all"; corpus narrows to one
collection by name. Returns passages with evidence IDs, titles, URLs and snippets.

Private collections:
{catalog}"""

SCOPED_DOC = {
    "web": "Search the web. Use short keyword queries. Returns passages with evidence IDs, titles, "
    "URLs and snippets.",
    "corpus": "Search the private document collections below. Use short keyword queries; corpus "
    "narrows to one collection by name. Returns passages with evidence IDs, titles, page links "
    "and snippets.\n\nPrivate collections:\n{catalog}",
}
SCOPE_TOOLS = {
    "web": {"search", "fetch_page", "get_evidence", "verify"},
    "corpus": {"search", "read_page", "find_in_document", "open_pages", "get_evidence", "verify"},
}


def build(recipe: Recipe, engine: Engine | None = None, scope: str = "all") -> MCPServer:
    """scope "web" or "corpus" serves only that source's tools, with search pinned to it, so a
    client can offer the web and the private collections as separate data sources."""
    catalog = recipe.corpus_catalog() or "- (none described)"
    server = MCPServer(
        "ttdr",
        instructions=(
            "Research tools on a Tenstorrent QB2. Every passage has an evidence ID in square "
            "brackets; cite claims with those IDs, e.g. [W1a2b3c4d], [W1a2b3c4d.3] or "
            "[3M_2018_10K#p59.0]. Search the private collections first for anything they "
            f"cover:\n{catalog}"
        ),
    )
    # Streamable HTTP is stateless here, so there's no per-client session to key on: a repeat is
    # the same search within REPEAT_WINDOW_S, which keeps one client's loop from tripping another.
    state: dict = {"engine": engine, "seen": {}}

    def eng() -> Engine:
        if state["engine"] is None:
            state["engine"] = Engine.open(recipe)
        return state["engine"]

    def tool():
        """server.tool(), for the tools this scope serves."""

        def register(f):
            return server.tool()(f) if scope == "all" or f.__name__ in SCOPE_TOOLS[scope] else f

        return register

    async def run_search(query: str, sources: str, max_results: int, corpus: str | None) -> str:
        key = _key(query, sources, max_results)
        now, seen = time.monotonic(), state["seen"]
        for k in [k for k, t in seen.items() if now - t > REPEAT_WINDOW_S]:
            del seen[k]
        repeat = key in seen
        seen[key] = now
        items = await eng().search(query, sources, max_results, corpus)
        note = REPEAT_NOTE if repeat else ""
        if sources in ("web", "all") and not recipe.services.web():
            note += "Web search is off in this deployment; results are from private documents.\n\n"
        return note + _format(items)

    if scope == "all":

        @server.tool(description=SEARCH_DOC.format(catalog=catalog))
        async def search(
            query: str, sources: str = "all", max_results: int = 8, corpus: str | None = None
        ) -> str:
            return await run_search(query, sources, max_results, corpus)

    elif scope == "web":

        @server.tool(name="search", description=SCOPED_DOC["web"])
        async def search_web(query: str, max_results: int = 8) -> str:
            return await run_search(query, "web", max_results, None)

    else:

        @server.tool(name="search", description=SCOPED_DOC["corpus"].format(catalog=catalog))
        async def search_corpus(query: str, max_results: int = 8, corpus: str | None = None) -> str:
            return await run_search(query, "corpus", max_results, corpus)

    @tool()
    async def fetch_page(url: str, focus: str | None = None, max_passages: int = 5) -> str:
        """Read a web page or PDF. With focus (the question you are answering), returns the
        most relevant passages, each with an evidence ID; without it, the first passages."""
        try:
            items = await eng().fetch(url, focus, max_passages)
        except Exception as e:  # noqa: BLE001 - reported to the model, which can try another URL
            return f"Could not fetch {url}: {type(e).__name__}"
        return _format(items)

    @tool()
    async def read_page(evidence_id: str, question: str) -> str:
        """Look at the page behind a document passage (by its evidence ID, e.g.
        [3M_2018_10K#p59.0]) and report what it shows for your question: chart values, figures,
        diagrams and table cells that the passage text may lack. Use it when a passage mentions a
        figure, table or chart, or seems to be missing the number you need."""
        reading = await eng().read_page(evidence_id, question)
        if reading is None:
            return f"No page to read for {evidence_id} (web passages and unknown IDs have none)."
        return f"[{evidence_id}] page reading:\n{reading}"

    @tool()
    async def find_in_document(doc: str, terms: str) -> str:
        """Find exact words inside one private document: the passages containing every word of
        terms, in page order. doc is the name before '#' in an evidence ID (3M_2018_10K for
        [3M_2018_10K#p59.0]). Use it for line items, defined terms, names and numbers that
        search ranks poorly, e.g. terms="capital expenditures" or "Note 7"."""
        items, total = await eng().find(doc, terms)
        if not items:
            return f"No passage of {doc} contains all of: {terms}"
        more = f" (first {len(items)} of {total})" if total > len(items) else ""
        return f"{total} passages of {doc} contain {terms!r}{more}:\n\n" + _format(items)

    @tool()
    async def open_pages(evidence_id: str, before: int = 0, after: int = 1) -> str:
        """Read the full text of a private document around a passage: its whole page plus up
        to 2 pages before or after, in reading order. Use it when a table, list or argument
        continues past the passage, or to read the notes under a table."""
        items = await eng().open_pages(evidence_id, before, after)
        if not items:
            return f"No pages to open for {evidence_id} (web passages and unknown IDs have none)."
        return _format(items)

    @tool()
    async def get_evidence(ids: list[str]) -> str:
        """Full text of passages by evidence ID, e.g. to quote or check a claim."""
        return _format(await eng().get(ids))

    if recipe.decide:  # tools follow the recipe: no decide role, no verify tool

        @tool()
        async def verify(claim: str, evidence_ids: list[str]) -> str:
            """Check one claim against the passages it cites (by evidence ID).
            Returns supported, contradicted or not_enough_info with the model's confidence."""
            from ttdr.engine.verify import from_recipe

            found = await eng().get(evidence_ids)
            if not found:
                return "not_enough_info: none of those evidence IDs are known"
            v = await from_recipe(recipe, eng().clients.decide).check(
                claim, "\n\n".join(e.text for e in found)
            )
            return f"{v.label} (supported {v.supported:.2f}, contradicted {v.contradicted:.2f})"

    if scope == "all":

        @server.custom_route("/pages/{doc}/{page}", methods=["GET"])
        async def page(request: Request) -> Response:
            """A document page (1-based, as in page links) as a PNG."""
            n = request.path_params["page"]
            png = n.isdigit() and await eng().clients.corpus.page_png(
                request.path_params["doc"], int(n) - 1
            )
            return Response(png, media_type="image/png") if png else Response("no such page", 404)

    return server


TOKEN_ENV = "TTDR_MCP_TOKEN"
LOCAL = {"127.0.0.1", "localhost", "::1"}


def require_token(app, token: str):
    """ASGI wrapper: every HTTP request needs `Authorization: Bearer <token>`."""

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            if not hmac.compare_digest(auth, f"Bearer {token}"):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"www-authenticate", b"Bearer")],
                    }
                )
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await app(scope, receive, send)

    return guarded


def serve(recipe: Recipe, host: str, port: int, path: str) -> None:
    """The engine's tools at PATH, the web-only and corpus-only scopes at PATH/web and PATH/docs,
    and page images at /pages/DOC/PAGE. No auth unless TTDR_MCP_TOKEN is set (then every request
    needs it)."""
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    token = os.environ.get(TOKEN_ENV)
    if host not in LOCAL and not token:
        print(f"warning: MCP server open on {host} without auth (set {TOKEN_ENV} to require one)")
    # Localhost keeps the SDK's DNS-rebinding defaults; a token-guarded remote bind can't know
    # every Host name clients will use, so the token is the check there.
    security = (
        None if host in LOCAL else TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    engine = Engine.open(recipe)
    apps = [
        build(recipe, engine, scope).streamable_http_app(
            streamable_http_path=p, host=host, transport_security=security
        )
        for scope, p in (("all", path), ("web", f"{path}/web"), ("corpus", f"{path}/docs"))
    ]

    @asynccontextmanager
    async def lifespan(_):
        async with AsyncExitStack() as stack:
            for a in apps:
                await stack.enter_async_context(a.router.lifespan_context(a))
            yield

    app = Starlette(routes=[r for a in apps for r in a.routes], lifespan=lifespan)
    uvicorn.run(require_token(app, token) if token else app, host=host, port=port)
