---
name: spending
description: >-
  Use this skill before any action that costs a city fee credit: inventing or
  revising a kind, founding a continent, or renaming, retiring or restoring a
  place. It gives the steps in order, the request-id rules, and what to ask
  the operator for when a spend is refused.
---

# Spending a city fee credit

These cost one city fee credit each:

- `city.invent_kind`
- `city.revise_kind`
- `city.found` with `parent_id: null` — founding a continent
- `city.place_edit` renaming, retiring, or restoring a place — the paid field alone, never mixed with free edits

Every one follows the same steps, in this order:

1. **`harness.authorise_spend(action="<action>", detail="<what and why>")`**, with `action` one of `invent_kind`, `revise_kind`, `found_continent`, `rename_place`, `retire_place`, or `restore_place`, and `detail` saying what and why.
   - **REFUSED**: stop. Do not call `city.credit_preflight`. If spending was disarmed, the harness has already written and sent your request. Do not look for another route, and do not ask a second time (Article 7).
   - **GATES_PASSED**: it returns a `city_credit_request_id`.
2. **`city.credit_preflight`** — note `fee_cost`, `balance_before`, and `balance_after`.
3. **The paid call**, with `city_credit_request_id` set to exactly the id from step 1.

Rules:

- **Always send the `city_credit_request_id`.** Leaving it out does not skip payment: it switches the call to the X-PAYMENT rail, which is not fee credits (Article 7).
- Never invent a request id yourself.
- On 503: wait 4 seconds, retry **once**, with the same request id.

## When a spend is refused

A refusal names what is missing. Only the operator can supply it: they arm spending, unlock an admin session, and approve the wallet. You never set any of these yourself.

So that your request is one they can act on, say in your journal (and in the `detail` of `authorise_spend`, which reaches them):

- the exact action, from the list above;
- what it is for, and why now;
- what the refusal said was missing.

Then carry on with free work. A refused spend can wait for the operator; nothing else in the session depends on it.
