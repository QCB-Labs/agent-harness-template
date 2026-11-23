# Shift reports

One entry per wake, written by `harness.file_shift_report` as
`YYYY-MM/SR-NNNN-YYYY-MM-DD.md`. The harness assigns the number, corrects the
title's number, and advances `budget.json` only after saving the report.
Never edit or delete a landed entry; corrections are new entries naming it.

Use the four headings required by [AGENTS.md](../../AGENTS.md), in order:

1. `## Waiting, Due and Deferred`
2. `## What Happened, and Why`
3. `## Observations`
4. `## What I Plan Next`

The newest report's plan feeds the next wake card. Reports are cut into keyed
cards for bounded recall. `read_recent_shift_reports(n=5)` is the full-report
fallback. If configured, the harness emails the after-action report; a failed
send is reported without undoing the saved file. An optional measured model
footer comes from `config/driver-record.json`, not the model's self-report.

Crash recovery starts from `_UNRECORDED-*.md`: `recover_unrecorded` reads surviving
dumps and `file_unrecorded` archives a named dump without overwriting history.
After recording and updating changed memory, use `commit_wake` and check its
result as specified in the protocol.

> Empty at genesis. An empty log is the correct starting state and is evidence
> of nothing. Do not add entries to make it look complete.
