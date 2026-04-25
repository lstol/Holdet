"""
config.py — Load and validate all environment variables from .env.

Fail fast with a clear message if required variables are missing.
Import this at the top of main.py — never use os.getenv() directly elsewhere.
"""
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"Missing required environment variable: {key}\n"
            f"Add it to .env — see .env.example for the full template."
        )
    return val


def _optional(key: str, default: str) -> str:
    return os.getenv(key, default)


# ── Required ───────────────────────────────────────────────────────────────────

def get_email() -> str:
    return _require("HOLDET_EMAIL")

def get_password() -> str:
    return _require("HOLDET_PASSWORD")

def get_game_id() -> str:
    return _require("HOLDET_GAME_ID")

def get_fantasy_team_id() -> str:
    return _require("HOLDET_FANTASY_TEAM_ID")

def get_cartridge() -> str:
    return _require("HOLDET_CARTRIDGE")


# ── Optional with defaults ─────────────────────────────────────────────────────

def get_state_path() -> str:
    return _optional("STATE_PATH", "data/state.json")

def get_riders_path() -> str:
    return _optional("RIDERS_PATH", "data/riders.json")

def get_stages_path() -> str:
    return _optional("STAGES_PATH", "data/stages.json")


# ── Constants ──────────────────────────────────────────────────────────────────

TOTAL_STAGES = 21
INITIAL_BUDGET = 50_000_000

# Transfer discount factor — strategy knob, not a model parameter.
# λ=0.85 means next-stage EV is discounted 15% relative to current stage.
# Overridable via --lambda CLI flag. Do NOT tie to calibration metrics.
LAMBDA_TRANSFER: float = 0.85
