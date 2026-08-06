#!/usr/bin/env python
"""Thin CLI over snowflake-connector-python using key-pair auth from .env.

Usage:
    python scripts/sf_query.py "SELECT 1"

Tooling only — not part of the application. The app touches Snowflake
exclusively through run_census_sql (src/contracts.py); this script is for
recon and manual querying during development.
"""

from __future__ import annotations

import os
import sys

import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from dotenv import load_dotenv

REQUIRED_VARS = [
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PRIVATE_KEY_PATH",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_ROLE",
]


def load_private_key(path: str, passphrase: str | None) -> bytes:
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


def connect() -> snowflake.connector.SnowflakeConnection:
    missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
    if missing:
        print(f"Missing required env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    private_key_der = load_private_key(
        os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"],
        os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
    )

    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key=private_key_der,
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        role=os.environ["SNOWFLAKE_ROLE"],
        database=os.environ.get("SNOWFLAKE_DATABASE"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA"),
    )


def main() -> None:
    if len(sys.argv) != 2:
        print('Usage: python scripts/sf_query.py "SELECT 1"', file=sys.stderr)
        sys.exit(1)

    load_dotenv()
    sql = sys.argv[1]

    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        columns = [col[0] for col in cur.description] if cur.description else []
        rows = cur.fetchall()
        if columns:
            print("\t".join(columns))
        for row in rows:
            print("\t".join(str(v) for v in row))
        print(f"\n({len(rows)} rows)", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
