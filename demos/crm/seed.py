# ruff: noqa: ISC004  (data: long strings across lines)
"""Build the CRM demo base in Airtable: a law-firm CRM shaped like Lawmatics (contacts, matters with a
pipeline, tasks, events, notes, documents), filled with made-up data. Dates are relative to today, so
the deadlines are always upcoming.

    uv run python demos/crm/seed.py            # schema (if missing) + data (if the base is empty)
    uv run python demos/crm/seed.py --reset    # delete this script's records and seed them again

Every person, company and case here is fictional.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from airtable import API, Airtable

TODAY = dt.date.today()
TZ = "America/Los_Angeles"
FIRM = "Whitfield & Ortega LLP"
ATTORNEYS = ["Dana Whitfield", "Marcus Ortega", "Priya Raman"]
STAFF = ATTORNEYS + ["Leo Brandt (paralegal)", "Ava Kim (intake)"]
PRACTICE_AREAS = [
    "Personal injury",
    "Employment",
    "Family law",
    "Estate planning",
    "Business & contracts",
    "Real estate",
    "Immigration",
]
STAGES = [
    "Lead",
    "Intake",
    "Consultation scheduled",
    "Retained",
    "Investigation",
    "Pleadings",
    "Discovery",
    "Mediation / negotiation",
    "Trial prep",
    "Closed - settled",
    "Closed - won",
    "Closed - lost",
    "Closed - declined",
]
CONTACT_TYPES = [
    "Client",
    "Prospect",
    "Opposing party",
    "Opposing counsel",
    "Witness",
    "Expert",
    "Medical provider",
]
SOURCES = [
    "Website form",
    "Referral - past client",
    "Referral - attorney",
    "Google Ads",
    "Bar referral",
    "Walk-in",
]
DOC_CATEGORIES = [
    "Intake form",
    "Incident / police report",
    "Medical records",
    "Contract",
    "Correspondence",
    "Pleading",
    "Discovery",
    "Research memo",
    "Financial",
]
EVENT_TYPES = [
    "Consultation",
    "Client meeting",
    "Deposition",
    "Hearing",
    "Mediation",
    "Court deadline",
    "Site visit",
]

TABLES = ["Contacts", "Matters", "Tasks", "Events", "Notes", "Documents"]


def day(n: int) -> str:
    return (TODAY + dt.timedelta(days=n)).isoformat()


def at(n: int, hh: int, mm: int = 0) -> str:
    """A local time n days from today, as ISO with the Pacific offset (PST in winter, PDT in summer)."""
    d = TODAY + dt.timedelta(days=n)
    dst = dt.date(d.year, 3, 8) <= d < dt.date(d.year, 11, 1)  # close enough for a demo
    return f"{d.isoformat()}T{hh:02d}:{mm:02d}:00{'-07:00' if dst else '-08:00'}"


def select(choices: list[str]) -> dict:
    return {"type": "singleSelect", "options": {"choices": [{"name": c} for c in choices]}}


DATE = {"type": "date", "options": {"dateFormat": {"name": "iso"}}}
DATETIME = {
    "type": "dateTime",
    "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": TZ},
}
TEXT = {"type": "singleLineText"}
LONG = {"type": "multilineText"}


def f(name: str, spec: dict) -> dict:
    return {"name": name, **spec}


# ------------------------------------------------------------------------------------------------ schema
def build_schema(at_: Airtable) -> dict[str, str]:
    have = {t["name"]: t["id"] for t in at_.tables()}
    if all(t in have for t in TABLES):
        print("schema: already there")
        return have
    if any(t in have for t in TABLES):
        raise SystemExit(
            f"schema: some demo tables exist ({[t for t in TABLES if t in have]}), others don't; "
            "delete them in Airtable and run again"
        )
    contacts = at_.create_table(
        "Contacts",
        [
            f("Name", TEXT),
            f("Type", select(CONTACT_TYPES)),
            f("Email", {"type": "email"}),
            f("Phone", {"type": "phoneNumber"}),
            f("Company", TEXT),
            f("Address", TEXT),
            f("Lead source", select(SOURCES)),
            f("Created", DATE),
            f("Notes", LONG),
        ],
        "People and companies: clients, prospects, opposing parties and counsel, witnesses, experts.",
    )
    matters = at_.create_table(
        "Matters",
        [
            f("Matter name", TEXT),
            f("Matter number", TEXT),
            f("Practice area", select(PRACTICE_AREAS)),
            f("Stage", select(STAGES)),
            f("Status", select(["Open", "Closed"])),
            f(
                "Client",
                {"type": "multipleRecordLinks", "options": {"linkedTableId": contacts["id"]}},
            ),
            f(
                "Opposing parties",
                {"type": "multipleRecordLinks", "options": {"linkedTableId": contacts["id"]}},
            ),
            f("Responsible attorney", select(ATTORNEYS)),
            f("Jurisdiction", TEXT),
            f("Opened", DATE),
            f("Incident date", DATE),
            f("Statute of limitations", DATE),
            f("Estimated value", {"type": "currency", "options": {"precision": 0, "symbol": "$"}}),
            f("Summary", LONG),
        ],
        "Cases and leads, each at a stage of the intake-to-close pipeline.",
    )
    link = {"type": "multipleRecordLinks", "options": {"linkedTableId": matters["id"]}}
    at_.create_table(
        "Tasks",
        [
            f("Task", TEXT),
            f("Matter", link),
            f("Due", DATE),
            f("Assigned to", select(STAFF)),
            f("Priority", select(["High", "Normal", "Low"])),
            f("Status", select(["To do", "In progress", "Done"])),
            f("Details", LONG),
        ],
        "To-dos on matters, with due dates.",
    )
    at_.create_table(
        "Events",
        [
            f("Event", TEXT),
            f("Matter", link),
            f("Type", select(EVENT_TYPES)),
            f("Start", DATETIME),
            f("Location", TEXT),
            f("Attendees", TEXT),
            f("Details", LONG),
        ],
        "Calendar: consultations, depositions, hearings, mediations, court deadlines.",
    )
    at_.create_table(
        "Notes",
        [
            f("Title", TEXT),
            f("Matter", link),
            f("Author", select(STAFF)),
            f("Date", DATE),
            f("Body", LONG),
        ],
        "Case notes: calls, meetings, research, strategy.",
    )
    at_.create_table(
        "Documents",
        [
            f("Document", TEXT),
            f("Matter", link),
            f("Category", select(DOC_CATEGORIES)),
            f("Received", DATE),
            f("File", {"type": "multipleAttachments"}),
            f("Description", LONG),
        ],
        "Files on matters.",
    )
    # name the inverse links Airtable added to Contacts after what they mean
    tables = {t["name"]: t for t in at_.tables()}
    for field in tables["Contacts"]["fields"]:
        if field["type"] != "multipleRecordLinks":
            continue
        inverse = next(
            m
            for m in tables["Matters"]["fields"]
            if m["id"] == field["options"]["inverseLinkFieldId"]
        )
        name = {"Client": "Matters as client", "Opposing parties": "Matters as opposing party"}[
            inverse["name"]
        ]
        at_._req(
            "PATCH",
            f"{API}/meta/bases/{at_.base}/tables/{contacts['id']}/fields/{field['id']}",
            json={"name": name},
        )
    print("schema: created", ", ".join(TABLES))
    return {t["name"]: t["id"] for t in at_.tables()}


# ------------------------------------------------------------------------------------------------ data
CONTACTS = [
    # key, name, type, email, phone, company, address, source, created (days ago), notes
    (
        "alvarez",
        "Rosa Alvarez",
        "Client",
        "rosa.alvarez@example.com",
        "(408) 555-0142",
        "",
        "1187 Meridian Ave, San Jose, CA 95125",
        "Google Ads",
        152,
        "Prefers text messages. Spanish and English.",
    ),
    (
        "freshmart",
        "FreshMart Grocers, Inc.",
        "Opposing party",
        "legal@freshmart.example",
        "(800) 555-0199",
        "FreshMart Grocers, Inc.",
        "2400 Commerce Way, Fresno, CA 93721",
        "",
        150,
        "Store #214 (Willow Glen).",
    ),
    (
        "pike",
        "Kendra Pike",
        "Opposing counsel",
        "kpike@pikehale.example",
        "(415) 555-0177",
        "Pike & Hale LLP",
        "50 Fremont St, San Francisco, CA 94105",
        "",
        120,
        "Defense counsel for FreshMart's insurer.",
    ),
    (
        "okoro",
        "Samuel Okoro",
        "Witness",
        "s.okoro@example.com",
        "(408) 555-0110",
        "",
        "San Jose, CA",
        "",
        140,
        "Shopper; saw the spill about 20 minutes before the fall.",
    ),
    (
        "chen",
        "David Chen",
        "Client",
        "dchen.mail@example.com",
        "(650) 555-0123",
        "",
        "88 Laurel St, San Carlos, CA 94070",
        "Referral - attorney",
        96,
        "Referred by Jill Moreno (Moreno Law).",
    ),
    (
        "lumora",
        "Lumora Health Systems",
        "Opposing party",
        "hr-legal@lumora.example",
        "(650) 555-0160",
        "Lumora Health Systems",
        "1 Lumora Pkwy, Redwood City, CA 94063",
        "",
        95,
        "",
    ),
    (
        "okafor",
        "Grace Okafor",
        "Client",
        "grace.okafor@example.com",
        "(510) 555-0131",
        "",
        "42 Hillcrest Rd, Oakland, CA 94618",
        "Referral - past client",
        40,
        "Joint estate plan with husband Emeka.",
    ),
    (
        "emeka",
        "Emeka Okafor",
        "Client",
        "emeka.okafor@example.com",
        "(510) 555-0132",
        "",
        "42 Hillcrest Rd, Oakland, CA 94618",
        "Referral - past client",
        40,
        "",
    ),
    (
        "nguyen",
        "Linh Nguyen",
        "Client",
        "linh.nguyen@example.com",
        "(408) 555-0188",
        "",
        "903 Saratoga Ave, San Jose, CA 95129",
        "Website form",
        75,
        "",
    ),
    (
        "becker",
        "Tom Becker",
        "Opposing party",
        "tbecker@example.com",
        "(408) 555-0189",
        "",
        "Campbell, CA",
        "",
        75,
        "Ex-husband; represented by Sharon Liu.",
    ),
    (
        "liu",
        "Sharon Liu",
        "Opposing counsel",
        "sliu@liufamilylaw.example",
        "(408) 555-0170",
        "Liu Family Law",
        "San Jose, CA",
        "",
        70,
        "",
    ),
    (
        "patel",
        "Arjun Patel",
        "Client",
        "arjun.patel@example.com",
        "(925) 555-0154",
        "",
        "17 Oak Knoll Ct, Danville, CA 94506",
        "Website form",
        33,
        "",
    ),
    (
        "brightline",
        "Brightline Contractors, Inc.",
        "Opposing party",
        "office@brightline.example",
        "(925) 555-0166",
        "Brightline Contractors, Inc.",
        "Pleasanton, CA",
        "",
        33,
        "CSLB license #1048832 (fictional).",
    ),
    (
        "harris",
        "Monique Harris",
        "Client",
        "monique.h@example.com",
        "(669) 555-0102",
        "",
        "2210 Story Rd, San Jose, CA 95122",
        "Bar referral",
        21,
        "",
    ),
    (
        "vance",
        "Gary Vance",
        "Opposing party",
        "",
        "(408) 555-0115",
        "",
        "Milpitas, CA",
        "",
        21,
        "Other driver. Insured by Pacific Crest Mutual.",
    ),
    (
        "sato",
        "Kenji Sato",
        "Client",
        "kenji@satoholdings.example",
        "(415) 555-0148",
        "Sato Holdings LLC",
        "77 Harbor Blvd, Suite 300, Oakland, CA 94607",
        "Referral - past client",
        12,
        "Opening a third cafe location.",
    ),
    (
        "morales",
        "Daniela Morales",
        "Prospect",
        "dmorales@example.com",
        "(408) 555-0136",
        "",
        "Sunnyvale, CA",
        "Website form",
        6,
        "Software engineer on H-1B; employer willing to sponsor.",
    ),
    (
        "brooks",
        "Tyrell Brooks",
        "Prospect",
        "tbrooks@example.com",
        "(510) 555-0119",
        "",
        "Hayward, CA",
        "Google Ads",
        4,
        "Bitten by neighbor's dog; stitches at urgent care.",
    ),
    (
        "kowalski",
        "Anna Kowalski",
        "Prospect",
        "anna.k@example.com",
        "(209) 555-0125",
        "",
        "Tracy, CA",
        "Website form",
        2,
        "Warehouse lead; says she wasn't paid overtime or given meal breaks.",
    ),
    (
        "reyes",
        "Carmen Reyes",
        "Client",
        "creyes@example.com",
        "(408) 555-0161",
        "",
        "San Jose, CA",
        "Referral - past client",
        410,
        "",
    ),
    (
        "lindqvist",
        "Erik Lindqvist",
        "Client",
        "erik.l@example.com",
        "(650) 555-0108",
        "",
        "Palo Alto, CA",
        "Referral - attorney",
        380,
        "",
    ),
    (
        "drmehta",
        "Dr. Anita Mehta",
        "Medical provider",
        "frontdesk@valleyortho.example",
        "(408) 555-0190",
        "Valley Orthopedics",
        "San Jose, CA",
        "",
        130,
        "Treating orthopedist for Rosa Alvarez.",
    ),
]

# key, name, number, area, stage, attorney, jurisdiction, opened (days ago), incident (days ago or None),
# SOL years from incident (or None), value, clients, opposing, summary
MATTERS = [
    (
        "alvarez",
        "Alvarez v. FreshMart Grocers",
        "M-2026-031",
        "Personal injury",
        "Discovery",
        "Dana Whitfield",
        "Santa Clara County Superior Court, CA",
        150,
        170,
        2,
        185000,
        ["alvarez"],
        ["freshmart"],
        "Slip and fall on spilled liquid detergent in aisle 7 of FreshMart store #214. No wet-floor sign; "
        "witness Samuel Okoro saw the spill about 20 minutes earlier. Client fractured her right wrist (ORIF "
        "surgery) and missed 9 weeks of work as a dental hygienist. Liability turns on notice: how long the "
        "spill was there and FreshMart's aisle inspection practice.",
    ),
    (
        "chen",
        "Chen v. Lumora Health Systems",
        "M-2026-034",
        "Employment",
        "Pleadings",
        "Marcus Ortega",
        "San Mateo County Superior Court, CA",
        95,
        110,
        3,
        240000,
        ["chen"],
        ["lumora"],
        "Senior billing analyst terminated 3 weeks after reporting upcoding of Medicare claims to compliance. "
        "Lumora cites 'restructuring'. Two prior reviews rate him 'exceeds expectations'. Claims: Labor Code "
        "1102.5 whistleblower retaliation, wrongful termination in violation of public policy.",
    ),
    (
        "okafor",
        "Okafor family estate plan",
        "M-2026-036",
        "Estate planning",
        "Retained",
        "Priya Raman",
        "Alameda County, CA",
        38,
        None,
        None,
        6500,
        ["okafor", "emeka"],
        [],
        "Revocable living trust, pour-over wills, powers of attorney and advance health care directives. "
        "Two minor children; Grace's brother as successor trustee. Home in Oakland and a rental in Sacramento.",
    ),
    (
        "nguyen",
        "Nguyen custody modification",
        "M-2026-038",
        "Family law",
        "Mediation / negotiation",
        "Priya Raman",
        "Santa Clara County Superior Court, CA",
        74,
        None,
        None,
        15000,
        ["nguyen"],
        ["becker"],
        "Request to modify the 50/50 schedule after the father's move to Sacramento. Mediation through Family "
        "Court Services is set; client wants primary physical custody during the school year.",
    ),
    (
        "patel",
        "Patel v. Brightline Contractors",
        "M-2026-040",
        "Business & contracts",
        "Investigation",
        "Marcus Ortega",
        "Contra Costa County, CA",
        32,
        60,
        4,
        68000,
        ["patel"],
        ["brightline"],
        "Kitchen and bath remodel: contractor took $52,000 of an $84,500 contract, stopped work 6 weeks ago, "
        "left the kitchen unusable. Possible claims: breach of contract, CSLB complaint, recovery of excess "
        "down payment (Bus. & Prof. Code 7159 caps it at $1,000 or 10%).",
    ),
    (
        "harris",
        "Harris - rear-end collision (I-280)",
        "M-2026-041",
        "Personal injury",
        "Investigation",
        "Dana Whitfield",
        "Santa Clara County, CA",
        20,
        26,
        2,
        60000,
        ["harris"],
        ["vance"],
        "Client stopped in traffic on I-280 northbound near Bird Ave; rear-ended by Gary Vance at about 35 mph. "
        "Neck and lower-back soft-tissue injuries, physical therapy ongoing. Police report cites Vance for "
        "unsafe speed (VC 22350).",
    ),
    (
        "sato",
        "Sato Holdings - Harbor Blvd lease review",
        "M-2026-042",
        "Real estate",
        "Retained",
        "Priya Raman",
        "Alameda County, CA",
        11,
        None,
        None,
        4500,
        ["sato"],
        [],
        "Review the landlord's draft 7-year commercial lease for a cafe at 410 Harbor Blvd, Oakland. Client "
        "concerns: the personal guarantee, CAM charge caps, the right to assign if he sells the business, and "
        "the renewal option.",
    ),
    (
        "morales",
        "Morales - employment-based green card",
        "M-2026-044",
        "Immigration",
        "Intake",
        "Marcus Ortega",
        "USCIS",
        5,
        None,
        None,
        9000,
        ["morales"],
        [],
        "H-1B software engineer (6th year starts in 7 months). Employer will sponsor EB-2. Needs PERM timeline "
        "and whether an H-1B extension beyond 6 years is available under AC21.",
    ),
    (
        "brooks",
        "Brooks - dog bite (Hayward)",
        "M-2026-045",
        "Personal injury",
        "Consultation scheduled",
        "Dana Whitfield",
        "Alameda County, CA",
        3,
        9,
        2,
        25000,
        ["brooks"],
        [],
        "Neighbor's dog bit the prospect's forearm on a public sidewalk; 6 stitches at urgent care. Neighbor's "
        "homeowner's insurance unknown.",
    ),
    (
        "kowalski",
        "Kowalski - unpaid overtime (Summit Logistics)",
        "M-2026-046",
        "Employment",
        "Lead",
        "Marcus Ortega",
        "San Joaquin County, CA",
        1,
        None,
        None,
        None,
        ["kowalski"],
        [],
        "Website lead: warehouse shift lead, says she routinely worked 10-hour shifts without overtime pay or "
        "meal breaks for about 2 years. Possible wage-and-hour claims; check whether a class or PAGA action fits.",
    ),
    (
        "reyes",
        "Reyes v. Coastline Apartments",
        "M-2025-118",
        "Personal injury",
        "Closed - settled",
        "Dana Whitfield",
        "Santa Clara County Superior Court, CA",
        405,
        470,
        2,
        92000,
        ["reyes"],
        [],
        "Habitability and mold exposure. Settled at mediation for $92,000.",
    ),
    (
        "lindqvist",
        "Lindqvist dissolution",
        "M-2025-102",
        "Family law",
        "Closed - won",
        "Priya Raman",
        "San Mateo County Superior Court, CA",
        378,
        None,
        None,
        22000,
        ["lindqvist"],
        [],
        "Dissolution with a contested business valuation; judgment entered.",
    ),
]

# matter, task, due (days from today), assigned, priority, status, details
TASKS = [
    (
        "alvarez",
        "Serve second set of special interrogatories",
        -2,
        "Leo Brandt (paralegal)",
        "High",
        "In progress",
        "Ask for aisle inspection logs and sweep sheets for the 4 hours before the fall.",
    ),
    (
        "alvarez",
        "Prepare Rosa Alvarez for deposition",
        6,
        "Dana Whitfield",
        "High",
        "To do",
        "Walk through the timeline, the photos, the ER visit and lost wages.",
    ),
    (
        "alvarez",
        "Request updated records from Valley Orthopedics",
        9,
        "Leo Brandt (paralegal)",
        "Normal",
        "To do",
        "Need the post-surgery PT notes and the final impairment rating.",
    ),
    ("alvarez", "Draft mediation brief", 24, "Dana Whitfield", "Normal", "To do", ""),
    (
        "chen",
        "File and serve the complaint",
        3,
        "Marcus Ortega",
        "High",
        "In progress",
        "Final review of the 1102.5 and Tameny counts; confirm the DFEH/CRD right-to-sue isn't needed.",
    ),
    (
        "chen",
        "Preservation letter to Lumora",
        1,
        "Leo Brandt (paralegal)",
        "High",
        "To do",
        "Emails between Chen, compliance and his manager, Jan-Jun; the restructuring plan.",
    ),
    (
        "chen",
        "Collect Chen's performance reviews and compliance emails",
        10,
        "Leo Brandt (paralegal)",
        "Normal",
        "To do",
        "",
    ),
    ("okafor", "Draft trust and pour-over wills", 8, "Priya Raman", "Normal", "In progress", ""),
    (
        "okafor",
        "Prepare deed transferring the Sacramento rental into the trust",
        15,
        "Leo Brandt (paralegal)",
        "Normal",
        "To do",
        "Check the lender's due-on-sale clause first.",
    ),
    (
        "nguyen",
        "Mediation statement for Family Court Services",
        4,
        "Priya Raman",
        "High",
        "To do",
        "School-year schedule, the commute from Sacramento, the children's activities.",
    ),
    ("nguyen", "Gather school attendance records", -1, "Ava Kim (intake)", "Normal", "To do", ""),
    (
        "patel",
        "Demand letter to Brightline",
        2,
        "Marcus Ortega",
        "High",
        "To do",
        "Demand completion or refund; cite the excess down payment; 14-day deadline.",
    ),
    (
        "patel",
        "Check Brightline's CSLB license and bond",
        0,
        "Ava Kim (intake)",
        "Normal",
        "To do",
        "License status, bond amount and bonding company, prior complaints.",
    ),
    (
        "patel",
        "Get a second contractor's estimate to finish the work",
        12,
        "Leo Brandt (paralegal)",
        "Normal",
        "To do",
        "",
    ),
    (
        "harris",
        "Request the CHP collision report",
        -3,
        "Ava Kim (intake)",
        "Normal",
        "Done",
        "Received.",
    ),
    (
        "harris",
        "Notify Pacific Crest Mutual of representation",
        1,
        "Leo Brandt (paralegal)",
        "High",
        "To do",
        "Letter of representation; ask for the policy limits.",
    ),
    ("harris", "Collect PT records and bills", 14, "Leo Brandt (paralegal)", "Normal", "To do", ""),
    (
        "sato",
        "Lease redline with comments",
        5,
        "Priya Raman",
        "High",
        "In progress",
        "Focus: guarantee burn-off, CAM cap, assignment on sale of business, renewal rent.",
    ),
    (
        "morales",
        "Send engagement letter and intake questionnaire",
        1,
        "Ava Kim (intake)",
        "Normal",
        "To do",
        "",
    ),
    ("morales", "Confirm H-1B start date and I-94", 7, "Marcus Ortega", "Normal", "To do", ""),
    (
        "brooks",
        "Conflict check for the neighbor",
        0,
        "Ava Kim (intake)",
        "High",
        "To do",
        "Neighbor's name: Harold Finch (from the intake call).",
    ),
    (
        "kowalski",
        "Call back the lead",
        0,
        "Ava Kim (intake)",
        "High",
        "To do",
        "Came in through the website form yesterday; screen for wage-and-hour and PAGA.",
    ),
]

# matter, event, type, start (days, hour, minute), location, attendees, details
EVENTS = [
    (
        "alvarez",
        "Deposition of Rosa Alvarez",
        "Deposition",
        (11, 10, 0),
        "Pike & Hale LLP, 50 Fremont St, SF",
        "Dana Whitfield, Rosa Alvarez, Kendra Pike",
        "Defense notice; court reporter booked by defense.",
    ),
    (
        "alvarez",
        "Deposition of FreshMart store manager (PMK)",
        "Deposition",
        (18, 13, 30),
        "Our office",
        "Dana Whitfield, Kendra Pike",
        "Topics: inspection policy, sweep logs, training.",
    ),
    (
        "alvarez",
        "Mediation",
        "Mediation",
        (31, 9, 30),
        "JAMS San Jose",
        "Dana Whitfield, Rosa Alvarez",
        "",
    ),
    (
        "chen",
        "Client meeting - complaint review",
        "Client meeting",
        (2, 16, 0),
        "Zoom",
        "Marcus Ortega, David Chen",
        "",
    ),
    (
        "okafor",
        "Trust signing",
        "Client meeting",
        (16, 11, 0),
        "Our office",
        "Priya Raman, Grace and Emeka Okafor",
        "Notary booked.",
    ),
    (
        "nguyen",
        "Family Court Services mediation",
        "Mediation",
        (7, 9, 0),
        "Santa Clara County Family Justice Center",
        "Priya Raman, Linh Nguyen",
        "",
    ),
    (
        "patel",
        "Site visit - unfinished kitchen",
        "Site visit",
        (3, 15, 0),
        "17 Oak Knoll Ct, Danville",
        "Marcus Ortega, Arjun Patel",
        "Photograph the work; bring the contract.",
    ),
    (
        "sato",
        "Lease call with landlord's counsel",
        "Client meeting",
        (9, 14, 0),
        "Phone",
        "Priya Raman, Kenji Sato",
        "",
    ),
    (
        "brooks",
        "Initial consultation",
        "Consultation",
        (1, 10, 30),
        "Our office",
        "Dana Whitfield, Tyrell Brooks",
        "Bring photos of the injury and the urgent care discharge papers.",
    ),
    (
        "morales",
        "Initial consultation",
        "Consultation",
        (2, 13, 0),
        "Zoom",
        "Marcus Ortega, Daniela Morales",
        "",
    ),
    (
        "chen",
        "Deadline: respond to Lumora's demurrer (if filed)",
        "Court deadline",
        (40, 17, 0),
        "",
        "",
        "",
    ),
]

# matter, title, author, days ago, body
NOTES = [
    (
        "alvarez",
        "Intake call",
        "Ava Kim (intake)",
        151,
        "Rosa slipped in aisle 7 (laundry) at FreshMart Willow Glen around 6:40 pm. Floor wet with blue liquid "
        "detergent, no cone. She fell on her right hand. Store manager took an incident report and photos. "
        "Ambulance declined; went to the ER that night.",
    ),
    (
        "alvarez",
        "Witness call - Samuel Okoro",
        "Dana Whitfield",
        120,
        "Okoro was in aisle 7 at about 6:20 pm and saw a puddle and a broken detergent jug by the bottom shelf. "
        "He told a cashier on his way out. Willing to sign a declaration.",
    ),
    (
        "alvarez",
        "Strategy - notice",
        "Dana Whitfield",
        30,
        "Key issue is constructive notice. Okoro puts the spill there 20+ minutes before the fall, and he told a "
        "cashier (actual notice?). Need the sweep logs; if there's no inspection for 45+ minutes that's strong.",
    ),
    (
        "chen",
        "Initial consultation",
        "Marcus Ortega",
        94,
        "Chen flagged upcoding (99215 billed for 99213 visits) to compliance on 3 Feb by email. Manager cool to him "
        "after. Terminated 24 Feb, 'restructuring', but his role was reposted in April. Two 'exceeds' reviews.",
    ),
    (
        "chen",
        "Damages",
        "Marcus Ortega",
        60,
        "Salary $118k plus 8% bonus. Unemployed 11 weeks, new job at $104k. Emotional distress: saw a therapist 6 times.",
    ),
    (
        "okafor",
        "Design meeting",
        "Priya Raman",
        30,
        "Joint revocable trust. Children 7 and 10; distributions at 25 / 30 / 35. Guardian: Grace's sister Ada. "
        "The Sacramento rental has a mortgage; check the due-on-sale clause (Garn-St Germain should cover it).",
    ),
    (
        "nguyen",
        "Client goals",
        "Priya Raman",
        70,
        "Becker moved to Sacramento (2 hours each way). Kids are 9 and 12 and in school in San Jose. Client wants "
        "primary physical custody during the school year, with alternating weekends and most of the summer to dad.",
    ),
    (
        "patel",
        "Intake",
        "Ava Kim (intake)",
        32,
        "Contract signed in March for $84,500, deposit $8,450, progress payments to $52,000 so far. Work stopped "
        "6 weeks ago; the owner (Rick Dalton) stopped answering. Kitchen has no counters or sink.",
    ),
    (
        "patel",
        "Down payment issue",
        "Marcus Ortega",
        25,
        "Deposit of $8,450 is 10% of the price, but B&P 7159 caps a home improvement down payment at $1,000 or 10%, "
        "whichever is LESS. So $7,450 over the cap: a licensing violation and leverage.",
    ),
    (
        "harris",
        "Intake call",
        "Ava Kim (intake)",
        20,
        "Stopped in slow traffic near Bird Ave; hit from behind. Neck pain next morning. Urgent care, then PT twice a "
        "week. Car repair estimate $6,800. Other driver admitted he was looking at his phone (per the police report).",
    ),
    (
        "sato",
        "Lease concerns",
        "Priya Raman",
        10,
        "Kenji's priorities: (1) a cap or burn-off on the personal guarantee, (2) CAM increases capped at 5% a year, "
        "(3) the right to assign the lease if he sells the cafe business, (4) a renewal option at a fixed or capped rent.",
    ),
    (
        "morales",
        "Website form",
        "Ava Kim (intake)",
        5,
        "Daniela: BS in CS, 6 years' experience, H-1B since 2021 (6-year limit next June). Employer will start PERM. "
        "Asked whether she can stay past 6 years while the green card is pending.",
    ),
    (
        "brooks",
        "Intake call",
        "Ava Kim (intake)",
        3,
        "Bitten on the forearm by the neighbor's German shepherd while walking on the sidewalk, 9 days ago. 6 "
        "stitches. Dog was off leash. No prior bites known. Neighbor: Harold Finch.",
    ),
    (
        "kowalski",
        "Website form",
        "Ava Kim (intake)",
        1,
        "Shift lead at the Summit Logistics warehouse in Tracy for 2 years. Says 10-hour shifts, paid straight time, "
        "no meal breaks on busy days; about 40 coworkers in the same situation.",
    ),
    (
        "reyes",
        "Closing memo",
        "Dana Whitfield",
        300,
        "Settled at mediation for $92,000; release signed; file closed.",
    ),
]

# matter, document, category, received (days ago), description, attachment key or None
DOCUMENTS = [
    (
        "alvarez",
        "FreshMart incident report - store #214",
        "Incident / police report",
        140,
        "The store's own incident report, produced in discovery.",
        "freshmart_incident",
    ),
    (
        "alvarez",
        "ER discharge summary",
        "Medical records",
        148,
        "Distal radius fracture; ortho referral.",
        None,
    ),
    (
        "alvarez",
        "Photos of aisle 7",
        "Discovery",
        140,
        "Taken by the store manager after the fall.",
        None,
    ),
    (
        "chen",
        "Lumora termination letter and reviews",
        "Correspondence",
        90,
        "Termination letter (24 Feb) and Chen's 2024 and 2025 performance reviews.",
        "chen_termination",
    ),
    (
        "patel",
        "Brightline remodel contract",
        "Contract",
        30,
        "Home improvement contract, signed in March.",
        "brightline_contract",
    ),
    (
        "sato",
        "Draft lease - 410 Harbor Blvd",
        "Contract",
        10,
        "Landlord's draft commercial lease (7 years).",
        "harbor_lease",
    ),
    (
        "harris",
        "Traffic collision report",
        "Incident / police report",
        15,
        "Police collision report.",
        "harris_collision",
    ),
    (
        "okafor",
        "Estate planning questionnaire",
        "Intake form",
        36,
        "Completed by Grace and Emeka.",
        None,
    ),
    (
        "nguyen",
        "Current custody order",
        "Pleading",
        72,
        "2023 stipulated judgment: 50/50 schedule.",
        None,
    ),
]

PDFS = {
    "freshmart_incident": (
        "FreshMart incident report.pdf",
        [
            "FRESHMART GROCERS, INC. - CUSTOMER INCIDENT REPORT",
            "Store: #214 Willow Glen, 1450 Lincoln Ave, San Jose, CA 95125",
            "Report no.: IR-214-0907            Prepared by: Mark Dobbins, Store Manager",
            "",
            "Date and time of incident: approximately 6:40 pm (see date on file)",
            "Location in store: Aisle 7 (laundry and cleaning), near the bottom shelf, east end",
            "Customer: Rosa Alvarez       Age: 41       Phone: on file",
            "",
            "DESCRIPTION (as reported by customer): Customer states she slipped on a liquid on the floor while",
            "reaching for paper towels and fell onto her right hand. Customer complained of severe wrist pain.",
            "",
            "CONDITION OF AREA: Blue liquid laundry detergent on the floor, approx. 3 ft by 2 ft. A cracked 150 oz",
            "detergent container was found on the bottom shelf. No wet floor sign was in place at the time of the",
            "incident. Area was cleaned and coned at 6:52 pm by associate J. Ruiz.",
            "",
            "LAST INSPECTION OF AREA: Floor sweep log for aisle 7 shows the last recorded sweep at 5:45 pm.",
            "The 6:15 pm sweep was not recorded (associate on break; coverage gap noted).",
            "",
            "WITNESSES: None identified at the scene. A customer told the front cashier (T. Lam) at about 6:25 pm",
            "that there was 'a mess in the soap aisle'. Cashier says she radioed for a clean-up; no record of the call.",
            "",
            "ACTION TAKEN: Ice pack offered. Customer declined ambulance. Photos taken (4). Customer given claim",
            "number CL-55193 for the insurer, Pacific Crest Mutual (general liability).",
            "",
            "POLICY REFERENCE: Store Safety Manual 4.2 requires floor sweeps of every aisle every 30 minutes,",
            "logged on the sweep sheet, and an immediate response to any reported spill with a wet floor sign.",
            "",
            "Signed: Mark Dobbins, Store Manager",
        ],
    ),
    "chen_termination": (
        "Lumora termination letter and reviews.pdf",
        [
            "LUMORA HEALTH SYSTEMS - HUMAN RESOURCES",
            "RE: Separation of employment - David Chen, Senior Billing Analyst, Revenue Cycle",
            "",
            "Dear David,",
            "This letter confirms that your employment with Lumora Health Systems ends effective today, February 24,",
            "as part of an organizational restructuring of the Revenue Cycle department. Your position has been",
            "eliminated. You will receive your final pay, including accrued PTO, today. Benefits continue through",
            "the end of the month; COBRA information will follow. Please return your badge and laptop.",
            "Sincerely, Paula Grant, HR Business Partner",
            "",
            "ANNUAL PERFORMANCE REVIEW 2024 - David Chen",
            "Overall rating: Exceeds expectations (4 of 5)",
            "Manager comments (R. Halvorsen): 'David is our most accurate analyst. His audit of E/M coding in Q3",
            "found $310k of billing errors before submission. Recommended for promotion consideration.'",
            "",
            "ANNUAL PERFORMANCE REVIEW 2025 - David Chen",
            "Overall rating: Exceeds expectations (4 of 5)",
            "Manager comments (R. Halvorsen): 'Consistently strong. Takes compliance seriously. Goal for next year:",
            "lead the coding accuracy working group.'",
            "",
            "ATTACHMENT - email, February 3, from David Chen to compliance@lumora.example:",
            "'I am reporting a pattern of E/M visits billed at level 99215 where documentation supports 99213,",
            "mostly from the Redwood City clinic, about 140 claims since October, many to Medicare. I raised this",
            "with my manager in January and was told to leave it. Spreadsheet attached.'",
            "",
            "Job posting, April 9 (Lumora careers site): 'Senior Billing Analyst, Revenue Cycle - Redwood City'.",
        ],
    ),
    "brightline_contract": (
        "Brightline home improvement contract.pdf",
        [
            "BRIGHTLINE CONTRACTORS, INC. - HOME IMPROVEMENT CONTRACT",
            "Contractor: Brightline Contractors, Inc., CSLB License No. 1048832, Pleasanton, CA",
            "Owner: Arjun Patel, 17 Oak Knoll Ct, Danville, CA 94506",
            "",
            "1. SCOPE: Full remodel of kitchen and primary bathroom per plans dated Feb 28 (cabinets, quartz",
            "counters, plumbing, electrical, tile, fixtures). Permits pulled by Contractor.",
            "2. CONTRACT PRICE: $84,500.",
            "3. DOWN PAYMENT: $8,450 due at signing.",
            "4. SCHEDULE OF PROGRESS PAYMENTS: demolition complete $12,000; rough plumbing and electrical",
            "$18,000; cabinets installed $13,550; counters and tile $16,000; final completion $16,500.",
            "5. APPROXIMATE START DATE: April 1. APPROXIMATE COMPLETION DATE: July 15.",
            "6. DELAYS: Contractor is not responsible for delays caused by the Owner, weather, or material",
            "shortages, but must give written notice of any delay within 5 business days.",
            "7. CHANGE ORDERS: Must be in writing and signed by both parties.",
            "8. ABANDONMENT: If Contractor stops work for more than 20 consecutive days without cause, Owner may",
            "terminate on 10 days' written notice and complete the work with others; Contractor is liable for",
            "the reasonable cost to complete in excess of the unpaid balance.",
            "9. DISPUTES: Mediation first, then binding arbitration in Alameda County (initialed by both parties).",
            "10. ATTORNEY FEES: The prevailing party in any action to enforce this contract recovers attorney fees.",
            "",
            "NOTICE: The law limits the down payment on a home improvement contract to $1,000 or 10 percent of the",
            "contract price, whichever is less. (Printed notice required by the Contractors State License Board.)",
            "",
            "Signed March 10 by Rick Dalton (President, Brightline) and Arjun Patel.",
        ],
    ),
    "harbor_lease": (
        "Draft lease 410 Harbor Blvd.pdf",
        [
            "COMMERCIAL LEASE (DRAFT) - 410 HARBOR BLVD, OAKLAND, CA 94607",
            "Landlord: Harbor Point Properties LP        Tenant: Sato Holdings LLC (dba Kumo Cafe)",
            "",
            "1. TERM: Seven (7) years from the Commencement Date.",
            "2. BASE RENT: $9,800 per month in year 1, increasing 4% each year.",
            "3. OPERATING EXPENSES (CAM): Tenant pays its pro rata share (6.2%) of Operating Expenses, which",
            "include property taxes, insurance, repairs, management fees (5% of gross rents) and capital",
            "improvements amortized over their useful life. No cap on annual increases.",
            "4. USE: Cafe and bakery; no cooking requiring a Type I hood without Landlord's consent.",
            "5. ASSIGNMENT AND SUBLETTING: Tenant may not assign this Lease or sublet any part of the Premises",
            "without Landlord's prior written consent, which Landlord may withhold in its sole discretion. A",
            "transfer of more than 49% of the ownership of Tenant is an assignment. Landlord may recapture the",
            "Premises on any request to assign, and receives 50% of any consideration paid for the assignment.",
            "6. PERSONAL GUARANTY: Kenji Sato personally guarantees all of Tenant's obligations for the full",
            "Term and any extension. The guaranty does not reduce or expire.",
            "7. OPTION TO RENEW: One option of five (5) years at the greater of (a) 104% of the last year's rent",
            "or (b) Fair Market Rent as determined by Landlord. Notice no earlier than 12 and no later than 9",
            "months before expiration.",
            "8. SECURITY DEPOSIT: Three months' Base Rent.",
            "9. TENANT IMPROVEMENTS: Landlord allowance of $40 per square foot (1,850 sq ft).",
            "10. DEFAULT: Late charge 10% after 5 days; Landlord may accelerate all rent for the remaining Term.",
        ],
    ),
    "harris_collision": (
        "Traffic collision report.pdf",
        [
            "TRAFFIC COLLISION REPORT (summary)",
            "Location: I-280 northbound, 0.3 mi south of Bird Ave overcrossing, San Jose, CA",
            "Time: approx. 5:20 pm, weekday. Weather: clear. Road: dry. Traffic: heavy, stop and go.",
            "",
            "Party 1: Gary Vance, 2019 Ford F-150. Insurance: Pacific Crest Mutual, policy no. on file.",
            "Party 2: Monique Harris, 2017 Honda Civic. Insurance: on file.",
            "",
            "NARRATIVE: P2 was stopped in the #2 lane for slowing traffic. P1 approached at an estimated 30-35",
            "mph and collided with the rear of P2. P1 stated: 'I looked down at my phone for a second and when I",
            "looked up she was stopped.' Skid marks: 18 ft (P1). Airbags did not deploy in V2.",
            "",
            "INJURIES: P2 complained of neck and lower back pain; declined transport; advised to see a doctor.",
            "",
            "PRIMARY COLLISION FACTOR: VC 22350 (unsafe speed for conditions) - Party 1.",
            "Associated factor: VC 23123.5 (handheld wireless device) - Party 1.",
            "Citation issued to P1: Yes.",
        ],
    ),
}


def pdf(lines: list[str]) -> bytes:
    """A one-page (or longer) text PDF, Helvetica 10pt: enough for a demo document."""
    per = 60
    pages = [lines[i : i + per] for i in range(0, len(lines), per)] or [[]]
    objs: list[bytes] = []
    kids = []
    font_id = 3
    for n, page in enumerate(pages):
        content = (
            "BT /F1 10 Tf 13 TL 50 790 Td "
            + " ".join(
                "(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '"
                for ln in page
            )
            + " ET"
        )
        stream = zlib.compress(content.encode("latin-1", "replace"))
        page_id, content_id = 4 + 2 * n, 5 + 2 * n
        kids.append(page_id)
        objs.append(
            (
                page_id,
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 "
                f"{font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode(),
            )
        )
        objs.append(
            (
                content_id,
                b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)
                + stream
                + b"\nendstream",
            )
        )
    objs = [
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (
            2,
            f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode(),
        ),
        (3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"),
    ] + objs
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for oid, body in sorted(objs):
        offsets[oid] = len(out)
        out += b"%d 0 obj\n" % oid + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % offsets[i] for i in range(1, len(objs) + 1))
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def seed(at_: Airtable) -> None:
    c = at_.create(
        "Contacts",
        [
            {
                "Name": n,
                "Type": t,
                **({"Email": e} if e else {}),
                "Phone": p,
                "Company": co,
                "Address": a,
                **({"Lead source": s} if s else {}),
                "Created": day(-ago),
                "Notes": no,
            }
            for _, n, t, e, p, co, a, s, ago, no in CONTACTS
        ],
    )
    cid = {row[0]: rec["id"] for row, rec in zip(CONTACTS, c)}
    rows = []
    for (
        key,
        name,
        num,
        area,
        stage,
        atty,
        juris,
        opened,
        incident,
        sol,
        value,
        clients,
        opp,
        summary,
    ) in MATTERS:
        r = {
            "Matter name": name,
            "Matter number": num,
            "Practice area": area,
            "Stage": stage,
            "Status": "Closed" if stage.startswith("Closed") else "Open",
            "Client": [cid[k] for k in clients],
            "Opposing parties": [cid[k] for k in opp],
            "Responsible attorney": atty,
            "Jurisdiction": juris,
            "Opened": day(-opened),
            "Summary": summary,
        }
        if incident is not None:
            r["Incident date"] = day(-incident)
            if sol:
                inc = TODAY - dt.timedelta(days=incident)
                r["Statute of limitations"] = inc.replace(year=inc.year + sol).isoformat()
        if value:
            r["Estimated value"] = value
        rows.append(r)
    m = at_.create("Matters", rows)
    mid = {row[0]: rec["id"] for row, rec in zip(MATTERS, m)}
    # the opposing counsel and witness hang off the matter through notes; link them as opposing parties too
    at_.update("Matters", mid["alvarez"], {"Opposing parties": [cid["freshmart"], cid["pike"]]})
    at_.update("Matters", mid["nguyen"], {"Opposing parties": [cid["becker"], cid["liu"]]})

    at_.create(
        "Tasks",
        [
            {
                "Task": t,
                "Matter": [mid[k]],
                "Due": day(d),
                "Assigned to": who,
                "Priority": p,
                "Status": s,
                "Details": det,
            }
            for k, t, d, who, p, s, det in TASKS
        ],
    )
    at_.create(
        "Events",
        [
            {
                "Event": e,
                "Matter": [mid[k]],
                "Type": ty,
                "Start": at(*when),
                "Location": loc,
                "Attendees": who,
                "Details": det,
            }
            for k, e, ty, when, loc, who, det in EVENTS
        ],
    )
    at_.create(
        "Notes",
        [
            {"Title": t, "Matter": [mid[k]], "Author": a, "Date": day(-ago), "Body": b}
            for k, t, a, ago, b in NOTES
        ],
    )
    docs = at_.create(
        "Documents",
        [
            {
                "Document": n,
                "Matter": [mid[k]],
                "Category": cat,
                "Received": day(-ago),
                "Description": desc,
            }
            for k, n, cat, ago, desc, _ in DOCUMENTS
        ],
    )
    for row, rec in zip(DOCUMENTS, docs):
        if key := row[5]:
            filename, lines = PDFS[key]
            at_.upload(rec["id"], "File", filename, pdf(lines), "application/pdf")
    print(
        f"data: {len(c)} contacts, {len(m)} matters, {len(TASKS)} tasks, {len(EVENTS)} events, "
        f"{len(NOTES)} notes, {len(docs)} documents ({sum(1 for d in DOCUMENTS if d[5])} with files)"
    )


def wipe(at_: Airtable) -> None:
    for t in reversed(TABLES):
        ids = [r["id"] for r in at_.list(t, fields=[])]
        for i in range(0, len(ids), 10):
            at_._req(
                "DELETE",
                f"{API}/{at_.base}/{t}",
                params=[("records[]", x) for x in ids[i : i + 10]],
            )
        print(f"reset: deleted {len(ids)} {t.lower()}")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--reset", action="store_true", help="delete the demo tables' records, then seed again"
    )
    args = p.parse_args()
    at_ = Airtable()
    build_schema(at_)
    if args.reset:
        wipe(at_)
    elif at_.list("Matters", fields=[], max_records=1):
        raise SystemExit("data: Matters already has records; use --reset to start over")
    seed(at_)


if __name__ == "__main__":
    main()
