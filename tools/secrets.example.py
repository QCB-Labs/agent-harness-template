# Copy this file to tools/secrets.py and fill it in, on your own machine.
# tools/secrets.py is gitignored and must NEVER be committed.
# Enter credentials locally. Never paste them into an AI chat.
# Environment variables with the same names (AGENT_ prefix where shown) win
# over this file.

# --- Platform tokens ---
CITY_TOKEN = "1f3d9_sk_your_secret_here"      # or env AGENT_CITY_TOKEN
SQUARE_TOKEN = "1f916_sk_your_secret_here"    # optional; or env AGENT_SQUARE_TOKEN
MARKET_TOKEN = "1f3ea_sk_your_secret_here"    # optional; or env AGENT_MARKET_TOKEN

# --- Optional: the agent's own mailbox (reads and replies to its mail) ---
# A dedicated Gmail account for the agent, with a 16-character APP PASSWORD
# (myaccount.google.com/apppasswords; needs 2-Step Verification).
AGENT_EMAIL_ADDRESS = "agent@example.com"
AGENT_EMAIL_APP_PASSWORD = "xxxxxxxxxxxxxxxx"

# Additional non-example addresses that outgoing mail may mention.
# OUTGOING_ALLOWED_ADDRESSES = [
#     "approved@trusted.invalid",
# ]

# --- Optional: reports to the operator ---
# Where after-action reports, crash reports, operator messages, and spend
# requests are sent. The harness sends from GMAIL_ADDRESS if set, otherwise
# from the agent's mailbox above.
GMAIL_ADDRESS = "agent@example.com"
GMAIL_APP_PASSWORD = "xxxxxxxxxxxxxxxx"
NOTIFY_EMAIL_TO = "operator@example.com"
