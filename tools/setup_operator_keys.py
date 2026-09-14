#!/usr/bin/env python3
"""Generate the operator's passphrase-protected Ed25519 keypair.

Run once, by the operator, on the machine that will issue commands. The private
key never leaves that machine and never goes to the cloud; only the public half
is installed, and only somewhere the agent cannot write.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from tools.paths import REPO_ROOT, operator_pubkey_path, trust_anchor_dir

PRIVATE_KEY_PATH = REPO_ROOT / "operator_private.pem"


def generate_keys() -> int:
    print("=== Operator Ed25519 Key Setup ===")
    print()
    print("The terminal shows nothing while you type a passphrase — no letters,")
    print("no dots, no asterisks. That is normal. Type it and press Enter.")
    print()

    if PRIVATE_KEY_PATH.exists():
        print(f"A key already exists at {PRIVATE_KEY_PATH}")
        if input("Overwrite it? Anything signed by the old key stops working. [y/N] ").strip().lower() != "y":
            print("Cancelled.")
            return 1

    passphrase = getpass.getpass("Enter a strong passphrase to encrypt your private key: ")
    if not passphrase:
        print("Error: passphrase cannot be empty.")
        return 1
    if passphrase != getpass.getpass("Confirm passphrase: "):
        print("Error: passphrases do not match.")
        return 1

    private_key = ed25519.Ed25519PrivateKey.generate()
    PRIVATE_KEY_PATH.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(
                passphrase.encode("utf-8")
            ),
        )
    )

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    gitignore = REPO_ROOT / ".gitignore"
    if gitignore.exists() and "operator_private.pem" not in gitignore.read_text(encoding="utf-8"):
        with open(gitignore, "a", encoding="utf-8") as handle:
            handle.write("\n# Operator private key — never commit\noperator_private.pem\n")
        print("Added operator_private.pem to .gitignore.")

    anchor = trust_anchor_dir()
    pubkey_path = operator_pubkey_path()

    print()
    print(f"Private key (encrypted): {PRIVATE_KEY_PATH}")
    print("  Stays on this machine. Never goes to the cloud. Never commit it.")
    print()
    print(f"=== PUBLIC KEY — install at {pubkey_path} ===")
    print()
    print(public_pem)
    print("Install it now. The harness will not start without it.")
    print()
    if sys.platform.startswith("win"):
        print("In an ADMINISTRATOR PowerShell (admin so the agent's own user cannot")
        print("overwrite the key — that is the whole point of where it lives).")
        print()
        print("Do not use Notepad: it can append .txt or add a BOM, either of which")
        print("stops the key parsing.")
        print()
        print("Copy everything between the dashed lines EXACTLY as it appears —")
        print("no added indentation. PowerShell requires the closing '@ to sit at")
        print("the very start of its line, and an indented one is a parse error.")
        print()
        print("-" * 70)
        print(f'New-Item -ItemType Directory -Force "{anchor}" | Out-Null')
        print(f'Set-Content -Encoding ascii "{pubkey_path}" @\'')
        for line in public_pem.strip().splitlines():
            print(line)
        print("'@")
        print(f'icacls "{anchor}" /inheritance:r /grant:r "Administrators:(OI)(CI)F" /grant:r "Users:(OI)(CI)R"')
        print("-" * 70)
    else:
        print(f"  sudo mkdir -p {anchor}")
        print(f"  sudo tee {pubkey_path}   # paste the block above, then Ctrl-D")
        print(f"  sudo chmod 0644 {pubkey_path}")
    print()
    print("Do NOT place the public key inside the repository. A key the agent can")
    print("write is a key the agent can replace with one of its own.")
    return 0


if __name__ == "__main__":
    raise SystemExit(generate_keys())
