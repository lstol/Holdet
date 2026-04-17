# RULES.md — Holdet Scoring Rules
# Complete machine-readable reference for the scoring engine
# Source: Official Danish rules (swush.com), fully translated and clarified
# Competition: Giro d'Italia 2026 / Tour de France 2026

---

## 1. Game Setup

| Parameter | Value |
|-----------|-------|
| Starting budget | 50,000,000 kr |
| Team size | Exactly 8 riders |
| Max from same real-world team | 2 |
| Captain | Exactly 1 of your 8 |
| Transfer fee (after Stage 1) | −1% of purchased rider's current value |
| Transfers before Stage 1 | Free, unlimited |
| Contract type | Gold = unlimited transfers |
| Bank interest | +0.5% per round on cash held (compounding) |
| Final score | Sum of all rider values + bank balance |

---

## 2. Per-Rider Scoring Components (applied each stage)

### 2.1 Stage Finish Position (non-TTT only)

| Position | Value (kr) |
|----------|-----------|
| 1st | +200,000 |
| 2nd | +150,000 |
| 3rd | +130,000 |
| 4th | +120,000 |
| 5th | +110,000 |
| 6th | +100,000 |
| 7th | +95,000 |
| 8th | +90,000 |
| 9th | +85,000 |
| 10th | +80,000 |
| 11th | +70,000 |
| 12th | +55,000 |
| 13th | +40,000 |
| 14th | +30,000 |
| 15th | +15,000 |
| 16th+ | 0 |

### 2.2 GC Standing After Stage (every stage, including TTT)

| GC Position | Value (kr) |
|-------------|-----------|
| 1st | +100,000 |
| 2nd | +90,000 |
| 3rd | +80,000 |
| 4th | +70,000 |
| 5th | +60,000 |
| 6th | +50,000 |
| 7th | +40,000 |
| 8th | +30,000 |
| 9th | +20,000 |
| 10th | +10,000 |
| 11th+ | 0 |

**GC tie rule:** When riders finish in the same time group, GC positions are
assigned individually based on crossing order at the finish line.
No shared positions. No averaged payouts.

### 2.3 Jersey Bonuses (per stage)

| Jersey | Value (kr) | Rule |
|--------|-----------|------|
| Yellow (GC leader) | +25,000 | Awarded to rider who WINS or DEFENDS at finish |
| Green (Points leader) | +25,000 | Same |
| Polka Dot (KOM leader) | +25,000 | Same |
| White (Best young rider) | +15,000 | Same |
| Most Aggressive (red number) | +50,000 | Awarded once per stage |

**CRITICAL JERSEY RULE:** The bonus goes to the rider who HOLDS the jersey
at the end of the stage — NOT to whoever wore it entering the stage.
A rider who wears yellow all day but loses it at the finish gets ZERO jersey bonus.

### 2.4 Sprint & KOM Points

- Every sprint point (intermediate or finish line) = **+3,000 kr per point**
- Every KOM point (any category, HC through Cat 4) = **+3,000 kr per point**
- Applies at EVERY designated point on the stage route, not just the finish
- Sprint/KOM points are always ≥ 0 in practice
- ONLY late arrival and DNF/DNS generate negative value
- Stage roadbook must be checked before each stage — number and location
  of bonus points varies significantly per stage

**Sprint point examples by stage type:**
- Flat stage: typically 2–3 intermediate sprints + finish = up to 10+ points possible
- Hilly stage: may have KOM points at climb summits AND intermediate sprint points;
  some stages place sprint points at climb tops (not just KOM) — check roadbook
- Mountain stage: multiple HC/Cat1 KOM points; polka dot contender can earn 20+ points
- All sprint/KOM points on a stage count, regardless of stage type

### 2.5 Late Arrival Penalty (non-TTT only)

- **−3,000 kr per full minute** behind stage winner
- **Truncated** (not rounded): 4 min 54 sec = 4 full minutes = −12,000 kr
- **Cap: −90,000 kr** (30 minutes maximum penalty)
- Applies only to riders who FINISH the stage
- Does NOT apply on TTT stages

### 2.6 DNF / DNS / Disqualification

| Status | Penalty | Detail |
|--------|---------|--------|
| DNF | −50,000 kr (once) | On the stage they abandon. Still earns sprint/KOM points earned before abandonment. Does NOT receive Team Bonus. |
| DNS | −100,000 kr per remaining stage | Rider deactivated. Bleeds every stage they don't start for rest of race. |
| Disqualified | −50,000 kr (once) | Same as DNF — no stage position. |

**DNS cascade formula:**
DNF on Stage N of 21 = −50,000 (Stage N) + −100,000 × (21 − N) remaining stages

Example: DNF Stage 8 → −50,000 + −100,000 × 13 = **−1,350,000 total**

**ACTION RULE: Sell any DNS/deactivated rider immediately.**
Every stage they remain on your team costs −100,000 kr.

---

## 3. Team-Level Bonuses

### 3.1 Team Bonus / Holdbonus (non-TTT only)

Triggered when any rider from a real-world team finishes 1st, 2nd, or 3rd.
ALL your ACTIVE riders from that same real-world team receive:

| Stage result | Bonus per your rider |
|-------------|---------------------|
| 1st place | +60,000 kr |
| 2nd place | +30,000 kr |
| 3rd place | +20,000 kr |

**Rules:**
- Applies to all your active riders from that team — including riders who
  didn't participate in the stage finish (e.g. domestiques)
- DNF riders on that stage do NOT receive the team bonus
- Maximum exposure: 2 riders × 60,000 = +120,000 from a single team win

### 3.2 Captain Bonus / Kaptajnbonus

- Captain earns value changes exactly like any other rider
- **In addition:** whatever positive value growth captain achieves →
  that same amount is deposited directly to your BANK (not rider value)
- **Negative days: NOT amplified.** If captain has a bad stage,
  you suffer only normal rider value loss — no extra bank penalty
- Captain is locked when the stage starts
- Captain must be set during the trading window before the stage
- Changing captain is free, any time the trading window is open

**Example:**
Captain finishes 2nd on a non-TTT stage:
- Stage position: +150,000 → rider value increases by 150,000
- Captain bonus: +150,000 → deposited to bank
- Total gain from captain pick: +300,000 kr

**Optimizer implication:** Captain selection is the single highest-leverage
decision per stage. Always assign captain to rider with highest expected
POSITIVE value growth, not just highest EV.

### 3.3 Stage Depth Bonus / Etapebonus (non-TTT only)

Based on how many of your 8 riders finish in the stage top 15.
Paid **directly to bank** (not rider values). Nonlinear scale:

| Riders in Top 15 | Bank deposit (kr) |
|-----------------|------------------|
| 0 | 0 |
| 1 | 4,000 |
| 2 | 8,000 |
| 3 | 15,000 |
| 4 | 35,000 |
| 5 | 65,000 |
| 6 | 120,000 |
| 7 | 220,000 |
| 8 | 400,000 |

**Note:** 8 riders in top 15 = 100× the 1-rider payout. Highly nonlinear.
Feasibility is stage-type dependent — see Strategy section.

---

## 4. Team Time Trial (TTT) Stages

On TTT stages, these components are **completely replaced:**

| Replaced | By |
|----------|-----|
| Stage finish position | TTT team placement value |
| Team Bonus (Holdbonus) | TTT scoring |
| Late arrival penalty | Not applied |
| Stage depth bonus (Etapebonus) | Not applied |

**TTT placement value** (to ALL active riders from that real-world team):

| TTT Placement | Value per rider (kr) |
|--------------|---------------------|
| 1st | +200,000 |
| 2nd | +150,000 |
| 3rd | +100,000 |
| 4th | +50,000 |
| 5th | +25,000 |
| 6th+ | 0 (no penalty) |

**What still applies normally on TTT stages:**
- GC standing (positions 1–10)
- Jersey bonuses
- Sprint/KOM points

**Strategic implication:** Having 2 riders from the best TTT team
= 2 × 200,000 = +400,000 from a single stage. Identify strong TTT
teams before the stage.

---

## 5. Financial Mechanics

### 5.1 Bank Interest
- +0.5% per round on cash held in bank
- Compounding — interest earns interest
- Incentivises not overpaying for marginal rider upgrades

### 5.2 Transfer Fee
- −1% of the **purchased** rider's current value
- Applied on buy, not sell
- Free transfers before Stage 1

### 5.3 Total Score
- Final ranking = sum of all 8 rider values + bank balance
- Bank includes: all captain bonus deposits + all stage depth deposits + interest

---

## 6. Trading Rules

- Window opens ~20:30 daily (2–3 hours after stage finish)
- Rounds 1 and 2: opens by 23:00 at latest
- Window closes when next stage starts
- **Finish all trades 30 minutes before announced start** — times change
- Captain change is free, any time window is open
- Captain locked at stage start

---

## 7. Confirmed Rule Clarifications

| Topic | Confirmed behaviour |
|-------|-------------------|
| Captain lock | Locked at stage start. Set in preceding window. Free to change. |
| Captain negative days | Only positive growth doubled. Losses NOT amplified. |
| Negative sprint/KOM | Sprint/KOM points ≥ 0 always. Only late arrival + DNF/DNS give negative value. |
| GC ties | Same finish time → positions assigned by crossing order. Individual positions, no shared ranks. |
| TTT bottom teams | 6th place and below = 0. No late arrival penalty either. |
| Transfer contracts | Gold = unlimited. 8-contract limit = Basic accounts only. |
| Final score | Rider values + bank (includes captain deposits, etapebonus deposits, interest). |
| DNF sprint/KOM | DNF riders still earn sprint/KOM points accumulated before abandonment. |
| DNF team bonus | DNF riders do NOT receive team bonus on that stage. |

---

## 8. Strategy Rules (for optimizer)

### Transfer Economics
- Fee = 1% of purchase price
- Must recover fee from future outperformance across remaining stages
- Rule of thumb: only transfer if new rider expected to outperform by > fee
- Fewer stages remaining → harder to justify any transfer
- Typical race: 2–4 transfers per week at break-even economics

### GC Rider Long-Term Value
- Top-5 GC: +60,000–100,000 kr per stage from standing alone
- Over 21 stages: 1,260,000–2,100,000 kr guaranteed from one rider
- Selling a top-5 GC rider mid-race is almost always wrong
- Exception: DNF/DNS — sell immediately regardless

### Stage Type → Strategy

| Stage Type | Primary value | Etapebonus | Late arrival risk | Key action |
|------------|--------------|------------|------------------|------------|
| Flat/Sprint | Stage finish + sprint points | HIGH | LOW | Target 6–8 top-15 riders. Sprinter captain. |
| Hilly/Breakaway | GC standing | LOW | MEDIUM | Accept 0–2 top-15. Don't over-trade. GC captain. |
| Mountain | GC standing + climbers | LOW–MEDIUM | LOW | Retain GC riders. Top climber captain. |
| ITT | Stage finish (TT specialists) | LOW | HIGH | Sell pure sprinters before long ITTs. TT captain. |
| TTT | TTT team placement | N/A | NONE | 2 riders from best TTT team. |

### Sprint/KOM Research Required Per Stage
- Must check official roadbook before each stage
- Number and location of intermediate sprints varies significantly
- Some hilly stages: sprint point at climb summit (favours breakaway, not sprinters)
- Mountain stages: 20+ KOM points possible for polka dot contender
- All of this affects expected value calculations for individual riders

### DNS Alert Rule
- Any rider with `isOut: true` in API → sell immediately
- Every stage they remain costs −100,000 kr
- No exceptions, no waiting to see if they recover
