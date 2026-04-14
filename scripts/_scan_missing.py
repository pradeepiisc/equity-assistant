"""Scan all company folders for missing master_report.md and transcript availability."""
import json
from pathlib import Path

d = Path(__file__).parent.parent / "data" / "companies"

missing = []
has_report = []

for co_dir in sorted(d.iterdir()):
    if not co_dir.is_dir():
        continue
    sym = co_dir.name
    mr = co_dir / "reports" / "master_report.md"
    t_dir = co_dir / "transcripts"
    f_dir = co_dir / "financials"
    t_count = len(list(t_dir.glob("*.pdf"))) if t_dir.exists() else 0
    f_count = len(list(f_dir.glob("*.txt"))) if f_dir.exists() else 0

    if mr.exists():
        has_report.append(sym)
    else:
        missing.append((sym, t_count, f_count))

print(f"Total companies: {len(missing) + len(has_report)}")
print(f"Already have master_report.md: {len(has_report)}")
print(f"Missing master_report.md: {len(missing)}")
print()

can_run = [(s, t, f) for s, t, f in missing if t > 0]
no_transcripts = [(s, t, f) for s, t, f in missing if t == 0]

print(f"  Can run analysis (have transcripts): {len(can_run)}")
for s, t, f in can_run:
    print(f"    {s:<25s} transcripts={t} financials={f}")

print(f"\n  Cannot run (no transcripts): {len(no_transcripts)}")
for s, t, f in no_transcripts:
    print(f"    {s:<25s} transcripts={t} financials={f}")
