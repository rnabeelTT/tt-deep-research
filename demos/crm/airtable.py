"""A small Airtable client for the CRM demo: records, schema, attachment uploads.

Reads AIRTABLE_TOKEN and AIRTABLE_BASE_ID from the environment, or from the repo's .env.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import httpx

API = "https://api.airtable.com/v0"
CONTENT = "https://content.airtable.com/v0"
ROOT = Path(__file__).resolve().parents[2]


def _env(name: str) -> str:
    if v := os.environ.get(name):
        return v
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name:
                return v.strip().strip("'\"")
    raise SystemExit(f"{name} is not set (environment or {env})")


class Airtable:
    def __init__(self, token: str | None = None, base: str | None = None):
        self.base = base or _env("AIRTABLE_BASE_ID")
        self.http = httpx.Client(
            headers={"Authorization": f"Bearer {token or _env('AIRTABLE_TOKEN')}"}, timeout=60
        )

    def _req(self, method: str, url: str, **kw) -> dict:
        r = self.http.request(method, url, **kw)
        if r.status_code >= 400:
            raise RuntimeError(
                f"Airtable {method} {url.split('/v0/')[-1]}: {r.status_code} {r.text[:400]}"
            )
        return r.json()

    # ---- schema
    def tables(self) -> list[dict]:
        return self._req("GET", f"{API}/meta/bases/{self.base}/tables")["tables"]

    def create_table(self, name: str, fields: list[dict], description: str = "") -> dict:
        body = {
            "name": name,
            "fields": fields,
            **({"description": description} if description else {}),
        }
        return self._req("POST", f"{API}/meta/bases/{self.base}/tables", json=body)

    def create_field(self, table_id: str, field: dict) -> dict:
        return self._req(
            "POST", f"{API}/meta/bases/{self.base}/tables/{table_id}/fields", json=field
        )

    # ---- records
    def list(
        self,
        table: str,
        formula: str | None = None,
        fields: list[str] | None = None,
        sort: list[tuple[str, str]] | None = None,
        max_records: int | None = None,
    ) -> list[dict]:
        params: list[tuple[str, str]] = []
        if formula:
            params.append(("filterByFormula", formula))
        for f in fields or []:
            params.append(("fields[]", f))
        for i, (field, direction) in enumerate(sort or []):
            params += [(f"sort[{i}][field]", field), (f"sort[{i}][direction]", direction)]
        if max_records:
            params.append(("maxRecords", str(max_records)))
        out, offset = [], None
        while True:
            p = params + ([("offset", offset)] if offset else [])
            page = self._req("GET", f"{API}/{self.base}/{table}", params=p)
            out += page["records"]
            if not (offset := page.get("offset")):
                return out

    def get(self, table: str, record_id: str) -> dict:
        return self._req("GET", f"{API}/{self.base}/{table}/{record_id}")

    def create(self, table: str, rows: list[dict]) -> list[dict]:
        """Create records 10 at a time (the API's limit); returns them in order."""
        out = []
        for i in range(0, len(rows), 10):
            body = {"records": [{"fields": r} for r in rows[i : i + 10]], "typecast": True}
            out += self._req("POST", f"{API}/{self.base}/{table}", json=body)["records"]
        return out

    def update(self, table: str, record_id: str, fields: dict) -> dict:
        body = {"fields": fields, "typecast": True}
        return self._req("PATCH", f"{API}/{self.base}/{table}/{record_id}", json=body)

    def upload(
        self, record_id: str, field: str, filename: str, data: bytes, content_type: str
    ) -> dict:
        """Attach a file (up to 5 MB) to a record's attachment field."""
        body = {
            "contentType": content_type,
            "file": base64.b64encode(data).decode(),
            "filename": filename,
        }
        return self._req(
            "POST", f"{CONTENT}/{self.base}/{record_id}/{field}/uploadAttachment", json=body
        )

    def download(self, url: str) -> bytes:
        r = httpx.get(url, timeout=120, follow_redirects=True)
        r.raise_for_status()
        return r.content
