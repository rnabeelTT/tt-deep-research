"""Lawmatics: files from the matters a user picks, through Lawmatics' REST API with the access
token their browser got for them.

Lawmatics apps are OAuth clients with a secret, so the sign-in is split: the browser opens
Lawmatics' consent page and receives the code, and this server swaps the code for a token
(with the app's secret, from the environment) and hands the token back to the browser. The
server keeps neither the token nor the refresh token: the browser holds the token for the page's
life and sends it with each request, as with the other stores. Browsing matters and their files
goes through this server too (`browse`), since Lawmatics' API isn't documented as callable from a
web page.

Written from Lawmatics' public docs without an account: what they don't state is gathered under
UNVERIFIED below, to check against a real firm before this is relied on.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ttdr.connectors import (
    AuthError,
    Budget,
    ConnectorError,
    Item,
    Limits,
    Resolved,
    Unavailable,
)
from ttdr.connectors.http import RETRY_STATUS, Fetcher, bearer, client
from ttdr.recipe import Connectors
from ttdr.recipe import Lawmatics as Settings

REDIRECT_PATH = "/auth/lawmatics-redirect"
"""The UI page Lawmatics' consent page returns to; it hands the code to the app and closes."""
STORE = "Lawmatics"
PICK = re.compile(r"(file|matter):(\d{1,20})")
"""A picked item: one file, or every file of a matter."""
ID = re.compile(r"\d{1,20}")
MAX_TOKEN = 8192
MAX_CODE = 2048
PAGE_LIMIT = 50
"""Pages of a list read for one browse or one matter (Lawmatics allows 500 requests a minute
per firm)."""
STORED = {".pdf": ".pdf", ".docx": ".docx", ".txt": ".txt", ".md": ".md", ".markdown": ".md"}
STORED[".csv"] = ".txt"
"""Types the library reads, by suffix, and the suffix each is saved under (no conversions:
Lawmatics stores files as uploaded)."""
ORIGINS = tuple(Connectors().origins)

# UNVERIFIED: not stated in the docs that could be read; check each against a real firm.
MATTERS = "prospects"
"""Matters are `/v1/prospects` (stated); their display fields are not."""
NAME_FIELDS = ("name", "case_title", "title", "matter_name")
"""Matter attributes tried, in order, for a display name before first and last name."""
FILE_NAME_FIELDS = ("name", "file_name", "filename", "title")
FILE_SIZE_FIELDS = ("size", "file_size", "byte_size")
FILES_OF_MATTER = "matter_id"
"""Query parameter that limits `/v1/files` to one matter (the docs say files filter by matter_id
"on polymorphic relationships", without the syntax)."""
DOWNLOAD = "files/download/{id}"
"""Stated. Whether it streams bytes or redirects to a signed URL is not, so both are handled."""
CONTENT_HOSTS = ("lawmatics.com", "amazonaws.com", "cloudfront.net")
"""Where a download redirect may point (signed URLs; no token is sent)."""
NEXT_LINK = ("links", "next")
TOTAL_PAGES = ("meta", "total_pages")


@dataclass(frozen=True)
class Config:
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    api_url: str
    scope: str = ""
    origins: tuple[str, ...] = ORIGINS

    @classmethod
    def load(
        cls,
        settings: Settings | None = None,
        env: Mapping[str, str] | None = None,
        origins: tuple[str, ...] = ORIGINS,
    ):
        """The recipe's [connectors.lawmatics] plus TTDR_LAWMATICS_CLIENT_SECRET (and
        TTDR_LAWMATICS_CLIENT_ID to override the ID, for development). None, and the import off,
        until both are set; ValueError for a URL that isn't https."""
        settings = settings or Settings()
        env = os.environ if env is None else env
        client_id = env.get("TTDR_LAWMATICS_CLIENT_ID", "").strip() or settings.client_id.strip()
        secret = env.get("TTDR_LAWMATICS_CLIENT_SECRET", "").strip()
        if not client_id or not secret:
            return None
        urls = {
            k: getattr(settings, k).strip().rstrip("/")
            for k in ("authorize_url", "token_url", "api_url")
        }
        for key, url in urls.items():
            if not url.startswith("https://") or not urlsplit(url).hostname:
                raise ValueError(f"{key} must be an https URL")
        return cls(client_id, secret, **urls, scope=settings.scope.strip(), origins=tuple(origins))


def _error(r: httpx.Response) -> str:
    """Lawmatics' error message, if the body has one (JSON:API errors or a plain message)."""
    try:
        body = r.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first = errors[0]
        return str(first.get("detail") or first.get("title") or "")
    return str(body.get("message") or body.get("error_description") or body.get("error") or "")


def _status_error(r: httpx.Response) -> Exception:
    message = _error(r)
    if r.status_code == 401:
        return AuthError("Lawmatics sign-in expired before the import finished; sign in again")
    if r.status_code == 403:
        return ConnectorError("Lawmatics refused access to this file")
    if r.status_code == 404:
        return ConnectorError("not found in Lawmatics; it may have been moved or deleted")
    if r.status_code in RETRY_STATUS:
        return Unavailable("Lawmatics is busy; try again in a minute")
    return ConnectorError(message or f"Lawmatics returned {r.status_code}")


def _attrs(resource: object) -> dict:
    if not isinstance(resource, dict):
        return {}
    attrs = resource.get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def _first(attrs: dict, fields: tuple[str, ...]) -> str:
    for f in fields:
        if isinstance(v := attrs.get(f), str) and v.strip():
            return v.strip()
    return ""


def _resources(body: object) -> list[dict]:
    data = body.get("data") if isinstance(body, dict) else None
    items = data if isinstance(data, list) else []
    return [d for d in items if isinstance(d, dict) and ID.fullmatch(str(d.get("id", "")))]


def _has_next(body: object, page: int) -> bool:
    if not isinstance(body, dict):
        return False
    links = body.get(NEXT_LINK[0])
    if isinstance(links, dict) and links.get(NEXT_LINK[1]):
        return True
    meta = body.get(TOTAL_PAGES[0])
    total = meta.get(TOTAL_PAGES[1]) if isinstance(meta, dict) else None
    return isinstance(total, int) and page < total


def matter_name(resource: dict) -> str:
    attrs = _attrs(resource)
    if name := _first(attrs, NAME_FIELDS):
        return name
    person = " ".join(_first(attrs, (f,)) for f in ("first_name", "last_name")).strip()
    return person or f"Matter {resource.get('id', '')}"


def file_info(resource: dict) -> dict:
    """What the browser shows for one file: its ID, name, size and when it last changed."""
    attrs = _attrs(resource)
    size = next((attrs[f] for f in FILE_SIZE_FIELDS if isinstance(attrs.get(f), int)), 0)
    return {
        "id": str(resource["id"]),
        "name": _first(attrs, FILE_NAME_FIELDS) or f"File {resource['id']}",
        "size": max(0, size),
        "updated_at": attrs.get("updated_at") if isinstance(attrs.get("updated_at"), str) else "",
    }


def _saved_name(name: str) -> str | None:
    """The name a file is saved under, or None for a type the library can't read."""
    suffix = Path(name).suffix.lower()
    if suffix not in STORED:
        return None
    saved = STORED[suffix]
    return name if suffix == saved else f"{Path(name).stem}{saved}"


def _content_host(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(
        host == h or host.endswith(f".{h}") for h in CONTENT_HOSTS
    )


class Lawmatics:
    id = "lawmatics"
    name = "Lawmatics"

    def __init__(
        self,
        config: Config,
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.config = config
        self.http = http or client()
        self.api = Fetcher(STORE, self.http, _status_error, sleep=sleep)

    def public(self) -> dict:
        return {
            "client_id": self.config.client_id,
            "authorize_url": self.config.authorize_url,
            "scope": self.config.scope,
            "redirect_path": REDIRECT_PATH,
            "origins": list(self.config.origins),
            "importable": sorted(STORED),
        }

    def _url(self, path: str) -> str:
        return f"{self.config.api_url}/{path}"

    # Sign-in -----------------------------------------------------------------------------------

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> dict:
        """Swap the browser's authorization code for an access token, with the app's secret. Only
        the access token and its lifetime go back; a refresh token is dropped, so the server
        never holds a lasting credential."""
        if not code or len(code) > MAX_CODE or not code.isprintable():
            raise AuthError("Lawmatics sign-in returned no code; sign in again")
        if redirect_uri not in {f"{o}{REDIRECT_PATH}" for o in self.config.origins}:
            raise AuthError("sign in from the app's own address (see the Files tab)")
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        if verifier:
            form["code_verifier"] = verifier
        try:
            r = await self.http.post(self.config.token_url, data=form)
        except httpx.TransportError:
            raise Unavailable("couldn't reach Lawmatics to finish the sign-in") from None
        if r.status_code in RETRY_STATUS:
            raise Unavailable("Lawmatics is busy; try signing in again in a minute")
        try:
            body = r.json()
        except ValueError:
            body = {}
        token = body.get("access_token") if isinstance(body, dict) else None
        if r.status_code >= 400 or not isinstance(token, str) or not token:
            raise AuthError("Lawmatics refused the sign-in; sign in again")
        expires = body.get("expires_in")
        return {
            "access_token": token,
            "expires_in": int(expires) if isinstance(expires, (int, float)) else 3600,
        }

    async def authorize(self, credential: str) -> str | None:
        """Accept a token Lawmatics honours, and name its user for the files' provenance."""
        token = credential
        if not token or len(token) > MAX_TOKEN or not token.isascii() or not token.isprintable():
            raise AuthError("Lawmatics sign-in is missing; sign in again")
        if any(c.isspace() for c in token):
            raise AuthError("Lawmatics sign-in is missing; sign in again")
        try:
            me = await self.api.json(self._url("users/me"), None, bearer(token))
        except AuthError:
            raise AuthError("Lawmatics sign-in expired; sign in again") from None
        except Unavailable:
            raise
        except ConnectorError:
            raise AuthError("this Lawmatics sign-in can't read the firm's files") from None
        attrs = _attrs(me.get("data") if isinstance(me, dict) else None)
        who = _first(attrs, ("email", "name"))
        if not who:
            who = " ".join(_first(attrs, (f,)) for f in ("first_name", "last_name")).strip()
        return who if who.isprintable() and len(who) <= 256 else None

    # Browsing ----------------------------------------------------------------------------------

    async def browse(self, credential: str, request: dict) -> dict:
        """One page for the UI's browser: matters (optionally matching QUERY), or one matter's
        files. {"kind": "matters", "query": str, "page": int} or {"kind": "files", "matter": id,
        "page": int}."""
        kind, page = request.get("kind"), request.get("page", 1)
        if not isinstance(page, int) or not 1 <= page <= 10_000:
            raise ConnectorError("page is a positive number")
        if kind == "matters":
            query = request.get("query") or ""
            if not isinstance(query, str) or len(query) > 200:
                raise ConnectorError("the search is too long")
            return await self._matters(credential, query.strip().lower(), page)
        if kind == "files":
            matter = str(request.get("matter", ""))
            if not ID.fullmatch(matter):
                raise ConnectorError("not a Lawmatics matter")
            body = await self._files_page(credential, matter, page)
            files = []
            for f in map(file_info, _resources(body)):
                f["importable"] = _saved_name(f["name"]) is not None
                files.append(f)
            return {"files": files, "more": _has_next(body, page)}
        raise ConnectorError("browse matters or files")

    async def _matters(self, credential: str, query: str, page: int) -> dict:
        """UNVERIFIED: no documented name search for matters, so a search reads list pages and
        keeps the matches (up to PAGE_LIMIT pages from PAGE)."""
        found, current = [], page
        while True:
            body = await self.api.json(
                self._url(MATTERS), {"page": str(current), "fields": "all"}, bearer(credential)
            )
            for m in _resources(body):
                name = matter_name(m)
                if not query or query in name.lower():
                    status = _attrs(m).get("status")
                    found.append(
                        {
                            "id": str(m["id"]),
                            "name": name,
                            "status": status if isinstance(status, str) else "",
                        }
                    )
            more = _has_next(body, current)
            if not query or not more or len(found) >= 25 or current - page + 1 >= PAGE_LIMIT:
                return {"matters": found, "more": more, "next_page": current + 1}
            current += 1

    async def _files_page(self, credential: str, matter: str, page: int) -> dict:
        params = {FILES_OF_MATTER: matter, "page": str(page), "fields": "all"}
        return await self.api.json(self._url("files"), params, bearer(credential))

    # Import ------------------------------------------------------------------------------------

    async def resolve(self, credential: str, item_id: str, limits: Limits) -> Resolved:
        """A picked file, or every file of a picked matter within LIMITS; what's left out is in
        `skipped`."""
        if not (m := PICK.fullmatch(item_id)):
            raise ConnectorError("not a Lawmatics file or matter")
        kind, ident = m.groups()
        if kind == "file":
            body = await self.api.json(
                self._url(f"files/{ident}"), {"fields": "all"}, bearer(credential)
            )
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, dict) or str(data.get("id")) != ident:
                raise ConnectorError("Lawmatics sent another file than the one picked")
            item = self._item(data, limits)
            if isinstance(item, str):
                raise ConnectorError(item)
            return Resolved([item])
        return await self._matter_files(credential, ident, limits)

    def _item(self, resource: dict, limits: Limits) -> Item | str:
        """The import plan for one file, or why it's left out."""
        info = file_info(resource)
        if not (saved := _saved_name(info["name"])):
            suffix = Path(info["name"]).suffix.lower() or "files without a type"
            return f"{suffix} can't be read; use PDF, Word (.docx), text or Markdown"
        if info["size"] > limits.max_bytes:
            return f"larger than the {limits.max_bytes // 2**20} MB import limit"
        return Item(
            f"file:{info['id']}", saved, info["size"], version=info["updated_at"], store=STORE
        )

    async def _matter_files(self, credential: str, matter: str, limits: Limits) -> Resolved:
        found, left_out, page = Resolved([]), {}, 1
        while page <= PAGE_LIMIT:
            body = await self._files_page(credential, matter, page)
            for resource in _resources(body):
                if len(found.items) >= limits.max_files:
                    found.skipped.append(
                        f"Matter {matter}: stopped at {limits.max_files} files, the most an import takes"
                    )
                    return self._summarize(found, matter, left_out)
                item = self._item(resource, limits)
                if isinstance(item, str):
                    left_out[item] = left_out.get(item, 0) + 1
                else:
                    found.items.append(item)
            if not _has_next(body, page):
                break
            page += 1
        return self._summarize(found, matter, left_out)

    @staticmethod
    def _summarize(found: Resolved, matter: str, left_out: dict[str, int]) -> Resolved:
        for reason, n in left_out.items():
            what = "1 file" if n == 1 else f"{n} files"
            found.skipped.append(f"Matter {matter}: {what} left out ({reason})")
        if not found.items and not found.skipped:
            found.skipped.append(f"Matter {matter}: it has no files")
        return found

    async def fetch(self, credential: str, item: Item, budget: Budget) -> tuple[str, bytes]:
        """The file's bytes. A redirect to a signed URL is followed without the token, and only
        to Lawmatics' storage hosts."""
        m = PICK.fullmatch(item.id)
        if not m or m.group(1) != "file":
            raise ConnectorError("not a Lawmatics file")
        url = self._url(DOWNLOAD.format(id=m.group(2)))
        r = await self.api.send(url, None, bearer(credential))
        try:
            if r.status_code >= 400:
                await r.aread()
                raise _status_error(r)
            location = r.headers.get("location", "") if r.is_redirect else None
        finally:
            await r.aclose()
        if location is None:
            return item.name, await self.api.body(url, budget, None, bearer(credential))
        if not _content_host(location):
            raise ConnectorError("Lawmatics sent the download somewhere unexpected")
        return item.name, await self.api.body(location, budget)

    async def aclose(self) -> None:
        await self.http.aclose()
