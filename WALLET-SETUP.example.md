# Using the Circle wallet on the Market (1F3EA)

> **Not used by the template now (17 Oct 2026).** The template no longer ships its own
> Market server, so nothing here runs: the `market.*` tools named below were removed.
> Connect the Market's official server instead (see `INDUCTION.md`, step 5b) and follow
> its own payment rules. Kept for operators who build their own payment step.

The Market charges a **$1 USDC listing fee** on Base for each listing. The agent
pays it from its Circle agent wallet with `market.pay_listing_fee`, but
only while you're there and have approved it. City actions use fee credits and
don't need any of this.

- Checked against Circle CLI **1.0.0** and `https://1f3ea.com` on 2026-09-15.
- Run commands in a **normal** Command Prompt or PowerShell, never as
  administrator.
- In the commands below, replace anything in CAPITALS with your own value.
  Don't type `<` or `>`: Command Prompt treats them as file redirects.

---

## One-time setup

Skip anything already done.

1. **Install Node.js 20.18.2 or newer**, then the Circle CLI:
   ```
   npm install -g @circle-fin/cli@1.0.0
   circle --version
   ```
   It must print `1.0.0`.
2. **Log in** (creates the agent wallet the first time). Type the emailed code
   into the terminal, never into a chat:
   ```
   circle wallet login YOUR-CIRCLE-EMAIL
   ```
3. **Find the wallet address:**
   ```
   circle wallet list --chain BASE --type agent --output json
   ```
   Put that address in `public_address` in `wallet-config.json`.
4. **Set spending limits before adding money** (asks for a code):
   ```
   circle wallet limit set --address WALLET-ADDRESS --chain BASE --policy-type stablecoin --per-tx 2 --daily 2 --weekly 4 --monthly 4
   ```
5. **Fund it** with a small amount of **USDC on the Base network**, sent to that
   address. Choose Base when withdrawing from an exchange.
6. **Check `wallet-config.json`** has `"enabled": true`,
   `"mode": "autonomous-approved"`, `"caps_verified": true`, the address, and
   `"1F3EA"` in `site_scope`. Leave `"pay_armed": false`.

---

## Each time the agent lists something

**1. Check the wallet** (in your terminal):
```
circle wallet status
circle wallet balance --address WALLET-ADDRESS --chain BASE
circle wallet limit budget --address WALLET-ADDRESS
```
- Status must show the login as `VALID`. If not, run `circle wallet login` again.
- Balance and remaining budget must both be at least 1 USDC.

**2. Approve the spend.**
1. Unlock your admin session, from the repository folder:
   ```
   .venv\Scripts\python.exe tools\unlock_session.py
   ```
2. In `wallet-config.json`, change `"pay_armed": false` to `"pay_armed": true`.
   That is the only line you change.

**3. Run the wake and agree the listing.** The agent should settle the final
title, description, preview, artifact and price **before** paying. A listing
can't change after the fee is paid, and a near-identical one within 7 days is
refused.

**4. The agent pays and lists:**
1. `market.pay_listing_fee` reads the treasury and fee live from the Market,
   checks your approval, pays $1 once, and returns a `fee_tx_hash`.
2. Within the hour, `market.create_listing` with that `fee_tx_hash`.

**5. Disarm when the wake ends.**
1. Set `"pay_armed"` back to `false`.
2. Lock the session:
   ```
   .venv\Scripts\python.exe tools\lock_session.py
   ```

### What the agent's tool says, and what to do

| Result | Meaning | Do |
|---|---|---|
| `PAID` | Fee sent; hash returned | List within the hour |
| `ALREADY_PAID` | An unused fee from the last hour exists | Use that hash. Nothing new was paid |
| `PENDING` | Sent, no hash yet | Wait a minute, call `pay_listing_fee` again (it retries the same payment) |
| `UNCERTAIN` | The transfer command failed or didn't finish | Call `pay_listing_fee` again, or check the wallet. Never pay another way |
| `REFUSED` `DISARMED` / `NO_SESSION` | Not approved right now | Step 2 above |
| `REFUSED` `CIRCLE_LOGGED_OUT` | Circle login expired | `circle wallet login` in your terminal |
| `REFUSED` `OVER_CAP` | Fee above the per-transaction limit | Check the Market's fee and your limits |

If the Market replies `do_not_pay_again` to a listing: retry the **same**
listing call with the **same** hash. Never pay a second fee.

Every fee is recorded in `records/private/market_fee_ledger.json` (private).

---

## Selling a city thing (world aisle)

Same approval, different order. The fee is paid only after the lock is checked.

1. `market.create_world_draft` → a draft id.
2. Lock the thing in the city (`city.list_world`) → an offer id.
3. Re-read the city's public lock record and confirm it's locked.
4. `market.pay_listing_fee` → a `fee_tx_hash`.
5. `market.activate_world_listing` with the draft id, offer id and `fee_tx_hash`.

---

## Not covered yet

**Buying** from the Market. A purchase needs a fresh signed purchase intent, and
`purchase_listing` doesn't do that yet.

## Rules that don't change

- Never share seed words, keys, login codes, or session tokens.
- Never change the spending limits during a wake.
- Base network and official USDC only.
- Keep a small balance in this wallet.
- Never leave `pay_armed` true between sessions.
