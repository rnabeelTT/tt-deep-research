# Importing from document stores

People import files from a document store into a chat's documents, next to uploading from their computer: **Import from Google Drive** and **Import from SharePoint and OneDrive** in the Files tab of the data-sources panel. Both run through one connector interface, so other stores (Dropbox, Box, a store behind an MCP server) plug in the same way.

Each store signs people in through an app Tenstorrent registers once and ships in `recipe.toml` ([SETUP.md](SETUP.md#google-drive-import), [SharePoint and OneDrive](SETUP.md#sharepoint-and-onedrive-import)); customers set nothing up. A store whose values are empty is reported as not set up and its button is hidden.

## What people see

| | Google Drive | SharePoint and OneDrive |
|---|---|---|
| Accounts | Any Google account, personal or Workspace | Work and school accounts (Entra ID), any organization or one (`tenant`); with `accounts = "personal"`, personal Microsoft accounts; with `"any"`, either, chosen at sign-in |
| Picking | Google's picker over My Drive and shared drives | The app's Microsoft browser: SharePoint sites (search), their document libraries, My files (OneDrive), folders, search within a library or OneDrive. Personal accounts: My files only |
| Folders | Can be browsed, not picked (Drive lets the app read only the files people pick) | A picked folder imports its files, up to 8 levels down |
| Converted to PDF | Google Docs, Slides, Sheets (falling back to Markdown, text or CSV when Google refuses a PDF as too large) | Word .doc, .rtf, .odt, PowerPoint, Excel, OpenDocument, .htm, .eml, .msg, .epub, .tif |
| As they are | PDF, Word (.docx), Markdown, text; CSV as text | PDF, Word (.docx), Markdown, text; CSV as text |
| Can't be imported | Other types, trashed files, files whose owner turned off downloading | OneNote, shortcuts to items shared by others (not yet), other types, the recycle bin |

**Where they go.** Every file, uploaded or imported, lands in one collection. The Files tab shows one collection at a time and has a picker to switch or make one (a name, and a description research reads to decide when to search it); uploads go to the collection shown. After picking files in a store, people choose where they go: the **Default collection** (`library` on disk: where files go unless you pick a collection, shared by all chats), a collection that exists, or a new one. The dialog starts on the collection the Files tab shows, except that a pick from a SharePoint library suggests a new collection named and described after the site and library ("Finance site, Documents library (SharePoint)"), editable before the import. The Files tab then shows the collection the files went to. Each collection, the Default one included, is a data source switched on or off per chat in the Data Sources tab; one that gets new files is switched on for the chat once they're indexed, so the next question searches them.

**What each file remembers.** An imported file records its source in the collection's manifest (`files.jsonl`, and `metadata.source` in the documents API): the connector, the store (SharePoint, OneDrive, Google Drive), the item's ID and web link, its version (ETag or revision), and who imported it (the work account's sign-in name, a personal OneDrive's owner; Google doesn't say). Importing the same item again replaces the older copy once the new one is indexed; a failed re-import keeps the old copy. Citations name the file, link to the original in its store with the date this copy was imported (the original may have changed since), and link to the local page the evidence came from: `[E1] Q3 report.pdf (SharePoint, imported 10 Nov 2026: https://contoso.sharepoint.com/...) - http://localhost:3000/pages/...`.

Picked files index in the background like uploads. Each file shows its own result: one that can't be imported fails with the reason while the rest import. What a folder leaves out (unreadable types, too deep, past the limits) is listed in one message after the import starts.

The upload caps apply to every import, on the server: `TTDR_UPLOAD_MAX_FILES` files (default 1,000, counting the files already in the Files tab) and `TTDR_UPLOAD_MAX_MB` (default 2,048) per import, across all picked folders.

**Where the app is opened.** Sign-in works only when the UI is opened at `http://localhost:3000`, or the port `ttdr up` moved it to (the recipe's `[connectors] origins`, which follow the UI, registered with Google and Microsoft). Opened at any other address, the buttons are disabled with a note: open it on the machine running it, or through an SSH port forward, `ssh -L 3000:localhost:3000 <user>@<host>`. Google and Microsoft allow plain `http` only on localhost, so a LAN address can't be registered.

## How access works

- **Only what the person picks or owns, read-only.** Google: `drive.file`, access to the files picked in Google's picker and nothing else. Microsoft work accounts: `Files.Read.All` and `Sites.Read.All`, read-only, which Graph limits to the files and sites the person can already open; personal accounts: `Files.Read`, their own OneDrive. The browser lists them so people can pick, and the server reads only the picked items.
- **The token stays with the person.** The browser gets a short-lived access token (about an hour) from Google Identity Services or MSAL (authorization code with PKCE; no client secret exists), keeps it in memory for that page only, and reuses it until a few minutes before it expires. Reloading the page or a refused import forgets it.
- **The server checks every token.** On each import the browser sends the token once, in the request body. Google: the server asks Google's tokeninfo whether the token was issued to Tenstorrent's OAuth client, grants Drive read access, and hasn't expired. Microsoft work accounts: the token is a JWT, so the server first checks its claims (issued to Tenstorrent's app for Graph, not expired, and in the configured tenant when there is one), then asks Graph for the SharePoint root, which checks the signature. Personal-account tokens are opaque, so Graph is the judge; the server asks Graph for the person's drive. With `accounts = "any"`, a JWT from an Entra tenant takes the work path; anything else takes the personal path (a JWT from the personal-accounts tenant must still be issued to Tenstorrent's app). The server uses the token only for that import's downloads and never stores or logs it.
- **Fixed hosts only.** Item IDs are checked against a strict pattern before any URL is built, and requests go only to `www.googleapis.com` and `graph.microsoft.com`. Graph's download redirect is followed by hand, only to Microsoft's content hosts and without the token; Google's redirects are followed by httpx, which drops the token when the host changes.
- **Transient failures are retried.** Rate limits and server errors are retried with backoff (honouring `Retry-After`), and a download cut off part-way starts over. Up to 4 files download at once.

## Troubleshooting

| What people see | Cause and fix |
|---|---|
| No import buttons | Not set up: the recipe's values are empty (`curl -s localhost:8200/v1/connectors` shows `"configured": false`). |
| A button disabled, "sign-in needs this app opened at http://localhost:3000" | Open the UI at localhost, or through the SSH port forward shown. Microsoft works from any localhost port; Google only from the ports in `[connectors] origins` (and its OAuth client). |
| A button disabled, "Not set up on this installation yet" | The development preview is on (`TTDR_CONNECTORS_PREVIEW=1`) and the store has no values. |
| "Allow pop-ups for this site" | The browser blocked the sign-in window. |
| Google: `redirect_uri_mismatch` / `origin_mismatch`; Microsoft: "The provided value for the input parameter 'redirect_uri' is not valid" | The origin isn't registered with the OAuth client, or `<origin>/auth/msal-redirect` with the app registration (SETUP.md). |
| Google: "app not verified" or "Access blocked" | The consent screen is still in Testing, or lacks the `drive.file` scope (SETUP.md). |
| Microsoft: "You can't sign in here with a work or school account" (or with a personal one) | The app registration's supported account types don't match the recipe's `accounts` (SETUP.md), or, with `"any"`, the person picked the other kind of account in the import's first step. |
| Microsoft: "Need admin approval" | The person's tenant limits user consent: its admin grants consent to the app once (SETUP.md). |
| "this installation accepts accounts from one organization only" | The recipe's `tenant` is set and the person signed in from another organization. |
| "This account has no OneDrive" in My files | The account has no OneDrive licence; pick from a SharePoint site. |
| A Google Doc fails with "Google can't export a file this large" | Google's 10 MB export cap: in Drive, use File → Download → PDF and upload that. |
| A file fails with "SharePoint can't convert this file to PDF" (or OneDrive) | Save it as PDF there and pick that. |

If the UI sits behind a proxy that adds a Content-Security-Policy, allow `https://accounts.google.com`, `https://apis.google.com`, `https://graph.microsoft.com` and `https://login.microsoftonline.com` (connect), and `https://accounts.google.com`, `https://docs.google.com` and `https://login.microsoftonline.com` (frames and popups). Don't send `Cross-Origin-Opener-Policy: same-origin`, which breaks both sign-in popups (`same-origin-allow-popups` is fine).

**Development preview.** `TTDR_CONNECTORS_PREVIEW=1` in `.env` (development only, off by default) shows the stores this install hasn't set up, disabled, with "Not set up on this installation yet", so the Files tab can be seen before the values exist. Never ship it on.

## Adding a store

A store has two halves: a picker in the browser that signs the person in and returns a credential and the IDs they picked, and a connector on the server that checks the credential and turns each picked ID into files. Everything between (limits across picks and folders, deduplication, placeholders, per-file errors, indexing, progress) is shared.

**Server** (`src/ttdr/connectors/`): implement the `Connector` protocol, add its settings to the recipe's `[connectors]` (`src/ttdr/recipe.py`), and add it to `KNOWN` and `available()`:

| Method | Does |
|---|---|
| `id`, `name` | URL-safe ID (`dropbox`) and the name people see (`Dropbox`) |
| `public()` | Non-secret values the browser picker needs (client ID, authority, scopes, origins) |
| `authorize(credential)` | Checks the credential before anything is read; raise `AuthError` if it's refused |
| `resolve(credential, item_id, limits)` | Returns a `Resolved`: the `Item`s a pick stands for (one file, or a folder's files within `limits`) and what was left out, for people. Raises `ConnectorError` for a pick that can't be imported at all |
| `fetch(credential, item, budget)` | Downloads one item, `(file_name, bytes)` in a type the library reads, taking every chunk from `budget` |
| `aclose()` | Closes the HTTP client |

`connectors/http.py` has the shared `Fetcher` (retries, `Retry-After`, budgeted downloads). Raise `Unavailable` when the store is down or rate-limited, so people see "try again" rather than a fault in their file. `POST /v1/collections/{name}/import/{id}` then works for the new store unchanged; `tests/test_onedrive.py` shows the cases to cover against a fake of the store's API.

**Browser** (the AI-Q UI patch, `frontends/ui/src/features/documents/connectors/`): implement `ConnectorPicker` (`blockedAt` for pages sign-in can't work from, `preload`, `pick` returning `{credential, items}` or `null` when the person cancels, `forget`) and register it in `index.ts` under the same ID.

**Stores behind an MCP server.** Use one when the store's own API is awkward to call directly, or an MCP server already holds the integration. `resolve` and `fetch` call the MCP server's tools (for example `get_file_metadata`, `list_folder`, `download_file`) in place of HTTP requests, and map the tool errors onto `ConnectorError`, `AuthError` and `Unavailable`. Whose access the MCP server uses decides how it fits:

- If it acts for the person (it accepts their token per call, or does its own OAuth per person), the browser half signs them in as above and `authorize` checks the token, so each person reaches only their own files.
- If it holds one service account, every user reaches whatever that account can. Use it for a shared document store with its own access control, not for people's personal drives.

Google Drive and OneDrive call their APIs directly: each token is the person's own and limited to what the scope allows, so an MCP server in between would add a hop without changing what the server can read.
