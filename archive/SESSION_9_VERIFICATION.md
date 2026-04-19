# Session 9 Verification Prompt

Paste the block below into your Claude project to verify that all Session 9 work is in order.

---

```
You are helping verify the Session 9 build of the Holdet fantasy cycling decision support tool.
The project is a Python CLI + Next.js frontend for Giro d'Italia 2026.

Working directory: ~/Claude/Holdet
Python: python3.14

Please run the following checks in order and report any failures.

## 1. Tests
```bash
cd ~/Claude/Holdet
python3.14 -m pytest --tb=short -q
```
Expected: 316 tests passing, 0 failures.

## 2. Rider type classification
```bash
python3.14 -c "
from scoring.probabilities import _rider_type
from data_types import Rider, Stage
import datetime

r_gc = Rider(holdet_id='1', name='GC', team='T', value=10_000_000,
             in_my_team=False, is_captain=False, status='active',
             gc_position=3, sprint_points=0, kom_points=0,
             jerseys=[], is_dns=False, is_dnf=False)
r_dom = Rider(holdet_id='2', name='DOM', team='T', value=2_000_000,
              in_my_team=False, is_captain=False, status='active',
              gc_position=None, sprint_points=0, kom_points=0,
              jerseys=[], is_dns=False, is_dnf=False)

from data_types import Stage
s_mtn = Stage(number=15, stage_type='mountain', distance=180, pcs_profile_score=4,
              gradient_final_km=8.5, ps_final_25k=3200,
              sprint_points=0, kom_points=0, date=datetime.date(2026,6,1))
s_flat = Stage(number=5, stage_type='flat', distance=200, pcs_profile_score=1,
               gradient_final_km=1.2, ps_final_25k=800,
               sprint_points=3, kom_points=0, date=datetime.date(2026,5,15))

print('GC rider on mountain:', _rider_type(r_gc, s_mtn))   # expect: gc
print('GC rider on flat:', _rider_type(r_gc, s_flat))       # expect: sprinter
print('Dom rider on mountain:', _rider_type(r_dom, s_mtn))  # expect: domestique
"
```

## 3. Odds module
```bash
python3.14 -c "
from scoring.odds import decimal_to_implied, normalise, odds_to_p_win
raw = {'r1': 2.5, 'r2': 4.0, 'r3': 6.0}
implied = {k: decimal_to_implied(v) for k, v in raw.items()}
norm = normalise(implied)
print('Implied:', {k: round(v,4) for k,v in implied.items()})
print('Normalised:', {k: round(v,4) for k,v in norm.items()})
print('Sum normalised:', round(sum(norm.values()), 6))  # expect: 1.0
p_win = odds_to_p_win(raw)
print('p_win r1:', round(p_win['r1'], 4))
"
```

## 4. Supabase sync script (dry-run)
```bash
cd ~/Claude/Holdet
python3.14 scripts/sync_to_supabase.py --race giro_2026 --dry-run 2>&1 | head -20
```
Expected: Script starts without ImportError. If `supabase` not installed, install first:
```bash
pip install supabase
```

## 5. Frontend build
```bash
cd ~/Claude/Holdet/frontend
npm run build 2>&1 | tail -20
```
Expected: Build completes successfully (exit 0). No TypeScript errors.

## 6. Live site
Open https://holdet.syndikatet.eu in a browser.
- [ ] Site loads (not "Site Not Found")
- [ ] `/auth` page shows email/password form
- [ ] After login, `/briefing` loads without JS errors
- [ ] `/team` page shows 8 rider cards or empty state
- [ ] `/stages` page shows list of 21 stages

## 7. State file integrity
```bash
python3.14 -c "
import json, pathlib
state = json.loads(pathlib.Path('state.json').read_text())
print('Keys:', list(state.keys()))
print('Race:', state.get('race'))
print('Bank:', state.get('bank'))
print('Riders in team:', sum(1 for r in state.get('riders', []) if r.get('in_my_team')))
"
```

## 8. Keep-alive workflow
Check `.github/workflows/keep_alive.yml` exists and contains the correct cron schedule:
```bash
grep "schedule" ~/Claude/Holdet/.github/workflows/keep_alive.yml
```
Expected: `0 9 */5 * *`

## Summary
Report results as a table:

| Check | Status | Notes |
|-------|--------|-------|
| 316 tests passing | | |
| _rider_type classification correct | | |
| Odds module normalises to 1.0 | | |
| Sync script importable | | |
| Frontend builds cleanly | | |
| Live site loads | | |
| State file has correct keys | | |
| Keep-alive cron correct | | |

Flag any failures with the exact error output.
```
