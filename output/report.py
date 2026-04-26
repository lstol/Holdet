"""
output/report.py — human-readable briefing and status formatting.

Public API:
    format_briefing(briefing, state) -> str
    format_status(state, riders) -> str
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import config


# ── BriefingOutput schema ─────────────────────────────────────────────────────

@dataclass
class BriefingOutput:
    """All data needed to render a pre-stage briefing."""
    stage: object                   # scoring.engine.Stage
    my_team: list                   # list[holdet_id]
    captain: str                    # holdet_id
    riders: list                    # list[Rider] — full roster for lookups
    probs: dict                     # dict[holdet_id, RiderProb]
    current_team_ev: float
    suggested_profile: Optional[object]   # RiskProfile | None
    suggested_profile_reason: str
    profiles: dict                  # dict[RiskProfile, ProfileRecommendation]


# ── format_briefing ───────────────────────────────────────────────────────────

def format_briefing(briefing: BriefingOutput, state: dict) -> str:
    """
    Render a human-readable pre-stage briefing.

    Sections:
    1. Stage header
    2. DNS/injury alerts
    3. Probability table (team riders + transfer candidates)
    4. 4-profile recommendation table
    5. Suggested profile with plain-English reasoning
    """
    lines: list[str] = []
    stage = briefing.stage
    rider_map = {r.holdet_id: r for r in briefing.riders}

    # 1. Stage header ──────────────────────────────────────────────────────────
    ttt_flag = "  [TTT]" if stage.is_ttt else ""
    lines.append(
        f"Stage {stage.number} — {stage.start_location} → {stage.finish_location}"
        f" ({stage.stage_type}, {stage.distance_km:.0f}km){ttt_flag}"
    )
    lines.append("=" * 70)

    # 2. DNS/injury alerts ─────────────────────────────────────────────────────
    alerts = []
    for rid in briefing.my_team:
        r = rider_map.get(rid)
        if r and r.status != "active":
            stages_left = config.TOTAL_STAGES - stage.number + 1
            penalty = stages_left * -100_000
            alerts.append(
                f"  ALERT: {r.name} ({r.team_abbr}) status={r.status.upper()}"
                f" — projected penalty {penalty:,} ({stages_left} stages remaining)"
            )
    if alerts:
        lines.append("DNS / INJURY ALERTS:")
        lines.extend(alerts)
        lines.append("")

    # 3. Probability table ─────────────────────────────────────────────────────
    # Collect rider ids to show: all 8 team riders + transfer candidates (buys)
    transfer_ids: set = set()
    for rec in briefing.profiles.values():
        for t in rec.transfers:
            if t.action == "buy":
                transfer_ids.add(t.rider_id)

    prob_rider_ids = list(briefing.my_team) + [
        rid for rid in transfer_ids if rid not in briefing.my_team
    ]

    # Header
    col = [28, 6, 7, 6, 7, 7, 6, 10]
    hdr = (
        f"{'Name':<{col[0]}}"
        f"{'Team':>{col[1]}}"
        f"{'Value':>{col[2]}}"
        f"{'pWin':>{col[3]}}"
        f"{'pTop3':>{col[4]}}"
        f"{'pTop15':>{col[5]}}"
        f"{'pDNF':>{col[6]}}"
        f"{'Source':>{col[7]}}"
    )
    sep = "─" * sum(col)
    lines.append("PROBABILITY TABLE:")
    lines.append(sep)
    lines.append(hdr)
    lines.append(sep)

    for rid in prob_rider_ids:
        r = rider_map.get(rid)
        rp = briefing.probs.get(rid)
        if r is None or rp is None:
            continue

        cap_mark = "[C] " if rid == briefing.captain else "    "
        name_str = cap_mark + r.name
        source_str = f"{'*' if rp.source == 'adjusted' else ' '} {rp.source}"
        team_flag = "*" if rid in transfer_ids else " "

        lines.append(
            f"{name_str:<{col[0]}}"
            f"{r.team_abbr:>{col[1]}}"
            f"{r.value/1_000_000:>{col[2]-1}.2f}M"
            f"{rp.p_win:>{col[3]}.1%}"
            f"{rp.p_top3:>{col[4]}.1%}"
            f"{rp.p_top15:>{col[5]}.1%}"
            f"{rp.p_dnf:>{col[6]}.1%}"
            f"  {source_str}"
        )

    if transfer_ids:
        lines.append("")
        lines.append("Transfer candidates marked with * above")

    # 3b. Rider confidence adjustments ────────────────────────────────────────
    adjusted_riders = [
        (rid, rp) for rid, rp in briefing.probs.items()
        if any(k.startswith("rca_") for k in rp.manual_overrides)
    ]
    if adjusted_riders:
        lines.append("")
        lines.append("RIDER CONFIDENCE ADJUSTMENTS:")
        for rid, rp in adjusted_riders:
            r = rider_map.get(rid)
            name = r.name if r else rid
            base_win = rp.manual_overrides.get("rca_p_win", rp.p_win)
            adj_win = rp.p_win
            delta = adj_win - base_win
            sign = "+" if delta >= 0 else "−"
            # Recover original multiplier: adj = base*(1+mult) → mult = adj/base - 1
            mult_pct = round((adj_win / base_win - 1) * 100) if base_win > 0 else 0
            mult_sign = "+" if mult_pct >= 0 else ""
            lines.append(
                f"  Rider: {name}\n"
                f"    Base P(win): {base_win:.2%}  →  Adjusted: {adj_win:.2%}"
                f"  ({mult_sign}{mult_pct:.0f}% manual)\n"
                f"    Source: {rp.source}"
            )

    lines.append("")

    # 4. Profile recommendation table ─────────────────────────────────────────
    from scoring.optimizer import RiskProfile

    profiles_order = [
        RiskProfile.ANCHOR,
        RiskProfile.BALANCED,
        RiskProfile.AGGRESSIVE,
        RiskProfile.ALL_IN,
    ]
    profile_names = ["ANCHOR", "BALANCED", "AGGRESSIVE", "ALL-IN"]
    col_w = 12
    label_w = 22

    def fmt_k(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v / 1000:.0f}k"

    def captain_name(rec) -> str:
        r = rider_map.get(rec.captain)
        if r is None:
            return rec.captain[:10]
        parts = r.name.split()
        if len(parts) >= 2:
            return f"{parts[-1][:8]} {parts[0][0]}."
        return r.name[:10]

    def transfer_count(rec) -> int:
        return sum(1 for t in rec.transfers if t.action == "buy")

    lines.append("PROFILE RECOMMENDATIONS:")
    table_sep = "─" * (label_w + col_w * 4)
    lines.append(table_sep)
    lines.append(
        f"{'':>{label_w}}"
        + "".join(f"{h:>{col_w}}" for h in profile_names)
    )
    lines.append(table_sep)

    table_rows = [
        ("Captain:",       lambda rec: captain_name(rec)),
        ("Expected value:", lambda rec: fmt_k(rec.expected_value)),
        ("Upside (p90):",  lambda rec: fmt_k(rec.upside_90pct)),
        ("Downside (p10):", lambda rec: fmt_k(rec.downside_10pct)),
        ("Transfers:",     lambda rec: str(transfer_count(rec))),
        ("Transfer cost:", lambda rec: fmt_k(-rec.transfer_cost)),
        ("Net EV:",        lambda rec: fmt_k(rec.expected_value - rec.transfer_cost)),
    ]

    for label, fn in table_rows:
        values = []
        for p in profiles_order:
            rec = briefing.profiles.get(p)
            values.append(fn(rec) if rec else "-")
        lines.append(
            f"{label:<{label_w}}"
            + "".join(f"{v:>{col_w}}" for v in values)
        )

    lines.append(table_sep)
    lines.append("")

    # Transfer details per profile
    for p, name in zip(profiles_order, profile_names):
        rec = briefing.profiles.get(p)
        if rec is None or not rec.transfers:
            continue
        buys = [t for t in rec.transfers if t.action == "buy"]
        sells = [t for t in rec.transfers if t.action == "sell"]
        if buys or sells:
            lines.append(f"  {name}: ", )
            for t in sells:
                lines.append(f"    SELL {t.rider_name} ({t.value/1_000_000:.2f}M)")
            for t in buys:
                lines.append(
                    f"    BUY  {t.rider_name} ({t.value/1_000_000:.2f}M, fee {t.fee:,})"
                )

    lines.append("")

    # 5. Suggested profile ─────────────────────────────────────────────────────
    if briefing.suggested_profile is not None:
        lines.append(
            f"SUGGESTED: {briefing.suggested_profile.name}"
            f" — {briefing.suggested_profile_reason}"
        )
    else:
        lines.append(f"SUGGESTED: BALANCED (default — no rank data)")

    return "\n".join(lines)


# ── format_status ─────────────────────────────────────────────────────────────

def format_status(state: dict, riders: list) -> str:
    """
    Render current team, bank, rank, and DNS alerts.

    riders: full list[Rider] from riders.json (may be empty list)
    """
    lines: list[str] = []

    my_team = state.get("my_team", [])
    captain = state.get("captain")
    bank = state.get("bank", config.INITIAL_BUDGET)
    rank = state.get("rank")
    total = state.get("total_participants")
    stage = state.get("current_stage", 0)
    stages_done = len(state.get("stages_completed", []))

    lines.append("=== Holdet Status ===")
    lines.append(
        f"Stage: {stage} / {config.TOTAL_STAGES}  ({stages_done} settled)"
    )
    lines.append(f"Bank:  {bank / 1_000_000:.3f}M")

    if rank:
        rank_str = f"Rank: {rank:,}"
        if total:
            rank_str += f" / {total:,}"
        lines.append(rank_str)

    if not my_team:
        lines.append("")
        lines.append("No team loaded. Run: python3 main.py ingest --stage N")
        return "\n".join(lines)

    rider_map = {r.holdet_id: r for r in riders}

    # Per-rider rows
    lines.append(f"\nYour team ({len(my_team)} riders):")
    dns_alerts: list[str] = []
    total_value = 0

    for rid in my_team:
        r = rider_map.get(rid)
        if r:
            cap_mark = " [C]" if rid == captain else "    "
            value_str = f"{r.value / 1_000_000:.2f}M"
            if r.start_value:
                delta = r.value - r.start_value
                value_str += f"  ({delta:+,})"
            dns_mark = "  *** DNS ***" if r.status != "active" else ""
            lines.append(
                f"  {cap_mark} {r.name:<30} {r.team_abbr:<6} {value_str}{dns_mark}"
            )
            if r.status != "active":
                stages_left = config.TOTAL_STAGES - stage
                penalty = stages_left * -100_000
                dns_alerts.append(
                    f"  ALERT: {r.name} status={r.status.upper()} — "
                    f"remaining penalty {penalty:,} ({stages_left} stages)"
                )
            total_value += r.value
        else:
            lines.append(f"       holdet_id={rid} (not found in riders.json)")

    lines.append(f"\n  Total team value: {total_value / 1_000_000:.2f}M")

    if dns_alerts:
        lines.append("")
        lines.extend(dns_alerts)

    return "\n".join(lines)
