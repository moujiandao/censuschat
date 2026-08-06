#!/usr/bin/env python
"""Verify every credential in .env actually works.

Usage:
    python scripts/check_env.py

Prints PASS/FAIL per credential. Secret VALUES are never printed — only
whether each one authenticates. Written for the M6 "clean-env verification"
exit criterion as much as for local use: run it on the deployed box to prove
the container has working credentials before debugging anything else.
"""

from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

# Present-but-unverifiable: nothing to authenticate against. Caddy hashes the
# password with bcrypt, so the plaintext here is only the README's copy.
PRESENCE_ONLY = ["BASIC_AUTH_USER", "BASIC_AUTH_PASS"]

SNOWFLAKE_VARS = [
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PRIVATE_KEY_PATH",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_ROLE",
    "SNOWFLAKE_DATABASE",
]


def ok(label: str, detail: str = "") -> bool:
    print(f"  PASS  {label}" + (f" — {detail}" if detail else ""))
    return True


def fail(label: str, detail: str) -> bool:
    print(f"  FAIL  {label} — {detail}")
    return False


def check_presence() -> bool:
    """Vars that exist but have nothing to authenticate against."""
    print("\nPresence only (nothing to call):")
    all_ok = True
    for var in PRESENCE_ONLY + SNOWFLAKE_VARS:
        if os.environ.get(var):
            ok(var, "set")
        else:
            all_ok = fail(var, "missing") and all_ok
    return all_ok


def check_anthropic() -> bool:
    """models.list() authenticates the key and consumes zero tokens."""
    print("\nAnthropic:")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return fail("ANTHROPIC_API_KEY", "missing")
    try:
        import anthropic

        models = anthropic.Anthropic().models.list(limit=1)
        n = len(list(models.data))
        return ok("ANTHROPIC_API_KEY", f"authenticated, {n} model(s) visible")
    except Exception as exc:  # noqa: BLE001 — report, never leak the key
        return fail("ANTHROPIC_API_KEY", f"{type(exc).__name__}: {exc}")


def check_langfuse() -> bool:
    """Basic auth against the projects endpoint: public key = user, secret = pass."""
    print("\nLangfuse:")
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sec = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

    missing = [n for n, v in (("LANGFUSE_PUBLIC_KEY", pub), ("LANGFUSE_SECRET_KEY", sec)) if not v]
    if missing:
        return fail("Langfuse", f"missing {', '.join(missing)}")

    try:
        r = httpx.get(
            f"{host.rstrip('/')}/api/public/projects",
            auth=(pub, sec),
            timeout=10.0,
        )
    except Exception as exc:  # noqa: BLE001
        return fail("Langfuse", f"{type(exc).__name__} reaching {host}")

    if r.status_code == 200:
        return ok("Langfuse", f"authenticated against {host}")
    if r.status_code in (401, 403):
        return fail("Langfuse", f"credentials rejected ({r.status_code})")
    return fail("Langfuse", f"unexpected HTTP {r.status_code} from {host}")


def main() -> None:
    load_dotenv()
    print("Verifying credentials from .env (values are never printed)")

    results = [check_presence(), check_anthropic(), check_langfuse()]

    print()
    if all(results):
        print("All credential checks passed.")
    else:
        print("Some checks FAILED — see above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
