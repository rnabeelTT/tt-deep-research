"""Connectors: document stores a user imports from (Google Drive first) into a collection.

A connector turns the item IDs a user picked, plus the credential their browser got for them,
into library uploads. Everything around it is shared: the import route, the size and count
limits, concurrent downloads and indexing. Adding a store (OneDrive, Dropbox, or one reached
through an MCP server's tools) means one class with the methods of `Connector`, its settings in
the recipe's [connectors], and an entry in `KNOWN` and `available`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from ttdr.corpus.library import Remote
from ttdr.recipe import Connectors

log = logging.getLogger(__name__)

RESOLVES = 8
"""Items looked up at once while an import is prepared."""


class ConnectorError(Exception):
    """A message the user can act on; never carries the credential."""


class AuthError(ConnectorError):
    """The credential is missing, expired, or wasn't issued to this app."""


class Unavailable(ConnectorError):
    """The store couldn't be reached; trying again later may work."""


@dataclass(frozen=True)
class Item:
    """One file to import: what to save it as and how to fetch it."""

    id: str
    name: str
    size: int = 0
    """Bytes, when the store knows them before the download (0 for converted formats)."""
    exports: tuple[tuple[str, str], ...] = ()
    """Formats to convert to, tried in order, (store's format name, suffix); empty: download
    the stored bytes."""
    web_url: str = ""
    """Where people open the file in the store, for citations."""
    version: str = ""
    """The store's version tag (ETag, revision), to tell a changed file on re-import."""
    store: str = ""
    """Where it lives, for people ("SharePoint", "OneDrive"); empty: the connector's name."""


def web_link(url: object) -> str:
    """URL if it is a plain https link (it goes into reports as is), else empty."""
    if not isinstance(url, str) or len(url) > 2048 or not url.startswith("https://"):
        return ""
    return url if url.isprintable() and not any(c.isspace() for c in url) else ""


@dataclass
class Resolved:
    """What one picked ID stands for: a file, or a folder's files, and what was left out."""

    items: list[Item]
    skipped: list[str] = field(default_factory=list)
    """For people, e.g. "Reports: 3 files of types the library can't read"."""


@dataclass
class Prepared:
    remotes: list[Remote]
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Limits:
    max_files: int = 1000
    max_bytes: int = 2048 * 2**20

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Limits:
        """The UI's upload caps (TTDR_UPLOAD_MAX_FILES, TTDR_UPLOAD_MAX_MB) apply to imports too."""
        env = os.environ if env is None else env
        files = int(env.get("TTDR_UPLOAD_MAX_FILES") or cls.max_files)
        mb = int(env.get("TTDR_UPLOAD_MAX_MB") or cls.max_bytes // 2**20)
        if files <= 0 or mb <= 0:
            raise ValueError("TTDR_UPLOAD_MAX_FILES and TTDR_UPLOAD_MAX_MB must be positive")
        return cls(files, mb * 2**20)


class Budget:
    """Bytes one import may still download, across all its files; a file that fails gives its
    share back."""

    def __init__(self, max_bytes: int):
        self.max_bytes, self.left = max_bytes, max_bytes

    def take(self, n: int) -> None:
        if n > self.left:
            raise ConnectorError(f"the import passed its {self.max_bytes // 2**20} MB limit")
        self.left -= n

    def give(self, n: int) -> None:
        self.left = min(self.max_bytes, self.left + n)


class Connector(Protocol):
    id: str
    """URL-safe, e.g. "google-drive"."""
    name: str
    """For people, e.g. "Google Drive"."""

    def public(self) -> dict:
        """What the browser needs to sign the user in and show a picker; nothing secret."""
        ...

    async def authorize(self, credential: str) -> str | None:
        """Raise AuthError unless CREDENTIAL is live and was issued to this app. Returns the
        signed-in account, for the files' provenance, when the store tells it."""
        ...

    async def resolve(self, credential: str, item_id: str, limits: Limits) -> Resolved:
        """Look the picked item up: a file, or a folder's files (no more than LIMITS allow).
        Raise ConnectorError for a pick the library can't take at all."""
        ...

    async def fetch(self, credential: str, item: Item, budget: Budget) -> tuple[str, bytes]:
        """The item's bytes and the file name to save them under, drawing on BUDGET."""
        ...

    async def aclose(self) -> None: ...


async def prepare(
    connector: Connector, credential: str, item_ids: list[str], limits: Limits
) -> Prepared:
    """Check the credential, then look up every picked item: one Remote per file, in pick order
    (a folder's files where it was picked), failed with the reason when a picked file can't be
    imported. Files past the limits are left out and listed in `skipped`. Raises AuthError when
    the credential is refused, and ConnectorError for a request over the limits."""
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        raise ConnectorError("no files picked")
    if len(ids) > limits.max_files:
        raise ConnectorError(f"pick at most {limits.max_files} files at once")
    account = await connector.authorize(credential)
    budget, slots = Budget(limits.max_bytes), asyncio.Semaphore(RESOLVES)

    async def one(item_id: str) -> Resolved | Remote:
        async with slots:
            try:
                return await connector.resolve(credential, item_id, limits)
            except AuthError:
                raise
            except ConnectorError as e:
                return Remote(f"{connector.name} file {item_id[:12]}", error=str(e))

    def remote(item: Item) -> Remote:
        async def fetch() -> tuple[str, bytes]:
            return await connector.fetch(credential, item, budget)

        source = {
            "connector": connector.id,
            "store": item.store or connector.name,
            "item_id": item.id,
            "web_url": item.web_url,
            "version": item.version,
            "imported_by": account,
        }
        return Remote(item.name, item.size, fetch, source=source)

    out = Prepared([])
    seen: set[str] = set()
    known_bytes = over_count = over_size = 0
    for found in await asyncio.gather(*(one(i) for i in ids)):
        if isinstance(found, Remote):
            out.remotes.append(found)
            continue
        out.skipped += found.skipped
        for item in found.items:
            if item.id in seen:
                continue
            seen.add(item.id)
            if len(out.remotes) >= limits.max_files:
                over_count += 1
            elif known_bytes + item.size > limits.max_bytes:
                over_size += 1
            else:
                known_bytes += item.size
                out.remotes.append(remote(item))
    if over_count:
        out.skipped.append(f"{over_count} more files: an import takes at most {limits.max_files}")
    if over_size:
        mb = limits.max_bytes // 2**20
        out.skipped.append(f"{over_size} more files: an import takes at most {mb} MB")
    return out


KNOWN = {"google-drive": "Google Drive", "onedrive": "OneDrive"}
"""Every store this build can import from, by ID, configured or not."""


def preview(env: Mapping[str, str] | None = None) -> bool:
    """TTDR_CONNECTORS_PREVIEW=1 (development only): the UI shows stores this install hasn't
    set up, disabled, so their buttons can be seen before the values exist."""
    env = os.environ if env is None else env
    return env.get("TTDR_CONNECTORS_PREVIEW", "").strip() == "1"


def available(settings: Connectors | None = None, env: Mapping[str, str] | None = None):
    """The connectors this install configured (the recipe's [connectors], or the environment's
    overrides), by ID. One configured wrongly is left off with a warning, not fatal: the rest
    of the app runs."""
    from ttdr.connectors import gdrive, onedrive

    settings = settings or Connectors()
    origins = tuple(settings.origins)
    found: dict[str, Connector] = {}
    try:
        if config := gdrive.Config.load(settings.google_drive, env, origins):
            found[gdrive.GoogleDrive.id] = gdrive.GoogleDrive(config)
    except ValueError as e:
        log.warning("Google Drive import is off: [connectors.google_drive] %s", e)
    try:
        if config := onedrive.Config.load(settings.onedrive, env, origins):
            found[onedrive.OneDrive.id] = onedrive.OneDrive(config)
    except ValueError as e:
        log.warning("OneDrive import is off: [connectors.onedrive] %s", e)
    return found
