---
name: starting-mail
description: >-
  Use this skill to write a new email first, rather than reply: to an approved
  correspondent such as the operator or a peer, or to ask the operator to
  approve someone new.
---

# Mail you start

- `harness.send_new_thread(correspondent_key="<key>", subject="<subject>", body="<your message>")` — write to an approved correspondent, named by key (for example `operator` or a peer's key). An unknown key is refused, and you cannot add one.
- `harness.request_new_correspondent(proposed_name="<name>", proposed_email="<address>", reason="<why>")` — ask the operator to approve someone new. Changes nothing by itself.

Both work only after private context is loaded.
