"""
scripts/init_rider_profiles.py — Seed data/rider_profiles.json with rule-based defaults.

No data fitting, no calibration. Seeds from rider type using stage 1 (flat) as
canonical seed stage. Run once before the race or when adding new riders.

Usage:
  python scripts/init_rider_profiles.py [--riders PATH] [--stages PATH] [--output PATH]

Rider profiles are structural bias signals, not learned parameters.
They do not update from outcomes, calibration, or odds.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingestion.api import load_riders
from scoring.engine import Stage
from scoring.probabilities import _rider_type, RiderRole

# Rule-based defaults keyed by rider type
_TYPE_DEFAULTS: dict[str, dict[str, float]] = {
    RiderRole.SPRINTER:     {"sprint_bias": 1.10, "gc_bias": 0.95, "climb_bias": 0.90, "consistency": 0.95},
    RiderRole.GC_CONTENDER: {"sprint_bias": 0.95, "gc_bias": 1.10, "climb_bias": 1.05, "consistency": 1.05},
    RiderRole.CLIMBER:      {"sprint_bias": 0.90, "gc_bias": 1.05, "climb_bias": 1.10, "consistency": 1.00},
    RiderRole.BREAKAWAY:    {"sprint_bias": 1.00, "gc_bias": 0.95, "climb_bias": 1.00, "consistency": 0.90},
    RiderRole.DOMESTIQUE:   {"sprint_bias": 0.95, "gc_bias": 0.95, "climb_bias": 0.95, "consistency": 0.85},
    RiderRole.TT:           {"sprint_bias": 0.95, "gc_bias": 1.05, "climb_bias": 0.95, "consistency": 1.00},
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed rider profiles from rider type defaults")
    parser.add_argument("--riders", default="data/riders.json", help="Path to riders.json")
    parser.add_argument("--stages", default="data/stages.json", help="Path to stages.json")
    parser.add_argument("--output", default="data/rider_profiles.json", help="Output path")
    parser.add_argument("--stage-number", type=int, default=1,
                        help="Stage number to use for type classification (default: 1)")
    args = parser.parse_args()

    if not os.path.exists(args.riders):
        print(f"Error: riders file not found: {args.riders}", file=sys.stderr)
        sys.exit(1)

    riders = load_riders(args.riders)

    # Load the seed stage
    seed_stage: Stage | None = None
    if os.path.exists(args.stages):
        with open(args.stages, encoding="utf-8") as fh:
            stages_data = json.load(fh)
        if isinstance(stages_data, list):
            stages_list = stages_data
        elif isinstance(stages_data, dict) and "stages" in stages_data:
            stages_list = stages_data["stages"]
        else:
            stages_list = [v for v in stages_data.values() if isinstance(v, dict)]

        for s in stages_list:
            if isinstance(s, dict) and s.get("number") == args.stage_number:
                seed_stage = Stage(
                    number=s["number"],
                    race=s.get("race", "giro_2026"),
                    stage_type=s.get("stage_type", "flat"),
                    distance_km=float(s.get("distance_km", 0)),
                    is_ttt=s.get("is_ttt", False),
                    start_location=s.get("start_location", ""),
                    finish_location=s.get("finish_location", ""),
                )
                break

    if seed_stage is None:
        # Fallback: synthetic flat stage
        seed_stage = Stage(
            number=args.stage_number,
            race="giro_2026",
            stage_type="flat",
            distance_km=200.0,
            is_ttt=False,
            start_location="",
            finish_location="",
        )
        print(f"  Stage {args.stage_number} not found — using synthetic flat stage for classification.")

    profiles: dict[str, dict] = {}
    for rider in riders:
        rtype = _rider_type(rider, seed_stage)
        defaults = _TYPE_DEFAULTS.get(rtype, _TYPE_DEFAULTS[RiderRole.DOMESTIQUE])
        # Key by rider name (lowercased) for human readability; matched by fuzzy lookup
        key = rider.name.lower()
        profiles[key] = dict(defaults)

    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(profiles, fh, indent=2, ensure_ascii=False)

    print(f"Seeded {len(profiles)} rider profiles → {args.output}")


if __name__ == "__main__":
    main()
