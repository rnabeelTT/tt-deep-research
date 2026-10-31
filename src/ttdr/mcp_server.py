"""The engine's MCP server: research tools any MCP client (AI-Q, Hermes) can call."""

import hmac
import os
import time

from mcp.server.mcpserver import MCPServer

from ttdr.engine import Engine
from ttdr.engine.evidence import Evidence
from ttdr.recipe import Recipe

REPEAT_WINDOW_S = 600
REPEAT_NOTE = (
    "You already ran this exact search; the results below are unchanged. "
    "Search for something different, or stop searching and give your answer.\n\n"
)


def _format(items: list[Evidence]) -> str:
    if not items:
        return "No results."
    return "\n\n".join(f"[{e.id}] {e.title}\nURL: {e.url}\n{e.text}" for e in items)


def _key(*parts) -> str:
    return " ".join(" ".join(str(p).lower().split()) for p in parts)


def build(recipe: Recipe, engine: Engine | None = None) -> MCPServer:
    server = MCPServer(
        "ttdr",
        instructions=(
            "Research tools on a Tenstorrent QB2. Every passage has an evidence ID in square "
            "brackets; cite claims with those IDs, e.g. [W1a2b3c4d], [W1a2b3c4d.3] or "
            "[3M_2018_10K#p59.0]."
        ),
    )
    # Streamable HTTP is stateless here, so there's no per-client session to key on: a repeat is
    # the same search within REPEAT_WINDOW_S, which keeps one client's loop from tripping another.
    state: dict = {"engine": engine, "seen": {}}

    def eng() -> Engine:
        if state["engine"] is None:
            state["engine"] = Engine.open(recipe)
        return state["engine"]

    @server.tool()
    async def search(
        query: str, sources: str = "all", max_results: int = 8, corpus: str | None = None
    ) -> str:
        """Search the web and private document collections. Use short keyword queries.
        sources: "web", "corpus" (private documents such as 10-K filings) or "all";
        corpus narrows to one collection (e.g. "financebench").
        Returns passages with evidence IDs, titles, URLs and snippets."""
        key = _key(query, sources, max_results)
        now, seen = time.monotonic(), state["seen"]
        for k in [k for k, t in seen.items() if now - t > REPEAT_WINDOW_S]:
            del seen[k]
        repeat = key in seen
        seen[key] = now
        items = await eng().search(query, sources, max_results, corpus)
        note = REPEAT_NOTE if repeat else ""
        if sources in ("web", "all") and not recipe.services.searxng:
            note += "Web search is off in this deployment; results are from private documents.\n\n"
        return note + _format(items)

    @server.tool()
    async def fetch_page(url: str, focus: str | None = None, max_passages: int = 5) -> str:
        """Read a web page or PDF. With focus (the question you are answering), returns the
        most relevant passages, each with an evidence ID; without it, the first passages."""
        try:
            items = await eng().fetch(url, focus, max_passages)
        except Exception as e:  # noqa: BLE001 - reported to the model, which can try another URL
            return f"Could not fetch {url}: {type(e).__name__}"
        return _format(items)

    @server.tool()
    async def read_page(evidence_id: str, question: str) -> str:
        """Look at the page behind a document passage (by its evidence ID, e.g.
        [3M_2018_10K#p59.0]) and report what it shows for your question: chart values, figures,
        diagrams and table cells that the passage text may lack. Use it when a passage mentions a
        figure, table or chart, or seems to be missing the number you need."""
        reading = await eng().read_page(evidence_id, question)
        if reading is None:
            return f"No page to read for {evidence_id} (web passages and unknown IDs have none)."
        return f"[{evidence_id}] page reading:\n{reading}"

    @server.tool()
    async def get_evidence(ids: list[str]) -> str:
        """Full text of passages by evidence ID, e.g. to quote or check a claim."""
        return _format(await eng().get(ids))

    if recipe.decide:  # tools follow the recipe: no decide role, no verify tool

        @server.tool()
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
    """No auth unless TTDR_MCP_TOKEN is set (then every request needs it)."""
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
    app = build(recipe).streamable_http_app(
        streamable_http_path=path, host=host, transport_security=security
    )
    uvicorn.run(require_token(app, token) if token else app, host=host, port=port)
