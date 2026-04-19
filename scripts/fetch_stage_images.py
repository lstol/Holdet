#!/usr/bin/env python3.14
"""
scripts/fetch_stage_images.py — Download Giro d'Italia stage profile images.

Downloads stage profile images from the official Giro website and optionally
uploads them to Supabase Storage.

Usage:
    python3 scripts/fetch_stage_images.py                  # download only
    python3 scripts/fetch_stage_images.py --upload         # download + upload to Supabase
    python3 scripts/fetch_stage_images.py --stage 7        # single stage only
    python3 scripts/fetch_stage_images.py --dry-run        # list URLs, no download

URL pattern (verify against https://www.giroditalia.it/tappe/ if images 404):
    https://static2.giroditalia.it/wp-content/uploads/2026/05/tappa{N:02d}_2026-profile.jpg

Saves to:
    data/stage_images/giro_2026/stage-{N:02d}.jpg

Supabase bucket:
    stage-images/giro_2026/stage-{N:02d}.jpg
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed. Run: pip install requests")
    sys.exit(1)


# ── Configuration ─────────────────────────────────────────────────────────────

STAGES_JSON = Path(__file__).parent.parent / "data" / "stages.json"
OUTPUT_DIR  = Path(__file__).parent.parent / "data" / "stage_images" / "giro_2026"

# URL pattern confirmed for Giro 2026 (verify at https://www.giroditalia.it/tappe/ if 404s occur)
# Alternative patterns to try if primary fails:
#   https://static2.giroditalia.it/wp-content/uploads/2026/tappa{n:02d}.jpg
#   https://www.giroditalia.it/wp-content/uploads/2026/05/stage-{n:02d}-profile.jpg
IMAGE_URL_TEMPLATE = (
    "https://static2.giroditalia.it/wp-content/uploads/2026/05/"
    "tappa{n:02d}_2026-profile.jpg"
)

SUPABASE_BUCKET = "stage-images"
SUPABASE_PATH_PREFIX = "giro_2026"

REQUEST_DELAY_S = 0.5  # polite crawl delay between requests


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_stages() -> list[dict]:
    with open(STAGES_JSON) as f:
        data = json.load(f)
    return data.get("stages", data) if isinstance(data, dict) else data


def _local_path(stage_number: int) -> Path:
    return OUTPUT_DIR / f"stage-{stage_number:02d}.jpg"


def _supabase_path(stage_number: int) -> str:
    return f"{SUPABASE_PATH_PREFIX}/stage-{stage_number:02d}.jpg"


def _download_image(stage_number: int, dry_run: bool = False) -> bool:
    """
    Download one stage image. Returns True on success, False on failure.
    Skips if file already exists (unless --force is specified).
    """
    url = IMAGE_URL_TEMPLATE.format(n=stage_number)
    dest = _local_path(stage_number)

    if dest.exists():
        print(f"  Stage {stage_number:02d}: already exists — skipping ({dest.name})")
        return True

    if dry_run:
        print(f"  Stage {stage_number:02d}: would download {url}")
        return True

    print(f"  Stage {stage_number:02d}: downloading {url} ...", end=" ", flush=True)
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "HoldetBot/1.0"})
        if resp.status_code == 404:
            print(f"404 — URL pattern may need updating (see module docstring)")
            return False
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        size_kb = len(resp.content) // 1024
        print(f"OK ({size_kb}KB)")
        return True
    except requests.RequestException as e:
        print(f"FAILED: {e}")
        return False


def _upload_to_supabase(stage_number: int, dry_run: bool = False) -> bool:
    """
    Upload one stage image to Supabase Storage.
    Requires SUPABASE_URL and SUPABASE_SERVICE_KEY in environment (or .env).
    """
    try:
        from supabase import create_client
    except ImportError:
        print("  ERROR: supabase-py not installed. Run: pip install supabase")
        return False

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("  ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in environment")
        return False

    local = _local_path(stage_number)
    if not local.exists():
        print(f"  Stage {stage_number:02d}: local file missing — download first")
        return False

    path = _supabase_path(stage_number)
    if dry_run:
        print(f"  Stage {stage_number:02d}: would upload to {SUPABASE_BUCKET}/{path}")
        return True

    print(f"  Stage {stage_number:02d}: uploading to {SUPABASE_BUCKET}/{path} ...", end=" ", flush=True)
    try:
        client = create_client(url, key)
        with open(local, "rb") as f:
            client.storage.from_(SUPABASE_BUCKET).upload(
                path=path,
                file=f,
                file_options={"content-type": "image/jpeg", "upsert": "true"},
            )
        print("OK")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Download Giro stage profile images")
    parser.add_argument("--upload",   action="store_true", help="Upload to Supabase Storage after download")
    parser.add_argument("--stage",    type=int,            help="Download single stage number only")
    parser.add_argument("--dry-run",  action="store_true", help="List URLs without downloading")
    parser.add_argument("--force",    action="store_true", help="Re-download even if file exists")
    args = parser.parse_args()

    # Load .env if present
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    stages = _load_stages()
    if args.stage:
        stages = [s for s in stages if s["number"] == args.stage]
        if not stages:
            print(f"ERROR: stage {args.stage} not found in {STAGES_JSON}")
            sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nFetching {len(stages)} stage image(s) → {OUTPUT_DIR}\n")

    downloaded_ok = 0
    downloaded_fail = 0

    for stage in stages:
        n = stage["number"]

        # Force re-download if requested
        if args.force:
            local = _local_path(n)
            if local.exists():
                local.unlink()

        ok = _download_image(n, dry_run=args.dry_run)
        if ok:
            downloaded_ok += 1
        else:
            downloaded_fail += 1

        if args.upload and ok and not args.dry_run:
            _upload_to_supabase(n, dry_run=args.dry_run)

        if not args.dry_run and len(stages) > 1:
            time.sleep(REQUEST_DELAY_S)

    print(f"\nDone: {downloaded_ok} OK, {downloaded_fail} failed")
    if downloaded_fail > 0:
        print(
            "\nNOTE: If images return 404, the URL pattern may need updating.\n"
            "Verify the correct path at https://www.giroditalia.it/tappe/\n"
            "and update IMAGE_URL_TEMPLATE in this script."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
