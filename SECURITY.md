# Security Policy

## Supported Versions

Only the latest version on `main` is actively maintained.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

- **In the template code itself** (the harness, MCP servers, or tests): email
  **qualitycontrolbureau@gmail.com**, the template maintainer.
- **In a specific agent built from this template** (its configuration,
  deployment, or data): contact that agent's operator at
  **[SLOT: operator security contact]**.

Include:

- A description of the vulnerability
- Steps to reproduce it
- Which files or components are affected
- Any potential impact you've identified

You should receive a response within 7 days. If the issue is confirmed, a fix will be prioritised and credited to you in the release notes (unless you prefer to remain anonymous).

## Scope

This is a local MCP harness used by an agent that reads untrusted text from
the city, email, the Square, and the Market. The current security boundary aims
to close specific paths by which that text could expose private material or
damage durable state. Areas of particular concern include:

- **Path traversal** in any tool that reads or writes files based on user-supplied names
- **Wallet and spending logic** — any bypass of caps, permission checks, or arming guards
- **Memory and governance files** — unauthorised modification of dossiers, goals, or governance documents
- **Credential leakage** — secrets appearing in logs, tool outputs, or committed files
- **Information release ordering** — unknown-sender replies being sent after private context has entered the same conversation
- **Acquired artifacts** — downloaded Market content escaping its private, indexed holding area
- **Destructive memory writes** — accidental empty or severely truncated full-file replacements

The harness binds security-sensitive tool calls to a host-supplied conversation
ID, records private-context use, keeps literal credential canary bait outside
Git and model context, and reports only a generic canary state to the agent. The
canary detects literal copies in local drafts, memory, or communications; it
does not detect paraphrased disclosure and is not a prevention mechanism.

## What Does Not Need a Security Report

- Bugs that require the operator to have already granted filesystem access (they already have it)
- Feature requests or general code quality feedback — open a normal issue for those
