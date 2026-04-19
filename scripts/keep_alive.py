#!/usr/bin/env python3.14
"""
scripts/keep_alive.py — Ping Supabase every 5 days to prevent free-tier pausing.

Inserts one row into keep_alive_log. Run via GitHub Actions cron.

Usage:
    python3 scripts/keep_alive.py

Requires env vars: SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_env() -> None:
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    _load_env()

    try:
        from supabase import create_client
    except ImportError:
        print("ERROR: supabase-py not installed")
        sys.exit(1)

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        sys.exit(1)

    client = create_client(url, key)
    client.table("keep_alive_log").insert({}).execute()
    print("keep-alive ping sent")


if __name__ == "__main__":
    main()
