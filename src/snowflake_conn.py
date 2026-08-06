"""Shared Snowflake key-pair-auth connection helper.

Used by `src/snapshot.py` (startup) and `src/tools.py:run_census_sql`
(request time — CLAUDE.md rule 13's sole exception). Mirrors
`scripts/sf_query.py`, which predates this module and stays a free-standing
recon script rather than importing from application code.
"""

from __future__ import annotations

import os
from typing import Any

import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from src.contracts import SNOWFLAKE_CONNECT_TIMEOUT_S

REQUIRED_ENV_VARS = [
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PRIVATE_KEY_PATH",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_ROLE",
]


def _load_private_key(path: str, passphrase: str | None) -> bytes:
    with open(path, "rb") as f:
        pem_bytes = f.read()
    key = serialization.load_pem_private_key(
        pem_bytes,
        password=passphrase.encode() if passphrase else None,
        backend=default_backend(),
    )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def connect(
    session_parameters: dict[str, Any] | None = None,
) -> snowflake.connector.SnowflakeConnection:
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    private_key_der = _load_private_key(
        os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"],
        os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
    )
    kwargs: dict[str, Any] = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key=private_key_der,
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        role=os.environ["SNOWFLAKE_ROLE"],
        database=os.environ.get("SNOWFLAKE_DATABASE"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA"),
        # Bounds only the login/auth handshake (issue #15 review finding:
        # an unbounded connect() can hang app startup indefinitely). Safe to
        # apply unconditionally — unlike a network_timeout, this never
        # interacts with SQL_STATEMENT_TIMEOUT_S on a long-but-valid query,
        # since it only covers the phase before a session exists.
        login_timeout=SNOWFLAKE_CONNECT_TIMEOUT_S,
    )
    if session_parameters:
        kwargs["session_parameters"] = session_parameters
    return snowflake.connector.connect(**kwargs)
