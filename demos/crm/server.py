"""The CRM demo's MCP server: Lawmatics-style tools (contacts, matters, tasks, events, notes, documents)
over the Airtable base that seed.py builds. Hermes (or any MCP client) runs it over stdio:

    uv run --project <repo> python <repo>/demos/crm/server.py

The same tool surface could sit on the Lawmatics API instead of Airtable. TTDR_UI_API (default
http://127.0.0.1:8200) is the Deep Agents API that takes document imports for research.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import sys
import time
from pathlib import Path

import httpx
from mcp.server.mcpserver import MCPServer

sys.path.insert(0, str(Path(__file__).parent))
from zoneinfo import ZoneInfo

from airtable import Airtable
from seed import ATTORNEYS, PRACTICE_AREAS, STAFF, STAGES

logging.getLogger("httpx").setLevel(logging.WARNING)
LOCAL = ZoneInfo("America/Los_Angeles")  # the firm's calendar
UI_API = os.environ.get("TTDR_UI_API", "http://127.0.0.1:8200")
AUTHOR = "Hermes (assistant)"
at_ = Airtable()

server = MCPServer(
    "firm-crm-airtable",
    instructions=(
        "The law firm's CRM (practice management): contacts, matters (cases and leads) with a pipeline "
        "stage, tasks, calendar events, notes and documents. Look things up before you change them, refer "
        "to matters by number (M-2026-031) or name, and tell the user what you changed. Today is "
        f"{dt.date.today():%A %d %B %Y}."
    ),
)

# ---------------------------------------------------------------------------------------------- reading
_cache: dict[str, tuple[float, list[dict]]] = {}


def rows(table: str, fresh: bool = False) -> list[dict]:
    """A whole table (the demo base is small), cached for 20 s; writes clear the cache."""
    hit = _cache.get(table)
    if hit and not fresh and time.time() - hit[0] < 20:
        return hit[1]
    data = at_.list(table)
    _cache[table] = (time.time(), data)
    return data


def changed(*tables: str) -> None:
    for t in tables:
        _cache.pop(t, None)


def names(table: str, ids: list[str], field: str) -> str:
    by_id = {r["id"]: r["fields"].get(field, "?") for r in rows(table)}
    return ", ".join(by_id.get(i, i) for i in ids or []) or "-"


def matter(ref: str) -> dict:
    """A matter by record ID, matter number, or (part of) its name."""
    ref = ref.strip()
    ms = rows("Matters")
    for r in ms:
        if ref in (r["id"], r["fields"].get("Matter number")):
            return r
    hits = [r for r in ms if ref.lower() in r["fields"].get("Matter name", "").lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ValueError(f"No matter matches {ref!r}. Use find_matters to look it up.")
    raise ValueError(
        f"{ref!r} matches several matters: "
        + "; ".join(
            f"{h['fields'].get('Matter number')} {h['fields'].get('Matter name')}" for h in hits
        )
    )


def contact_line(r: dict) -> str:
    f = r["fields"]
    bits = [f.get("Type", ""), f.get("Company", ""), f.get("Email", ""), f.get("Phone", "")]
    return f"{f.get('Name')} ({r['id']}): " + " | ".join(b for b in bits if b)


def matter_line(r: dict) -> str:
    f = r["fields"]
    return (
        f"{f.get('Matter number')} {f.get('Matter name')} - {f.get('Practice area')}, stage: {f.get('Stage')}, "
        f"attorney: {f.get('Responsible attorney', '-')}, client: {names('Contacts', f.get('Client'), 'Name')}"
    )


def on(r: dict, m: dict) -> bool:
    return m["id"] in (r["fields"].get("Matter") or [])


def when(r: dict) -> str:
    """A task's due date, or an event's start in the firm's time zone (Airtable returns UTC)."""
    if due := r["fields"].get("Due"):
        return due
    start = r["fields"].get("Start")
    if not start:
        return ""
    return dt.datetime.fromisoformat(start).astimezone(LOCAL).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------------------------- tools
@server.tool()
def crm_overview() -> str:
    """The pipeline stages, practice areas and staff, and how many open matters sit at each stage."""
    counts: dict[str, int] = {}
    for r in rows("Matters"):
        counts[r["fields"].get("Stage", "?")] = counts.get(r["fields"].get("Stage", "?"), 0) + 1
    return "\n".join(
        [
            "Stages (in order): " + ", ".join(f"{s} ({counts.get(s, 0)})" for s in STAGES),
            "Practice areas: " + ", ".join(PRACTICE_AREAS),
            "Attorneys: " + ", ".join(ATTORNEYS),
            "Staff (for task assignment): " + ", ".join(STAFF),
        ]
    )


@server.tool()
def find_contacts(query: str = "", type: str = "") -> str:
    """Contacts whose name, company, email or phone contains QUERY; TYPE narrows to Client, Prospect,
    Opposing party, Opposing counsel, Witness, Expert or Medical provider."""
    q = query.lower()
    hits = [
        r
        for r in rows("Contacts")
        if (
            not q
            or any(
                q in str(r["fields"].get(k, "")).lower()
                for k in ("Name", "Company", "Email", "Phone")
            )
        )
        and (not type or r["fields"].get("Type", "").lower() == type.lower())
    ]
    return "\n".join(contact_line(r) for r in hits) or "No contacts match."


@server.tool()
def create_contact(
    name: str,
    type: str = "Prospect",
    email: str = "",
    phone: str = "",
    company: str = "",
    address: str = "",
    lead_source: str = "",
    notes: str = "",
) -> str:
    """Add a person or company. TYPE: Client, Prospect, Opposing party, Opposing counsel, Witness, Expert or
    Medical provider. Check find_contacts first so you don't create a duplicate."""
    fields = {
        "Name": name,
        "Type": type,
        "Email": email,
        "Phone": phone,
        "Company": company,
        "Address": address,
        "Lead source": lead_source,
        "Notes": notes,
        "Created": dt.date.today().isoformat(),
    }
    rec = at_.create("Contacts", [{k: v for k, v in fields.items() if v}])[0]
    changed("Contacts")
    return f"Created contact {name} ({rec['id']})."


@server.tool()
def find_matters(
    query: str = "",
    stage: str = "",
    practice_area: str = "",
    attorney: str = "",
    include_closed: bool = False,
) -> str:
    """Matters (cases and leads) whose name, number or summary contains QUERY, optionally narrowed by
    STAGE, PRACTICE_AREA or responsible ATTORNEY. Open matters only unless INCLUDE_CLOSED."""
    q = query.lower()
    out = []
    for r in rows("Matters"):
        f = r["fields"]
        if not include_closed and f.get("Status") == "Closed":
            continue
        text = " ".join(
            str(f.get(k, "")) for k in ("Matter name", "Matter number", "Summary")
        ).lower()
        client = names("Contacts", f.get("Client"), "Name").lower()
        if q and q not in text and q not in client:
            continue
        if stage and f.get("Stage", "").lower() != stage.lower():
            continue
        if practice_area and f.get("Practice area", "").lower() != practice_area.lower():
            continue
        if attorney and attorney.lower() not in f.get("Responsible attorney", "").lower():
            continue
        out.append(matter_line(r))
    return "\n".join(out) or "No matters match."


@server.tool()
def get_matter(matter_ref: str) -> str:
    """Everything on one matter: details, people, open and done tasks, events, notes and documents.
    MATTER_REF is the matter number (M-2026-031), its record ID, or part of its name."""
    m = matter(matter_ref)
    f = m["fields"]
    lines = [matter_line(m)]
    for k in (
        "Status",
        "Jurisdiction",
        "Opened",
        "Incident date",
        "Statute of limitations",
        "Estimated value",
    ):
        if k in f:
            lines.append(f"{k}: {f[k]}")
    lines.append(f"Opposing parties: {names('Contacts', f.get('Opposing parties'), 'Name')}")
    lines.append(f"Summary: {f.get('Summary', '-')}")
    tasks = sorted((r for r in rows("Tasks") if on(r, m)), key=when)
    lines.append("\nTasks:")
    lines += [
        f"- [{t['fields'].get('Status')}] {t['fields'].get('Task')} - due {t['fields'].get('Due')}, "
        f"{t['fields'].get('Assigned to', '-')} ({t['id']})"
        for t in tasks
    ] or ["- none"]
    events = sorted((r for r in rows("Events") if on(r, m)), key=when)
    lines.append("\nEvents:")
    lines += [
        f"- {when(e)} {e['fields'].get('Event')} ({e['fields'].get('Type')}) at "
        f"{e['fields'].get('Location', '-')}"
        for e in events
    ] or ["- none"]
    notes = sorted(
        (r for r in rows("Notes") if on(r, m)), key=lambda r: r["fields"].get("Date", "")
    )
    lines.append("\nNotes:")
    lines += [
        f"- {n['fields'].get('Date')} {n['fields'].get('Title')} ({n['fields'].get('Author')}): "
        f"{n['fields'].get('Body', '')}"
        for n in notes
    ] or ["- none"]
    docs = [r for r in rows("Documents") if on(r, m)]
    lines.append("\nDocuments:")
    lines += [
        f"- {d['fields'].get('Document')} ({d['fields'].get('Category')}, received "
        f"{d['fields'].get('Received')}){' [file attached]' if d['fields'].get('File') else ''}"
        for d in docs
    ] or ["- none"]
    return "\n".join(lines)


def next_number() -> str:
    year = dt.date.today().year
    nums = [
        int(m.group(1))
        for r in rows("Matters", fresh=True)
        if (m := re.fullmatch(rf"M-{year}-(\d+)", r["fields"].get("Matter number", "")))
    ]
    return f"M-{year}-{max(nums, default=0) + 1:03d}"


@server.tool()
def create_matter(
    matter_name: str,
    client_contact_ids: list[str],
    practice_area: str,
    summary: str,
    stage: str = "Intake",
    responsible_attorney: str = "",
    jurisdiction: str = "",
    incident_date: str = "",
    statute_of_limitations: str = "",
    estimated_value: float = 0,
    opposing_party_contact_ids: list[str] | None = None,
) -> str:
    """Open a new matter for existing contacts (create them first with create_contact). Dates are
    YYYY-MM-DD. Returns the new matter number."""
    number = next_number()
    fields = {
        "Matter name": matter_name,
        "Matter number": number,
        "Practice area": practice_area,
        "Stage": stage,
        "Status": "Open",
        "Client": client_contact_ids,
        "Opposing parties": opposing_party_contact_ids or [],
        "Responsible attorney": responsible_attorney,
        "Jurisdiction": jurisdiction,
        "Opened": dt.date.today().isoformat(),
        "Incident date": incident_date,
        "Statute of limitations": statute_of_limitations,
        "Estimated value": estimated_value or None,
        "Summary": summary,
    }
    at_.create("Matters", [{k: v for k, v in fields.items() if v not in ("", None, [])}])
    changed("Matters")
    return f"Opened {number} {matter_name} at stage {stage}."


@server.tool()
def update_matter(
    matter_ref: str,
    stage: str = "",
    responsible_attorney: str = "",
    estimated_value: float = 0,
    statute_of_limitations: str = "",
    add_to_summary: str = "",
) -> str:
    """Move a matter to another pipeline STAGE (a Closed stage also closes it), reassign it, or update its
    value, statute of limitations, or summary (ADD_TO_SUMMARY is appended)."""
    m = matter(matter_ref)
    fields: dict = {}
    if stage:
        if stage not in STAGES:
            return f"Unknown stage {stage!r}. Stages: {', '.join(STAGES)}"
        fields |= {"Stage": stage, "Status": "Closed" if stage.startswith("Closed") else "Open"}
    if responsible_attorney:
        fields["Responsible attorney"] = responsible_attorney
    if estimated_value:
        fields["Estimated value"] = estimated_value
    if statute_of_limitations:
        fields["Statute of limitations"] = statute_of_limitations
    if add_to_summary:
        fields["Summary"] = (m["fields"].get("Summary", "") + "\n\n" + add_to_summary).strip()
    if not fields:
        return "Nothing to change."
    at_.update("Matters", m["id"], fields)
    changed("Matters")
    return f"Updated {m['fields'].get('Matter number')}: " + ", ".join(fields)


@server.tool()
def add_note(matter_ref: str, title: str, body: str) -> str:
    """Add a note to a matter (call summaries, research findings, strategy). Markdown is fine; keep the
    sources' citations in the body when the note comes from research."""
    m = matter(matter_ref)
    at_.create(
        "Notes",
        [
            {
                "Title": title,
                "Matter": [m["id"]],
                "Author": AUTHOR,
                "Body": body,
                "Date": dt.date.today().isoformat(),
            }
        ],
    )
    changed("Notes")
    return f"Added note '{title}' to {m['fields'].get('Matter number')}."


@server.tool()
def create_task(
    matter_ref: str,
    task: str,
    due: str,
    assigned_to: str,
    priority: str = "Normal",
    details: str = "",
) -> str:
    """Add a task to a matter. DUE is YYYY-MM-DD; ASSIGNED_TO is one of the staff in crm_overview;
    PRIORITY High, Normal or Low."""
    m = matter(matter_ref)
    rec = at_.create(
        "Tasks",
        [
            {
                "Task": task,
                "Matter": [m["id"]],
                "Due": due,
                "Assigned to": assigned_to,
                "Priority": priority,
                "Status": "To do",
                "Details": details,
            }
        ],
    )[0]
    changed("Tasks")
    return f"Created task '{task}' on {m['fields'].get('Matter number')}, due {due} ({rec['id']})."


@server.tool()
def update_task(task_id: str, status: str = "", due: str = "", assigned_to: str = "") -> str:
    """Change a task's STATUS (To do, In progress, Done), DUE date or assignee. TASK_ID comes from get_matter
    or upcoming."""
    fields = {
        k: v for k, v in (("Status", status), ("Due", due), ("Assigned to", assigned_to)) if v
    }
    if not fields:
        return "Nothing to change."
    rec = at_.update("Tasks", task_id, fields)
    changed("Tasks")
    return f"Updated task '{rec['fields'].get('Task')}': " + ", ".join(
        f"{k} = {v}" for k, v in fields.items()
    )


@server.tool()
def upcoming(days: int = 14, attorney_or_staff: str = "") -> str:
    """The calendar and to-do list: open tasks due within DAYS (overdue ones first) and events in the next
    DAYS, across all matters; ATTORNEY_OR_STAFF narrows to one person."""
    today = dt.date.today()
    end = today + dt.timedelta(days=days)
    by_matter = {r["id"]: r["fields"] for r in rows("Matters")}

    def mname(r):
        f = by_matter.get((r["fields"].get("Matter") or [""])[0], {})
        return f"{f.get('Matter number', '')} {f.get('Matter name', '')}".strip()

    who = attorney_or_staff.lower()
    tasks = [
        r
        for r in rows("Tasks")
        if r["fields"].get("Status") != "Done"
        and r["fields"].get("Due")
        and dt.date.fromisoformat(r["fields"]["Due"]) <= end
        and (not who or who in r["fields"].get("Assigned to", "").lower())
    ]
    events = [
        r
        for r in rows("Events")
        if r["fields"].get("Start")
        and str(today) <= when(r)[:10] <= str(end)
        and (not who or who in r["fields"].get("Attendees", "").lower())
    ]

    def weekday(iso: str) -> str:
        return f"{dt.date.fromisoformat(iso[:10]):%a} {iso}"

    out = [f"Today is {today:%A} {today}. Open tasks due by {end:%a} {end}:"]
    for t in sorted(tasks, key=when):
        due = dt.date.fromisoformat(t["fields"]["Due"])
        flag = " OVERDUE" if due < today else (" TODAY" if due == today else "")
        out.append(
            f"- {weekday(str(due))}{flag} [{t['fields'].get('Priority')}] {t['fields'].get('Task')} "
            f"| assigned to: {t['fields'].get('Assigned to', '-')} | matter: {mname(t)} | task id {t['id']}"
        )
    out.append(f"\nEvents through {end:%a} {end}:")
    out += [
        f"- {weekday(when(e))} {e['fields'].get('Event')} ({e['fields'].get('Type')}) | matter: {mname(e)} "
        f"| at: {e['fields'].get('Location', '-')}"
        for e in sorted(events, key=when)
    ] or ["- none"]
    return "\n".join(out)


@server.tool()
def import_documents_for_research(matter_ref: str, collection: str = "") -> str:
    """Copy a matter's attached files into a private research collection on the QuietBox, so the research
    tools (search with sources="corpus", and deep research) can read and cite them. COLLECTION defaults to
    the matter number, lowercased (m-2026-031). Indexing takes a minute or two; search the collection by that name afterwards."""
    m = matter(matter_ref)
    name = re.sub(
        r"[^a-z0-9_-]+", "-", (collection or m["fields"].get("Matter number", "matter")).lower()
    )
    files = [
        (a["filename"], at_.download(a["url"]), a.get("type", "application/pdf"))
        for d in rows("Documents", fresh=True)
        if on(d, m)
        for a in d["fields"].get("File") or []
    ]
    if not files:
        return f"{m['fields'].get('Matter number')} has no attached files."
    with httpx.Client(timeout=120) as http:
        r = http.post(
            f"{UI_API}/v1/collections",
            json={
                "name": name,
                "description": f"{m['fields'].get('Matter name')}: files from the CRM",
                # starts switched off in the research UI: only chats that turn it on search it
                "default_enabled": False,
            },
        )
        r.raise_for_status()  # an existing collection comes back as it is
        r = http.post(
            f"{UI_API}/v1/collections/{name}/documents",
            files=[("files", (fn, data, ct)) for fn, data, ct in files],
        )
        r.raise_for_status()
    return (
        f"Importing {len(files)} file(s) into collection '{name}': "
        + ", ".join(fn for fn, _, _ in files)
        + f". Indexing job {r.json().get('job_id')}; search it with corpus='{name}' in a minute or two."
    )


if __name__ == "__main__":
    server.run("stdio")
