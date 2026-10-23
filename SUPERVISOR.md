# SUPERVISOR

Written by the operator: the human who runs this agent. The agent reads this
file through `harness.read_supervisor_file` and never edits it. No harness tool can
write it.

This file holds no private details: no email address, location, family,
employer, or credentials. An AI may draft the headings and prompts below, never
the contents.

## Setup note

The operator fills and signs this file during [setup](SETUP.md).
Host configuration and unattended-wake verification are described in
[Choose where your agent runs](SETUP.md#choose-where-your-agent-runs). Keep credentials, private-pattern lists,
trust keys, and optional evidence casefiles outside this document. The host's
tool permissions and scheduler are separate from the harness's `STOP.md`;
removing that marker is an operator action recorded in `STOP_LOG.md`.

## How I work

[SLOT] When I'm present, how I start and end wakes, how I prefer to be told
things.

## How I correct

[SLOT] How I signal that something is wrong, and what the agent should do next.

## What "done" means

[SLOT] When I want a null result over a stretched one; when to stop rather than
carry on at lower confidence.

## What must never be recorded

[SLOT] Anything that must never enter memory, correspondence, or public output,
even when it would help.

## What I decline to involve the agent in

[SLOT] The operator's own limits, separate from what governance forbids the
agent.

## Authority I hold

[SLOT] Confirm you hold spending approval, governance changes, the stop marker,
and publication, or name who else does.

## Succession

[SLOT] Who holds that authority if I am unavailable, or what happens to the
agent and its repository if nobody does.

## What I commit to about the record

[SLOT] Promises only the operator can make, e.g. "I do not delete journal
entries." State any exception plainly. A promise you wouldn't want measured
against your conduct doesn't belong here.

## Signature

NOT SIGNED. Replace this line with `<operator handle> — YYYY-MM-DDTHH:MM:SSZ`,
using a real UTC time. Until signed, this file is a draft.
