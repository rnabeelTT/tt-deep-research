# Law-firm assistant demo: Hermes + a CRM + research lookups

One assistant (Hermes, on the QuietBox's Ornith) that works a law firm's CRM, looks things up with the
research engine, and files what it finds back on the case. Full research reports are the deep research
app's job and are demoed there. The CRM is an Airtable base shaped like
Lawmatics (contacts, matters with a pipeline, tasks, calendar, notes, documents) with a made-up firm,
Whitfield & Ortega LLP. The tools are Lawmatics-shaped, so the same surface can later sit on the
Lawmatics API instead of Airtable.

| piece | where |
| --- | --- |
| CRM data | Airtable base (`AIRTABLE_BASE_ID`), built by `seed.py` |
| CRM tools (MCP, stdio) | `server.py`: `crm_overview`, `find_contacts`, `create_contact`, `find_matters`, `get_matter`, `create_matter`, `update_matter`, `add_note`, `create_task`, `update_task`, `upcoming`, `import_documents_for_research` |
| Research tools (MCP, HTTP) | the engine at `http://localhost:9901/mcp`: `search`, `fetch_page`, `find_in_document`, `get_evidence`, `verify` |
| How Hermes should work | `.hermes/skills/firm-assistant/SKILL.md` |

Where data lives: the CRM records are in Airtable's cloud, as they would be in Lawmatics. The models,
the matter documents once imported, the search indexes and the research all stay on the QuietBox.

## Setup (once)

1. Airtable: an account, an empty base, and a personal access token with `data.records:read`,
   `data.records:write`, `schema.bases:read`, `schema.bases:write` on that base. In `.env`:
   ```
   AIRTABLE_TOKEN=pat...
   AIRTABLE_BASE_ID=app...          # just the app... part of the base URL
   ```
2. Build and fill the base (dates are relative to today; `--reset` reseeds before a demo so the
   deadlines are fresh and the demo's own writes are gone):
   ```bash
   uv run python demos/crm/seed.py            # or --reset
   ```
   Delete the default "Table 1" in Airtable by hand (the API can't delete tables).
3. Hermes: both MCP servers in `~/.hermes/config.yaml`, and the skill:
   ```yaml
   mcp_servers:
     ttdr:
       url: http://localhost:9901/mcp
     firm-crm-airtable:
       command: uv
       args: ["run", "--project", "<repo>", "python", "<repo>/demos/crm/server.py"]
   ```
   ```bash
   ln -s <repo>/.hermes/skills/firm-assistant ~/.hermes/skills/firm-assistant
   hermes mcp test firm-crm-airtable && hermes mcp test ttdr
   ```
4. The app and models up (`uv run ttdr up`): the research tools need the engine (:9901), and document
   imports the Deep Agents API (:8200).

## Run

```bash
OPENAI_BASE_URL=http://127.0.0.1:8000/v1 OPENAI_API_KEY=local \
  hermes chat --tui --provider openai --model ornith-ai/Ornith-1.5-35B-A3B -s firm-assistant -t firm-crm-airtable,ttdr
```

## Stories

1. **Morning briefing** (read only, ~30 s): "Give me this week's briefing: what's overdue, what's due
   today, and what's coming up. Flag anything urgent." Overdue discovery, today's intake call-backs,
   depositions and the custody mediation, statutes of limitations checked.
2. **Look it up and file it** (~1-2 min): "Import the Sato lease into research, then tell me what it says
   about assigning the lease if Kenji sells the cafe, and about the personal guarantee. Add a note to the
   matter with what you found." Hermes pulls the matter's files into a collection on the QuietBox, searches
   them, answers with page citations (the sole-discretion consent, the recapture right, the 50% transfer
   fee, a guaranty that never burns off), and files the note.
3. **New intake** (~1 min): "New lead from the website: Jordan Ellis, jordan.ellis@example.com, rear-ended
   on Highway 101 in San Mateo yesterday, whiplash, the other driver was cited. Set them up and schedule
   the next steps." Contact, matter at Intake with the right attorney and the statute of limitations,
   tasks for the conflict check and engagement letter; Hermes confirms before writing.

Quick ones that land well: "What does the FreshMart incident report say about the sweep log?" (after
importing M-2026-031's files), "Move Patel to Pleadings", "Which PI cases does Dana have and
where are they?"
