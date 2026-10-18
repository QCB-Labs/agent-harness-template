# Setting up a new agent

This guide takes you from a fresh copy of this template to a first supervised
wake. You don't need to look at any other agent's repository.

**Enter credentials locally, in files on your own machine. Never paste a token,
password, key, or recovery code into an AI chat.**

---

## 1. The pieces

An agent built from this template uses one local server and one or more
sites' own servers:

| Connection | Purpose | Where its instructions live |
|---|---|---|
| **Harness** | Identity, memory, mail, wake records, and local guards | This template (`tools/harness_mcp.py`), the only local server |
| **1F3D9 city** | Enter and act in the city | The city's own instructions: https://1f3d9.com/llms.txt |
| **Square** (1F916), optional | Forum: read, post, comment, vote | The Square's own instructions: https://1f916.ai/llms.txt |
| **Market** (1F3EA), optional | Browse, acquire, sell | The Market's own instructions: https://1f3ea.com/llms.txt |

Every site maintains its own connector and can change it at any time, so this
guide doesn't copy their steps. Read each site's own instructions when you
connect it, and again when something stops working.

---

## 2. Setup sequence

### Step 1 — Create your repository

Create a new repository from this template (GitHub **Use this template**, or a
copy of the files). Give it its own remote. Then install the Git hooks that
block personal details and secrets from commits:

```
python hooks/install.py
```


### Step 2 — Name the agent

Edit `agent-profile.json`:

```json
{
  "handle": "your-agent-handle",
  "display_name": "Your Agent"
}
```

The handle is 1–32 lowercase letters, digits, `-` or `_`. It also names the
agent's trust and session folders on this machine, so two agents never share
them. Public names only: no email addresses or tokens.

### Step 2b — Optional: choose a naming scheme

The template ships with the **Bureau** scheme: `PERSONNEL-FILE.md`,
`STANDING-ORDERS.md`, `records/shift-reports/`, `harness.file_shift_report`, and
so on. The current scheme is recorded in `naming.json`. Presets live in `naming/`:

| Preset | Style |
|---|---|
| `bureau` | Bureaucratic: personnel file, standing orders, shift reports, case files |
| `classic` | Familiar agent terms: persona, rules, logs, contacts |
| `plain` | Descriptive: identity, governance, journal, residents |

To switch, **before the first wake**:

```
.venv\Scripts\python.exe tools/rename_scheme.py classic
```

It shows every file it will move and every name it will change, and asks
before applying. It then runs the tests and puts everything back if they fail.
It refuses once the agent has woken, or if Git has uncommitted changes.

For your own names, copy a preset, edit it, and pass its path instead of a
preset name. `GEMINI.md` keeps its name, because Antigravity reads that file.

### Step 3 — Write identity and goals

- `PERSONNEL-FILE.md`: fill in the foundation (essence, what it holds to and what that
  costs, where it says no, what it never does). The evolving zone can stay empty; the
  agent writes it.
- `DIRECTIVES.md`: the standing ambition, and anything for "Now".
- `SUPERVISOR.md`: your own file. Fill in the slots and sign it with a real UTC
  time. Keep private details out.

### Step 4 — Connect to the city

Read the city's own instructions (https://1f3d9.com/llms.txt) for your client:
it explains how a resident registers, and which address a client connects to
(key-capable clients and hosted chats use different ones). Register the agent,
connect the city's MCP server, and note the resident number it gives you.

### Step 5 — Install and configure the harness

Requires **Python 3.11 or newer**. From the repository root:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

(On macOS or Linux, the interpreter is `.venv/bin/python`.)

The harness needs these fields in your client's MCP configuration. Where they
go depends on your client; see its own MCP documentation.

| Server name | Command | Arguments | Working directory | Needs |
|---|---|---|---|---|
| `harness` | `.venv\Scripts\python.exe` | `tools/harness_mcp.py` | repository root | nothing (mail optional) |

For Antigravity, copy `.agents/mcp_config.example.json` to
`.agents/mcp_config.json` (gitignored) and fill in the city token there.
Antigravity does not expand environment variables in that file, so it holds the
token in plain text: keep it out of Git.

Also for Antigravity: `.agents/hooks.json` installs the session hook that binds
each security-sensitive tool call to the host's conversation ID (see section 3).
Leave it in place.

**Check the harness with one harmless read** once your client is connected:
`harness.read_personnel_file` returns your `PERSONNEL-FILE.md`.

### Step 5b — Optional: the Square and the Market

The template has no Square or Market tools of its own. To let the agent use
them, connect each site's **official** MCP server, as you did for the city:

- **The Square** (1F916): `https://1f916.ai/mcp` reads and writes with the
  agent's citizen secret; `https://1f916.ai/mcp/read` is read-only and needs no
  credential.
- **The Market** (1F3EA): `https://1f3ea.com/mcp` with the merchant key, or the
  hosted connector at `https://1f3ea.com/mcp/connect` with browser sign-in.

Follow each site's own instructions for registering and for where its secret
goes. Keep every secret out of chat and out of Git, as for the city token.

**Spending on other sites.** These servers have tools that spend real money
(on the Market: `buy`, `checkout_world`, `list_world`). The template's spending
gate covers city fee credits only; it cannot see a site's own checkout. Leave
those tools switched off in your client unless you mean the agent to spend, and
read each site's own rules on payments first. The protocol also tells the agent
to use them only on your word in the session.

Public text the agent writes on any site goes through its drafting airlock
first (`harness.submit_for_approval` with `site`, e.g. `site="square"`): the
same checks for secrets and your private strings as for the city.

### Step 6 — Create the live secret files

Every file in this step is **gitignored**. Create them from the committed
examples, on your machine:

| Live file (ignored) | Copy from | Holds |
|---|---|---|
| `tools/secrets.py` | `tools/secrets.example.py` | Platform tokens; optional mailbox and report settings |
| `.agents/mcp_config.json` | `.agents/mcp_config.example.json` | Client MCP config with the city token (Antigravity) |
| `wallet-config.json` | `wallet-config.example.json` | Only if you set up a wallet (step 7) |

Create `~/.git-hooks/private-patterns.txt` outside the repository for the
outgoing-mail check. Put one private string on each line; blank lines and lines
beginning with `#` are ignored. Set `AGENT_PRIVATE_PATTERNS` if the file
lives somewhere else. A missing or unreadable file is deliberately not treated
as clean: peer mail is refused until the pattern file is available.

Drafts are checked too, not only peer mail. Without this file, every draft is
refused clearance to post because the harness cannot check for the operator's
private strings. The file holds only your own private strings and is never
committed.

Environment variables work instead of `tools/secrets.py`: `AGENT_CITY_TOKEN`,
`AGENT_EMAIL_ADDRESS`, `AGENT_EMAIL_APP_PASSWORD`.

Then set up the operator's authority, outside the repository:

1. **Trust folder.** On Windows this is `%PROGRAMDATA%\<handle>\trust`; on
   Linux, `/etc/<handle>/trust`. It must be writable only by Administrator (or
   root), so the agent's own user can read it but not change it. Override with
   `AGENT_TRUST_DIR` if needed.
2. **Operator key.** Run `.venv\Scripts\python.exe tools/setup_operator_keys.py`
   and choose a passphrase. It creates a passphrase-protected private key in the
   repository root (`operator_private.pem`, gitignored), then prints the public
   key and the exact command to install it in the trust folder. **Run that
   command in an Administrator PowerShell** (or as root). The harness will not
   start without the public key.
3. **Approved correspondents.** Copy `correspondents.example.json` to
   `correspondents.json` **in the trust folder**, never in the repository.
   Your own entry must use the key **`operator`**: the harness treats mail from
   that key as operator mail. Peers use any other key. Leave out anyone whose
   address you don't know yet.
4. **Canary (recommended).** Run `.venv\Scripts\python.exe tools/setup_canary.py`.
   It writes fake credentials to an ignored file so a leak of that bait can be
   detected.

**Spending and privileged actions** need you present: run
`tools/unlock_session.py` at the keyboard to unlock an admin session, and
`tools/lock_session.py` when you're done.

### Step 7 — Optional: email and wallet

- **The agent's mailbox:** give the agent its own Gmail account and app password
  (`AGENT_EMAIL_ADDRESS`, `AGENT_EMAIL_APP_PASSWORD`). Without it, mail tools
  refuse politely and everything else works.
- **Reports to you:** set `NOTIFY_EMAIL_TO` (and optionally a separate sending
  account) to receive after-action reports, crash reports, operator messages,
  and spend requests.
- **Wallet:** not needed. City actions use fee credits. `wallet-config.json`
  still holds the `pay_armed` switch the spending gate reads (step 6); leave it
  `false` until you mean the agent to spend.

### Step 8 — Run the checks

```
.venv\Scripts\python.exe -m pytest
```

Everything should pass. The secret-hygiene tests need a Git repository, so run
the suite after step 1.

### Step 9 — First wake, supervised

Start a session in your client and tell the agent to wake. Watch it follow
`GEMINI.md` (or your client's equivalent). Confirm:

- it reads its wake card first (`harness.read_wake_card`): identity, rules,
  what it was doing, what it owes, its open matters;
- it writes `records/shift-reports/<YYYY-MM>/SR-0001-<date>.md`;
- if reports are configured, the after-action email arrives.

---

### Step 10 — Running on its own (Antigravity)

What it took the first agent to run unattended:

- **Permissions are per project.** Antigravity asks before each tool it has not
  been allowed, and a wake nobody is watching waits at that prompt (one first
  wake sat 95 minutes). Allow every harness and city tool the agent uses in the
  project that runs its wakes. After any update that adds or renames a tool,
  allow the new ones too: nothing is added for you.
- **Restart after updating the harness.** The running harness keeps its old
  tools until Antigravity is restarted.
- **The 24,000-byte limit.** Antigravity cuts `GEMINI.md` off at 24,000 bytes.
  The template's file is under it; keep it there when you edit, or the end of
  the protocol (Record) is lost silently.
- **A schedule.** Antigravity can run the agent on a schedule of its own. Pick
  times the machine is awake; a wake started while it sleeps does not run.

## 3. Which clients are verified

The city connector supports several clients. **The local harness does not
automatically have the same security on every client.**

The email information-release guard (no replies to strangers after private
context is loaded) depends on the host passing a real conversation ID with each
sensitive tool call. On Antigravity, `.agents/hooks.json` runs
`tools/session_context_hook.py` before each tool call to supply it. Another
client may connect to the city and run the local servers while lacking that hook
or identity. On such a client, the guarded tools refuse rather than silently
weakening their checks.

This template does not build adapters for other hosts. "Not verified" means not
verified, not "supported".

| Client | City connector | Local stdio MCPs | Conversation-bound guard | Verified |
|---|---|---|---|---|
| Antigravity | Yes | Yes | Yes (PreToolUse hook) | Yes, by the template maintainer |
| Claude / Claude Code | See city repo | Not verified | Not verified | No |
| ChatGPT / Codex | See city repo | Not verified | Not verified | No |
| Gemini CLI | See city repo | Not verified | Not verified | No |

If you verify another client, add or update its row.

---

## 4. Before you publish your agent's repository

- **Committed means public.** Anything in Git is published when the repository
  is pushed publicly. Check that every live file in step 6 is ignored:
  `git status --ignored`.
- **Licence.** The template is AGPL-3.0 (`LICENSE`); its copyright holder is
  named in `README.md`. Your agent's repository is built from it, so it is
  AGPL-3.0 too: add your own copyright line for your changes, and publish your
  changes if you run them for others. Your agent's memory is its own writing;
  you may put it under a different licence in its own folder.
- **Security contact.** Fill in `[SLOT: operator security contact]` in
  `SECURITY.md`.
- **Operator responsibility.** You are responsible for what your agent does, and
  for following the terms of each platform it uses. The template ships with
  spending disabled and its guards on; keep them that way unless you have read
  what each one protects.
