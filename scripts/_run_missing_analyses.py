"""Run full_company_analysis for all companies missing master_report.md that have transcripts."""
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

d = Path(__file__).parent.parent / "data" / "companies"

# Find companies missing master_report.md that have transcripts
to_run = []
for co_dir in sorted(d.iterdir()):
    if not co_dir.is_dir():
        continue
    sym = co_dir.name
    mr = co_dir / "reports" / "master_report.md"
    if mr.exists():
        continue
    t_dir = co_dir / "transcripts"
    t_count = len(list(t_dir.glob("*.pdf"))) if t_dir.exists() else 0
    if t_count > 0:
        to_run.append(sym)

print(f"Will run full_company_analysis for {len(to_run)} companies\n")

from workflows.full_company_analysis import run as run_analysis

results = []
for i, sym in enumerate(to_run, 1):
    print(f"\n{'#'*60}")
    print(f"  [{i}/{len(to_run)}] {sym}")
    print(f"{'#'*60}")
    start = time.time()
    try:
        run_analysis(sym)
        elapsed = time.time() - start
        mr = d / sym / "reports" / "master_report.md"
        if mr.exists():
            results.append((sym, "SUCCESS", f"{elapsed:.0f}s"))
            print(f"  => SUCCESS ({elapsed:.0f}s)")
        else:
            results.append((sym, "ABORTED", f"{elapsed:.0f}s"))
            print(f"  => ABORTED (no report created) ({elapsed:.0f}s)")
    except Exception as exc:
        elapsed = time.time() - start
        results.append((sym, f"ERROR: {exc}", f"{elapsed:.0f}s"))
        print(f"  => ERROR: {exc} ({elapsed:.0f}s)")
        traceback.print_exc()

print(f"\n\n{'='*60}")
print(f"  BATCH COMPLETE: {len(results)} companies processed")
print(f"{'='*60}")
success = sum(1 for _, s, _ in results if s == "SUCCESS")
aborted = sum(1 for _, s, _ in results if s == "ABORTED")
errors = sum(1 for _, s, _ in results if s.startswith("ERROR"))
print(f"  Success: {success}  |  Aborted: {aborted}  |  Errors: {errors}")
print()
for sym, status, elapsed in results:
    print(f"  {sym:<25s} {status:<40s} {elapsed}")
