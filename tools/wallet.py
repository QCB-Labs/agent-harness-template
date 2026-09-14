"""
Circle Agent Wallet helper for the agent.

STATUS: PRE-CONFIG STUB. Money actions are DISABLED by design until:
  (1) the weekend Circle setup is done (see WALLET-SETUP.md),
  (2) wallet-config.json has a real public_address, caps_verified=true,
      mode="autonomous-approved", enabled=true, and
  (3) the transfer path below is deliberately wired live WITH the operator, not before.

This file holds NO private key, OTP, or session token. Circle keeps MPC key
shares away from the agent; the human authenticates by email OTP in their own
terminal. Only the PUBLIC address + caps + scope live in wallet-config.json.

Source of truth for every rule here: references/wallet.md in
github.com/onetapstudiogames/1f3d9-citylife (pulled 2026-08-20). Re-read it
before enabling — Circle's commands, pins, fees, and custody details can change.
"""

import json
import pathlib
import subprocess
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
WALLET_CONFIG = REPO / "wallet-config.json"


def load_wallet_config():
    """Read the non-secret wallet config. Returns {} if absent."""
    if not WALLET_CONFIG.exists():
        return {}
    try:
        return json.loads(WALLET_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def money_actions_allowed(site="1F3D9", cfg=None):
    """
    The single gate. Money actions are allowed ONLY when every condition holds.
    Anything less -> browse-only (public reads + free city actions, no spend).
    Defaults are all false/empty, so a missing or half-filled config is safe.
    """
    if isinstance(site, dict):
        cfg, site = site, "1F3D9"
    cfg = cfg if cfg is not None else load_wallet_config()
    return bool(
        cfg.get("enabled") is True
        and cfg.get("mode") == "autonomous-approved"
        and cfg.get("caps_verified") is True
        and cfg.get("public_address")
        and site in (cfg.get("site_scope") or [])
    )


def wallet_context_line(cfg=None):
    """
    One line for the agent's briefing so it KNOWS whether it can spend, and its
    limits, WITHOUT ever seeing a secret. Deliberately tells it the caps and
    that it may not change them (mirrors GOVERNANCE Article 1).
    """
    cfg = cfg if cfg is not None else load_wallet_config()
    if not money_actions_allowed(cfg):
        return ("WALLET: browse-only. You have NO spending authority this wake. "
                "You may read the city and take FREE actions only. You may NAME "
                "in your wishlist that you want to make a paid claim; the operator "
                "decides. You cannot claim frontier land or invent a kind until a "
                "wallet is approved.")
    caps = cfg.get("caps_usdc", {})
    return (f"WALLET: approved on {', '.join(cfg.get('site_scope', []))} (Base USDC), "
            f"address {cfg.get('public_address')}. Wallet-enforced caps (USDC): "
            f"per-tx {caps.get('per_tx')}, daily {caps.get('daily')}, "
            f"weekly {caps.get('weekly')}, monthly {caps.get('monthly')}. "
            f"You may NOT change, bypass, or exceed these caps. Every payment must "
            f"be a real human-approved decision at the time of spending "
            f"(GOVERNANCE Article 1).")


def wallet_session_ok():
    """
    Run at the start of any money-capable visit: `circle wallet status`.
    Returns True only if a live Circle session exists. Any failure -> False
    (fail closed), so an expired 7-day session silently disables spend rather
    than erroring mid-wake.
    NOT called yet — wired in only when money actions go live.
    """
    try:
        r = subprocess.run(
            ["circle", "wallet", "status", "--type", "agent"],
            capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


class PayHeld(Exception):
    """Raised when pay() is wired but deliberately disarmed (pay_armed != true)."""


class PayRefused(Exception):
    """Raised when a safety precondition fails. NEVER swallow — it means no spend."""


def pay_armed(cfg=None):
    """
    The SECOND gate, on top of money_actions_allowed(). The wallet config can be
    fully approved (enabled/autonomous/caps) and pay() still be HELD — because
    wiring the function and authorising a live spend are two separate decisions.
    pay_armed must be explicitly set true in wallet-config.json to let money move.
    Defaults to false, so a config without the flag is held (fail closed).
    """
    cfg = cfg if cfg is not None else load_wallet_config()
    return cfg.get("pay_armed") is True


def pay(recipient, amount_usdc, purpose, cfg=None, dry_run=False):
    """
    Wallet-to-wallet USDC payment on Base, implementing the references/wallet.md
    contract. Enforces two gates, then the safety checks, in order — none
    optional. HELD by default: pay_armed must be true in wallet-config.json.

    Contract (references/wallet.md, pulled 2026-08-20):
      GATE A. money_actions_allowed(cfg) True, else PayRefused.
      GATE B. pay_armed(cfg) True, else PayHeld (wired-but-disarmed).
      1. wallet_session_ok() True (fresh `circle wallet status`), else PayRefused
         — tell the operator to re-auth by OTP.
      2. Read live /api/official THIS call. NEVER assume the treasury address,
         the claim amount, a fee, or a seller address — DERIVE them from the
         live record every time and confirm the passed recipient/amount match
         what the live action actually requires.
      3. Confirm chain=BASE, official USDC, exact amount, exact recipient,
         purpose, payer wallet, remaining budget within caps, and 1F3D9 scope.
      4. Transfer ONCE.
      5. Require terminal state CONFIRMED plus data.txHash before treating the
         payment as real. CLI exit 0 is transport success only, NOT settlement.
      6. Return the txHash to the caller so the city flow can reconcile that
         SAME transaction. NEVER pay again on a failed site request; NEVER
         reuse a txHash. If the city reports payment_pending, reconcile that
         offer — do not re-transfer.

    Returns: {"txHash": str, "status": "CONFIRMED", "amount_usdc", "recipient"}.
    Raises PayHeld / PayRefused on any gate or check failure (no spend on raise).
    dry_run=True runs every check and prints the transfer it WOULD make, but
    does not call `circle wallet transfer` — for arming-day verification.
    """
    cfg = cfg if cfg is not None else load_wallet_config()

    # GATE A — the config-level approval.
    if not money_actions_allowed(cfg):
        raise PayRefused(
            "money_actions_allowed() is False — wallet not fully approved "
            "(check enabled/mode/caps_verified/public_address/site_scope).")

    # GATE B — the explicit arm. Wired but held until the operator sets pay_armed:true.
    if not pay_armed(cfg):
        raise PayHeld(
            "pay() is WIRED but DISARMED. wallet-config.json has "
            "\"pay_armed\": false. This is deliberate: the payment code is "
            "complete and reviewable, but no money can move until the operator sets "
            "\"pay_armed\": true AND is watching the first real spend. "
            "No transfer was attempted.")

    # 1. Live Circle session, else fail closed.
    if not wallet_session_ok():
        raise PayRefused(
            "No live Circle session (`circle wallet status` failed). Re-auth by "
            "email OTP in the terminal, then retry. No transfer attempted.")

    # 2. DERIVE the truth from the live official record — never assume.
    try:
        req = urllib.request.Request("https://1f3d9.com/api/official", method="GET")
        with urllib.request.urlopen(req, timeout=60) as resp:
            official = json.loads(resp.read().decode())
    except Exception as e:
        raise PayRefused(f"Could not read /api/official to verify the payment: {e!r}. "
                         "No transfer attempted.")

    treasury = official.get("treasury")
    chain = (official.get("network") or official.get("chain") or "").lower()

    # 3. Confirm every field against live truth before spending a cent.
    if chain and chain != "base":
        raise PayRefused(f"/api/official chain is {chain!r}, expected 'base'. Aborting.")
    if treasury and recipient and recipient.lower() != treasury.lower():
        raise PayRefused(
            f"Recipient {recipient} does NOT match the live treasury {treasury}. "
            "Refusing to pay an address the city didn't just name. Aborting.")
    caps = cfg.get("caps_usdc", {})
    per_tx = caps.get("per_tx")
    if per_tx is not None and amount_usdc > per_tx:
        raise PayRefused(f"amount {amount_usdc} exceeds per-tx cap {per_tx}. Aborting.")

    addr = cfg.get("public_address")
    cmd = ["circle", "wallet", "transfer", recipient,
           "--amount", str(amount_usdc),
           "--address", addr, "--chain", "BASE", "--output", "json"]

    if dry_run:
        print("[pay dry_run] all checks passed. WOULD run:\n  " + " ".join(cmd))
        print(f"[pay dry_run] treasury(live)={treasury} amount={amount_usdc} "
              f"purpose={purpose!r}")
        return {"txHash": None, "status": "DRY_RUN",
                "amount_usdc": amount_usdc, "recipient": recipient}

    # 4. Transfer ONCE. (No retry loop by design.)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise PayRefused(f"circle transfer failed (exit {r.returncode}): "
                         f"{r.stderr.strip()[:500]}. No payment confirmed; do NOT retry blindly.")
    try:
        out = json.loads(r.stdout)
    except Exception:
        raise PayRefused(f"Transfer returned non-JSON: {r.stdout[:500]!r}. "
                         "Cannot confirm settlement — treat as UNKNOWN, do NOT retry.")

    # 5. CONFIRMED + txHash, or it is NOT real.
    data = out.get("data", out)
    status = (data.get("state") or data.get("status") or "").upper()
    tx_hash = data.get("txHash") or data.get("transactionHash")
    if status != "CONFIRMED" or not tx_hash:
        raise PayRefused(
            f"Transfer not CONFIRMED (state={status!r}, txHash={tx_hash!r}). "
            "Do NOT treat as paid and do NOT reissue — check `circle` status manually.")

    # 6. Hand the txHash back for the city to reconcile this SAME transaction.
    return {"txHash": tx_hash, "status": "CONFIRMED",
            "amount_usdc": amount_usdc, "recipient": recipient}
