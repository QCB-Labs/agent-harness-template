"""
Circle agent wallet helper for the agent's Market (1F3EA) payments.

Used by market.pay_listing_fee to pay the Market's listing fee. City actions use
fee credits and never touch this wallet. Buying Market listings is not supported
yet (it needs a signed purchase intent).

This file holds NO private key, login code, or session token. Circle keeps the
key shares; the operator logs in by email code in their own terminal. Only the
public address, caps, and scope live in wallet-config.json.

Checked against Circle CLI 1.0.0 (2026-09-15). See WALLET-SETUP.example.md.
"""

import json
import pathlib
import shutil
import subprocess

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
    The wallet approval check. True ONLY when every condition holds.
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


def circle_command():
    """Full path to the Circle CLI.

    npm installs it on Windows as circle.CMD, which subprocess cannot run by its
    bare name. shutil.which resolves the real file on every platform.
    """
    return shutil.which("circle") or "circle"


def transfer_token(recipient, amount, token, payer, idempotency_key, timeout=180):
    """Send `amount` of the ERC-20 `token` from the agent wallet, once.

    Always passes --token: without it the Circle CLI sends the native coin
    (ETH), not USDC. The idempotency key makes a retry return the same
    transfer instead of paying again. Returns the CompletedProcess; raises if
    the command could not run at all. Callers do all approval checks first.
    """
    cmd = [
        circle_command(), "wallet", "transfer", recipient,
        "--amount", str(amount),
        "--token", token,
        "--address", payer,
        "--chain", "BASE",
        "--idempotency-key", idempotency_key,
        "--output", "json",
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def wallet_session_ok():
    """
    True only if the Circle CLI reports a VALID mainnet login right now.
    Uses `circle wallet status --output json` (Circle CLI 1.0.0). Any failure,
    expired token, or unreadable output -> False (fail closed).
    """
    try:
        r = subprocess.run(
            [circle_command(), "wallet", "status", "--output", "json"],
            capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    if r.returncode != 0:
        return False
    try:
        data = json.loads(r.stdout).get("data", {})
        return data.get("mainnet", {}).get("tokenStatus") == "VALID"
    except Exception:
        return False


def pay_armed(cfg=None):
    """
    The operator's arming switch. The wallet can be fully approved and still not
    pay: pay_armed must be explicitly true in wallet-config.json, and the
    operator sets it only for a supervised wake. Missing or non-boolean -> False.
    """
    cfg = cfg if cfg is not None else load_wallet_config()
    return cfg.get("pay_armed") is True
