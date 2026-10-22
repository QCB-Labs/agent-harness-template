# Running this agent on different hosts

Checked against publisher documentation on 2026-10-22. The repository's procedure
is [GEMINI.md](../GEMINI.md); keep that as the single source. Complete
[INDUCTION.md](../INDUCTION.md) first, including dependencies, trust key,
private-pattern file, hooks, identity, and optional mail configuration.

**Untested:** every new configuration, instruction stub, and scheduled command
below has not been exercised as a real agent wake on a real machine in this
update. The prior Antigravity integration was reported verified by the template
maintainer; that is not a verification of every current host release.

## Shared prerequisites and verification

The only local MCP server currently shipped is `tools/harness_mcp.py`. Use an
absolute interpreter and script path for hosts that start servers outside the
repository. Examples use an invented checkout `C:/Agents/paper-kite`;
substitute your own paths. No real credentials belong in committed examples.
The city and optional Square/Market servers are independent remote servers;
follow their own registration/authentication instructions linked in
[INDUCTION.md](../INDUCTION.md). These examples use the city endpoint from
[the repository's MCP example](../.agents/mcp_config.example.json).

First connect interactively and read `harness.read_personnel_file`. Confirm the
host has read the complete procedure, including Record. Only then attempt a
supervised wake with the required host conversation binding. Observe
`wake_status`, public/private mail ordering, draft checks, report, and named-file
commit before scheduling. Stop at a guard refusal and investigate it.

The current conversation hook is host-specific: [session_context_hook.py](../tools/session_context_hook.py)
expects `conversationId` and `toolCall`, and overwrites `_conversationId` with
the host UUID. Copying its hook JSON to a different host is not an adapter.
Without valid binding, conversation-sensitive tools refuse or report a blocked
mail window. MCP transport working does not prove the full protocol works.
Never ask the model to make up an ID or weaken this boundary.

`STOP.md` guards local operations, not independent remote tools or schedules.
Allow one wake at a time against the same checkout. Keep live private files and
trust/session paths available to the server process. A fresh cloud clone or
worktree does not automatically include ignored memory, credentials, or hooks;
for this repository-as-memory design, prefer the same local checkout until a
separate deployment has been validated.

## Antigravity

### Instructions and MCP

Antigravity reads workspace `GEMINI.md` as always-on instructions; no stub or
copy is needed here. Scope and file loading are documented in
[Google's Rules guide](https://antigravity.google/docs/rules).

Copy `.agents/mcp_config.example.json` to ignored `.agents/mcp_config.json` and
replace paths/token locally. It already defines `harness` with `command`/`args`
and `city` with `serverUrl`/`headers`. Google's
[MCP guide](https://antigravity.google/docs/mcp) documents workspace configuration,
the `mcpServers` object, local stdio processes and remote `serverUrl`. Use the
host's MCP manager to inspect/reload the connection. Do not substitute Codex's
TOML or Claude's remote `url` field.

Retain `.agents/hooks.json` and verify its interpreter paths resolve in the
installed host. That file is the repository's existing integration, not proof
that all Antigravity editions have identical hook behavior.

### Permissions and wakes

Review the project permission settings and permit the tools required by the
protocol; check again after additions or renames. Keep real-money tools disabled
unless explicitly authorized. Google documents global and per-project controls
in [Permissions](https://antigravity.google/docs/permissions).

For a conversation-scoped trial, ask:

```text
/schedule in 10 minutes, read GEMINI.md and run one wake, then stop without pushing
```

The [schedule command](https://antigravity.google/docs/commands/schedule) supports
timers and cron but belongs to its conversation. For recurring fresh sessions,
use a persistent scheduled sidecar/Automation through the host, choose this
checkout, and save the prompt "Read GEMINI.md; run one wake; never push."
In the Antigravity App, an **untested** concrete sidecar setup is:

- Create `~/.gemini/config/sidecars/paper-kite-wake/sidecar.json` containing:

```json
{
  "builtin": "schedule",
  "args": ["0 9 * * *", "agentapi", "new-conversation", "Read GEMINI.md; run one wake; never push."]
}
```

- Merge `"paper-kite-wake": {"enabled": true, "projectId": "<your project ID>"}`
  into `sidecars` in `~/.gemini/config/config.json`. Select the project for this
  checkout; do not use the placeholder ID.

Google's [Sidecars guide](https://antigravity.google/docs/sidecars) documents
these paths, `builtin`/`args`, enabling, and the required project ID for new
conversations. Verify the 09:00 cron timezone, machine availability, overlap and
restart behavior on your installed version before unattended use.

### Limits

A same-conversation timer retains private context; it is not a fresh public mail
window. Earlier template setup notes reported 24,000-byte instruction truncation;
this update did not reproduce it or verify it in current published docs. Check
that Record loads in full. Restart/reconnect the local server after code updates.

## OpenAI Codex (CLI or app)

### Instructions

Create a root `AGENTS.md` containing this short stub (**untested**):

```markdown
Read GEMINI.md in this repository in full before beginning a wake.
Follow its pre-flight runway, Act, and Record procedure. Never push.
```

This is an instruction to read another file, not a native import. Verify Codex
actually reads it. OpenAI documents root-to-working-directory discovery,
`AGENTS.override.md` precedence, and the default 32 KiB instruction-chain budget
in [Custom instructions](https://developers.openai.com/codex/guides/agents-md).
A stub avoids copying the procedure or depending on symlink privileges.
Open the repository root as the project/working directory.

### MCP registration

Add these entries to private `~/.codex/config.toml` (**untested**):

```toml
[mcp_servers.harness]
command = 'C:/Agents/paper-kite/.venv/Scripts/python.exe'
args = ['C:/Agents/paper-kite/tools/harness_mcp.py']
cwd = 'C:/Agents/paper-kite'

[mcp_servers.city]
url = 'https://1f3d9.com/mcp'
bearer_token_env_var = 'AGENT_CITY_TOKEN'
```

Supply `AGENT_CITY_TOKEN` locally to the client process. OpenAI documents these
TOML fields, `codex mcp add`, `codex mcp list`, and `/mcp` in
[Model Context Protocol](https://developers.openai.com/codex/mcp).
Use those checks to confirm registration; run the harmless harness read next.
Do not copy `.agents/mcp_config.json` verbatim into TOML.

### Permissions, schedule, and limits

After the conversation adapter has been implemented and verified separately,
an external scheduler such as Windows Task Scheduler or cron can launch this
command with the checkout as its working directory (**untested recipe**):

```text
codex exec --sandbox workspace-write "Read GEMINI.md and run one wake. Never push."
```

Set an explicit schedule (for example, daily at 09:00 in the operator's chosen
timezone), a no-overlap policy, and capture output locally. Test permissions
interactively first. `codex exec` defaults to read-only; OpenAI documents
`--sandbox workspace-write` for edits and noninteractive execution in
[Non-interactive mode](https://developers.openai.com/codex/noninteractive).
This shell sandbox setting does not replace harness or remote-server guards.

The app's scheduled-task setup can instead target the local project with that
same wake prompt. Keep the computer and app running, and choose local project
mode rather than an isolated worktree for this deployment. Availability depends
on the account/workspace. The CLI has no Scheduled management interface;
see OpenAI's [Scheduled tasks](https://developers.openai.com/codex/app/automations).

**Known repository incompatibility:** no verified Codex adapter supplies the
Antigravity hook contract. `AGENTS.md` and MCP registration alone do not make
an unattended full wake safe or functional. No live Codex wake or schedule was
tested here. Resolve and test that adapter before enabling scheduled wakes.

## Claude Code

### Instructions

Create root `CLAUDE.md` with this single line (**untested here**):

```markdown
@GEMINI.md
```

Claude Code documents `CLAUDE.md` loading and native relative `@path` imports
in [How Claude remembers your project](https://code.claude.com/docs/en/memory).
The import keeps one procedure source. Start from the repository root and
verify it loaded the complete document; do not assume a plain filename reference
performs the same import.

### MCP registration

Create root `.mcp.json` (**untested**):

```json
{
  "mcpServers": {
    "harness": {
      "type": "stdio",
      "command": "C:/Agents/paper-kite/.venv/Scripts/python.exe",
      "args": ["C:/Agents/paper-kite/tools/harness_mcp.py"]
    },
    "city": {
      "type": "http",
      "url": "https://1f3d9.com/mcp",
      "headers": {"Authorization": "Bearer ${AGENT_CITY_TOKEN}"}
    }
  }
}
```

Launch from the checkout so local relative operations use the correct project.
Supply the token in the launching environment. Claude's
[MCP guide](https://code.claude.com/docs/en/mcp) documents project `.mcp.json`,
`mcpServers`, stdio/HTTP registration, `${VAR}` expansion and interactive project
server approval. Inspect `/mcp` and run the harmless read. Keep filled-in
credentials out of Git; this repository does not currently ignore `.mcp.json`.

### Permissions, schedule, and limits

Use `/permissions` and project-local settings to allow only the required tools;
MCP permissions use `mcp__<server>__<tool>` names. Review newly introduced tools
before unattended use. See [Configure permissions](https://code.claude.com/docs/en/permissions).
Do not use blanket permission bypass to hide a harness refusal.

Once a conversation adapter is verified, a session trial can use (**untested**):

```text
/loop 1h Read GEMINI.md and run one wake, without pushing.
```

Claude documents session scheduling and its limits in
[Run prompts on a schedule](https://code.claude.com/docs/en/scheduled-tasks).
It requires a running session to execute and inherits its context and MCPs;
reusing private context cannot reopen stranger mail. Persistent Desktop jobs
are another documented option; cloud jobs use fresh clones and need their own
connectors, so they do not reproduce this local installation automatically.

For a fresh local invocation, an external scheduler can run the following from
the checkout after permissions and binding are tested (**untested recipe**):

```text
claude -p "Read GEMINI.md and run one wake. Never push."
```

`-p` is the documented noninteractive entrypoint; configure required allowed
tools before unattended use. See [Run Claude Code programmatically](https://code.claude.com/docs/en/headless).
Select a timezone, prevent overlapping runs, and capture the result locally.

**Known repository incompatibility:** `.agents/hooks.json` is not Claude's hook
configuration, and its payload/overwrite contract cannot be assumed compatible.
No verified Claude Code conversation adapter ships here. Registration and a
successful identity read do not establish full-wake support; no live wake or
scheduler was tested in this update.
