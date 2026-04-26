"""
tests/test_rider_profiles.py — Session 19.6: Rider identity profiles

6 tests covering apply_rider_profiles() correctness, ordering invariants,
ROLE_TOP15 isolation, pipeline order, no-op behaviour, and source deduplication.
"""
from __future__ import annotations

import pytest

from scoring.probabilities import (
    RiderProb,
    RiderRole,
    ROLE_TOP15,
    apply_rider_adjustments,
    apply_rider_profiles,
)
from scoring.rider_profiles import RiderProfile


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_rp(
    rider_id: str,
    p_win: float = 0.10,
    p_top3: float = 0.20,
    p_top10: float = 0.40,
    p_top15: float = 0.50,
    source: str = "model",
) -> RiderProb:
    return RiderProb(
        rider_id=rider_id,
        stage_number=1,
        p_win=p_win,
        p_top3=p_top3,
        p_top10=p_top10,
        p_top15=p_top15,
        p_dnf=0.02,
        source=source,
    )


def _sprinter_profile(rider_id: str = "r1") -> RiderProfile:
    return RiderProfile(rider_id=rider_id, sprint_bias=1.10, consistency=1.0)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestRiderProfiles:
    def test_rider_profile_applies_sprint_bias(self):
        # Sprinter with sprint_bias=1.10 → p_win increases proportionally
        probs = {"r1": _make_rp("r1", p_win=0.10)}
        profiles = {"r1": RiderProfile(rider_id="r1", sprint_bias=1.10, consistency=1.0)}
        role_map = {"r1": RiderRole.SPRINTER}

        result = apply_rider_profiles(probs, profiles, role_map)

        # sprint_bias × consistency × p_win = 1.10 × 1.0 × 0.10 = 0.11
        assert abs(result["r1"].p_win - 0.11) < 1e-6

    def test_rider_profile_consistency_reduces_all_fields(self):
        # consistency=0.90 reduces all four fields; ordering p_win ≤ p_top3 ≤ p_top10 ≤ p_top15 holds
        probs = {"r1": _make_rp("r1", p_win=0.10, p_top3=0.20, p_top10=0.40, p_top15=0.50)}
        profiles = {"r1": RiderProfile(rider_id="r1", consistency=0.90)}
        role_map = {"r1": RiderRole.DOMESTIQUE}

        result = apply_rider_profiles(probs, profiles, role_map)
        rp = result["r1"]

        assert rp.p_win   < probs["r1"].p_win   + 1e-9
        assert rp.p_top3  < probs["r1"].p_top3  + 1e-9
        assert rp.p_top10 < probs["r1"].p_top10 + 1e-9
        assert rp.p_top15 < probs["r1"].p_top15 + 1e-9
        assert rp.p_win   <= rp.p_top3
        assert rp.p_top3  <= rp.p_top10
        assert rp.p_top10 <= rp.p_top15

    def test_profile_does_not_modify_role_top15(self):
        # ROLE_TOP15 dict must be unchanged after apply_rider_profiles()
        import copy
        original = copy.deepcopy(ROLE_TOP15)

        probs = {"r1": _make_rp("r1")}
        profiles = {"r1": RiderProfile(rider_id="r1", sprint_bias=1.15, gc_bias=1.10, consistency=1.20)}
        role_map = {"r1": RiderRole.SPRINTER}

        apply_rider_profiles(probs, profiles, role_map)

        assert ROLE_TOP15 == original

    def test_profile_pipeline_order_correct(self):
        # apply_rider_adjustments then apply_rider_profiles;
        # rca_p_win key in manual_overrides confirms adjustments ran first,
        # source contains both "user" and "profile".
        probs = {"r1": _make_rp("r1", source="model")}
        adjustments = {"r1": 0.10}
        profiles = {"r1": RiderProfile(rider_id="r1", consistency=0.95)}
        role_map = {"r1": RiderRole.GC_CONTENDER}

        probs = apply_rider_adjustments(probs, adjustments)
        # rca_p_win must exist — confirms adjustments ran before profiles
        assert "rca_p_win" in probs["r1"].manual_overrides

        probs = apply_rider_profiles(probs, profiles, role_map)
        sources = set(probs["r1"].source.split("+"))
        assert "user" in sources
        assert "profile" in sources

    def test_missing_profile_is_no_op(self):
        # Rider not in profiles dict → probs identical to input, no crash
        rp_orig = _make_rp("r1", p_win=0.15, p_top3=0.25, p_top10=0.45, p_top15=0.55)
        probs = {"r1": rp_orig}
        profiles: dict = {}  # no entry for r1
        role_map = {"r1": RiderRole.SPRINTER}

        result = apply_rider_profiles(probs, profiles, role_map)

        assert result["r1"].p_win   == rp_orig.p_win
        assert result["r1"].p_top3  == rp_orig.p_top3
        assert result["r1"].p_top10 == rp_orig.p_top10
        assert result["r1"].p_top15 == rp_orig.p_top15
        assert result["r1"].source  == rp_orig.source

    def test_profile_source_tagging_no_duplicates(self):
        # Calling apply_rider_profiles() twice yields "model+profile" not "model+profile+profile"
        probs = {"r1": _make_rp("r1", source="model")}
        profiles = {"r1": RiderProfile(rider_id="r1", consistency=0.95)}
        role_map = {"r1": RiderRole.GC_CONTENDER}

        result1 = apply_rider_profiles(probs, profiles, role_map)
        result2 = apply_rider_profiles(result1, profiles, role_map)

        assert result2["r1"].source == "model+profile"
        assert result2["r1"].source.count("profile") == 1
