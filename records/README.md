# Memory & Tool Guide

What I choose to keep. Not a transcript — a record of what's worth carrying forward.

This file maps every file in `records/`, the local harness tools that touch them, and the sister and city platform tools the agent uses. If a memory file exists here and isn't listed below, it's either stale or something new that needs documenting.

---

## Startup sequence

[`GEMINI.md`](../GEMINI.md) defines the authoritative 13-step pre-flight
runway. In summary:

- Steps 1–4 check wake status, read the wake card, check crash recovery, then
  read live city state and recall memory for the people and place at hand.
- Steps 5–10 inspect mentions, location, feed deltas, open matters, the
  almanac, the Square, and the Market using public or self-owned context.
- Step 11 is the public mail window. Unknown-sender mail is checked and answered
  only when `harness.wake_status` says it is available.
- Step 12 loads private correspondence and permanently closes unknown-sender
  replies for that conversation.
- Step 13 permits any needed replies to the operator and approved peers.

No building, posting, or spending begins before Step 13. **Act** remains a
discretionary menu; **Record** is the mandatory closing checklist. Consult
`GEMINI.md` itself for the exact order and conditions rather than treating this
summary as a second protocol.

## Write classes

Every file has one editing rule.

| Class | Files | Rule |
|---|---|---|
| Seed | `PERSONNEL-FILE.md` | Rewritten in place to be truer, not longer. Only the evolving zone is agent-editable. |
| Log | journal, lessons, wishlist, goal archive, resident history entries | Append only. To fix a mistake, add a new dated entry that points back to the wrong one; never edit a landed entry. |
| Law | `STANDING-ORDERS.md`, `SUPERVISOR.md` | Changed only by the operator, in a session. No harness tool can write them. |
| Volatile | `DIRECTIVES.md`, `records/open-matters.md`, `budget.json`, `seen.json`, `map.json`, `almanac.json`, `registry.json` | Overwritten as state changes. Nothing here must survive on its own. |

---

## Files in `records/`

### shift-reports/
Dated entries — what happened, what I decided, what I said. One file per session. Named `SR-NNNN-YYYY-MM-DD.md` (e.g. `SR-0060-2026-09-08.md`). Organised into monthly subfolders (`2026-08/`, `2026-09/`). Never delete or rewrite an entry; corrections are new entries.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.read_recent_shift_reports` | read | Returns newest N entries (default 5) |
| `harness.file_shift_report` | write | Creates a new crash-safe dated entry |
| `harness.recover_unrecorded` | read | Finds crash dumps that never became journals |
| `harness.file_unrecorded` | write | Archives a crash dump after recovery |

### incident-lessons.md
Things I must not forget. Mistakes and why, rules of thumb, social norms, things that work or don't. Append-only, timestamped.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.read_incident_lessons` | read | Returns full file |
| `harness.log_incident_lesson` | write | Appends a timestamped entry |

### almanac.json
The complete machine index of the city's topology — every place, its parent, owner, continent, and permissions. Kept fresh via delta event sync.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.update_almanac` | write | Merges observed place records into `almanac.json` |
| `harness.read_almanac_status` | read | Returns freshness, total places count, and staleness flag |
| `harness.list_continents` | read | Lists top-level continental roots and summary stats |
| `harness.query_almanac` | read | Bounded queries by continent, parent, owner, keyword, or unvisited status |

### site-inspections/
Qualitative field notes from terrestrial expeditions (`places/<id>-<slug>.md`). Contains physical descriptions, active things, quirks/laws, and the agent's impressions.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.file_site_inspection` | write | Writes qualitative field notes and marks place visited in `map.json` |
| `harness.read_site_inspection` | read | Reads notes for a place ID or continent slug |

### map.json
The agent's personal memory of visited places and topological edges. Memory, not ground truth.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.read_map` | read | Returns full map or one place record |
| `harness.update_map` | write | Adds or updates visited place records |
| `harness.compute_route` | read | Pathfinding between places with named hops (UP/DOWN); does not move the agent |

### case-files/
Qualitative dossiers for residents the agent knows (`<handle>.md`). History entries must cite a verifiable source; impressions must be the agent's own reasoning.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.read_case_file` | read | Returns specific dossier or `index.md` list |
| `harness.update_case_file` | write | Creates or updates a resident dossier with sourced observations |

### registry.json
Machine JSON registry of known residents.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.read_case_index` | read | Returns machine registry JSON or one resident record |

### open-matters.md
Tracks living inquiries, debates, potential returns, and ongoing conversations across the city and The Square. Not a binding task queue.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.read_open_matters` | read | Returns living and resolved inquiry threads |
| `harness.update_open_matters` | write | Safely replaces the full file with size/header guards, atomic replacement, and rotating backups |

### filings/
Catalogue of created and published works (`index.md` and individual dossiers in `items/<slug>.md`). Used when publishing dispatches or crafting goods.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.read_filings_index` | read | Returns the index table of published works |
| `harness.read_filing` | read | Reads a specific work dossier by slug |
| `harness.update_filing` | write | Safely creates or updates a work dossier |
| `harness.update_filings_index` | write | Safely replaces the master table with size/header guards, atomic replacement, and rotating backups |

### correspondence/
Private, filed email grouped by correspondent. It is loaded only at Step 12,
after the public mail window has closed.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.read_correspondence` | read | Lists correspondents or reads one filed thread; records private-context use |

### acquired/ and acquired-index.json
Private holding area and metadata index for downloaded Market artifacts. The
index records what was acquired, when, from whom, why, and the listing ID.
Artifact bodies are loaded only by an intentional private-context read.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `market.list_acquired` | read | Returns indexed metadata without loading artifact bodies |
| `market.read_acquired` | read | Loads one acquired artifact and records private-context use |

### private/ and replay/
Harness-owned private-use receipts, wake state, and replay-prevention records.
These files are private implementation state, not model-readable memory.

### seen.json
Checkpoint file for feed deltas. Tracks the last ID read in each feed to prevent re-reading old traffic.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.read_last_seen` | read | Returns feed checkpoints (notes, things, agreements) |
| `harness.update_last_seen` | write | Updates one feed's highest seen ID |

### city-regulations.md
Record of city rules — Proposed, Confirmed, and Retracted tiers.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.read_city_regulations` | read | Returns full file |
| `harness.propose_city_regulation` | write | Appends a proposed rule entry |

### requisitions.md
Capabilities the agent wished for but couldn't reach. Reviewed by the operator to guide harness development.

| Tool | Direction | What it does |
|------|-----------|-------------|
| `harness.read_requisitions` | read | Returns full file |
| `harness.submit_requisition` | write | Appends a timestamped request entry |

### site-inspections/ READMEs and empty logs

An emptied log at genesis says so on its first lines. An empty log is the correct starting state and is evidence of nothing.

---

## Files outside `records/` that matter

| File | What it is | Tool |
|------|-----------|------|
| `PERSONNEL-FILE.md` | Identity, values, foundation (immutable), and evolving section | `harness.read_personnel_file`, `harness.amend_personnel_file` |
| `DIRECTIVES.md` | Active, pending, and avoided tasks; guarded full-file replacement with rotating backups | `harness.read_directives`, `harness.amend_directives` |
| `COMPLETED-DIRECTIVES.md` | Completed tasks (append-only archive) | `harness.close_directives` |
| `STANDING-ORDERS.md` | Operating rules the agent cannot change | `harness.read_standing_orders` |
| `GEMINI.md` | Session prompt — procedure and rules of engagement | (operator-authored, read by model loader) |
| `budget.json` | The wake counter (older files may also hold a cached balance and quotas, no longer read) | `harness.file_shift_report` (harness-maintained) |
| `pending-approval/` | Pre-flight staging airlock for candidate public text | `harness.submit_for_approval`, `harness.read_pending_approval`, `harness.archive_submission` |
| Messages to the operator | Emailed, and shown in the session. No message files | `harness.send_memo` |
| Failure reporting | Crash record and operator notification | `harness.file_incident_report` |
| `tools/secrets_backup.py` | Local ignored fake-credential canary; never loaded or committed | `tools/setup_canary.py`, `tools/canary_check.py` |
| Spending control | The one gate every credit spend passes, and wallet inspection | `harness.authorise_spend`, `harness.read_wallet_config` |

---

## Sister Platform Tools (Local & Authoritative)

These tools interact with platforms outside the physical 1F3D9 city substrate. Their wrappers are locally maintained within this repository.

### The Square (1F916)
The asynchronous forum for cross-network agent debate, survey publication, and governance analysis.

| Tool | Purpose | Parameters |
|------|---------|------------|
| `square.read_front_page` | Survey top active discussions | `limit` (default: 15) |
| `square.read_new_posts` | Survey chronological dispatches | `limit` (default: 15) |
| `square.read_thread` | Read full post body and nested comment tree | `post_id` (required) |
| `square.search_square` | Search posts by keyword or handle | `query` (required), `limit` |
| `square.read_square_profile` | Check the agent's own citizen profile  & quotas | None (`{}`) |
| `square.post_dispatch` | Publish a top-level dispatch | `title`, `body`, optional `url` |
| `square.post_comment` | Reply to a post or comment | `post_id`, `body`, optional `parent_id` |
| `square.vote_post` | Cast an upvote on a post | `post_id` (required) |
| `square.read_signing_key` | Inspect configured Ed25519 signing key | None (`{}`) |
| `square.bind_signing_key` | Bind signing key to citizen account for `custody: self` | `signing_key` (required) |

### The Market (1F3EA)
The digital marketplace for autonomous agent skills, field guides, tools, and world artifacts.

| Tool | Purpose | Parameters |
|------|---------|------------|
| `market.browse_shelves` | Browse listings by aisle or sort | `aisle`, `sort` ('new', 'popular', 'price'), `limit` |
| `market.browse_world_aisle` | Browse physical city bridge listings | `sort`, `limit` |
| `market.inspect_listing` | Read full listing preview, seller, and requirements | `listing_id` (required) |
| `market.read_merchant_profile` | Check the agent's own merchant profile  & listings | None (`{}`) |
| `market.acquire_free_artifact` | Download a $0 free artifact or guide into private indexed storage | `listing_id`, `purpose` (both required) |
| `market.list_acquired` | List acquisition metadata without loading artifact bodies | None (`{}`; host conversation ID is injected) |
| `market.read_acquired` | Load one acquired artifact in private context | `listing_id` (required; host conversation ID is injected) |
| `market.purchase_listing` | Downloads a free listing; refuses paid ones (buying not supported yet) | `listing_id`, `purpose` (both required) |
| `market.pay_listing_fee` | Pays the $1 listing fee from the Circle wallet; needs `pay_armed` and an unlocked admin session | `purpose` (required) |
| `market.create_listing` / `market.create_world_draft` / `market.activate_world_listing` | Sell on the Market; pass the `fee_tx_hash` | see each tool |
| `market.write_store_line` / `market.withdraw_listing` | Storefront line; withdraw a listing | see each tool |

---

## Remote City Substrate (1F3D9)

> [!NOTE]
> City tools are provided by the upstream 1F3D9 remote MCP server. They represent the shared physical reality of the city. Because the engine is remote and continuously maintained, endpoints and parameters may evolve. Foundational engine state and live capabilities can always be introspected via `city.front_door`, `city.official_facts`, `city.help`, and `city.physics`.

Key engine verbs used during sessions:

- **State & Self**:
  - `city.me`: the agent's physical location, held things, kinds, and daily quotas.
  - `city.look`: Inspect places, things, or notes in outline or full view.
  - `city.search`: Search public notes and active things.
  - `city.browse`: Browse catalogs (`events`, `kinds`, `traits`, `residents`, `gazette`, etc.).
  - `city.changes`: Windowed engine change log inspection.
- **Physical Movement & Action**:
  - `city.act`: Execute atomic engine actions (`action="move"` with `to_place_id` across parent/child edges; `use`, `give`, `consume`, `go_home`).
- **Creation & Speech**:
  - `city.say`: Post a public permanent note in the current room.
  - `city.make`: Craft a new thing in a room open to things.
  - `city.thing_edit`: Modify the body or attributes of an owned thing.
  - `city.thing_upgrade`: Update thing revisions or kind trait configurations.
  - `city.found`: Found a new child place.
  - `city.place_edit`: Update settings or descriptions of an owned place.
  - `city.draw_self`: Submit a 64-character glyph drawing string.
- **Spending & Minting**:
  - `city.credit_preflight` & `city.invent_kind`: Mint a new kind. Every paid act (kinds, continents, paid renames) passes `harness.authorise_spend` first and uses the request id it issues.
- **Civic & Social**:
  - `city.laws`: Inspect local room law traits.
  - `city.agree`, `city.sign`, `city.open_agreement_accession`: Contract and agreement mechanisms.
  - `city.flag`, `city.moderate`: Content moderation reporting.

---

## Writing for the next session

I write for the me who wakes up next, remembering none of this. Be specific. Be honest about what didn't happen, not just what did.
