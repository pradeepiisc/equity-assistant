"""
Scan all company folders for missing data:
  - transcripts (PDFs in transcripts/)
  - PPTs (files in ppt/)
  - financials (files in financials/)
  - shareholding (files in shareholding/)
  - insights (files in insights/)
  - master_report.md
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "companies"


def count_files(folder: Path, ext: str | None = None) -> int:
    if not folder.exists():
        return 0
    if ext:
        return len([f for f in folder.iterdir() if f.suffix.lower() == ext])
    return len([f for f in folder.iterdir() if f.is_file()])


def main():
    all_dirs = sorted(d.name for d in DATA.iterdir() if d.is_dir())

    missing_transcripts = []
    missing_ppt = []
    missing_financials = []
    missing_shareholding = []
    missing_insights = []
    missing_master = []
    has_everything = []

    for sym in all_dirs:
        base = DATA / sym
        t_count = count_files(base / "transcripts", ".pdf")
        p_count = count_files(base / "ppt")
        f_count = count_files(base / "financials")
        s_count = count_files(base / "shareholding")
        i_count = count_files(base / "insights")
        has_master = (base / "reports" / "master_report.md").exists()

        missing = []
        if t_count == 0:
            missing_transcripts.append(sym)
            missing.append("transcripts")
        if p_count == 0:
            missing_ppt.append(sym)
            missing.append("ppt")
        if f_count == 0:
            missing_financials.append(sym)
            missing.append("financials")
        if s_count == 0:
            missing_shareholding.append(sym)
            missing.append("shareholding")
        if i_count == 0:
            missing_insights.append(sym)
            missing.append("insights")
        if not has_master:
            missing_master.append(sym)

        if not missing:
            has_everything.append(sym)

    print(f"Total companies: {len(all_dirs)}")
    print(f"Have ALL data (transcripts+ppt+financials+shareholding+insights): {len(has_everything)}")
    print()

    print(f"Missing transcripts: {len(missing_transcripts)}")
    for s in missing_transcripts:
        print(f"  {s}")
    print()

    print(f"Missing financials: {len(missing_financials)}")
    for s in missing_financials:
        print(f"  {s}")
    print()

    print(f"Missing shareholding: {len(missing_shareholding)}")
    for s in missing_shareholding:
        print(f"  {s}")
    print()

    print(f"Missing insights: {len(missing_insights)}")
    for s in missing_insights:
        print(f"  {s}")
    print()

    print(f"Missing PPTs: {len(missing_ppt)}")
    for s in missing_ppt:
        print(f"  {s}")
    print()

    print(f"Missing master_report.md: {len(missing_master)}")
    for s in missing_master:
        print(f"  {s}")
    print()

    # Summary: companies needing data fetch (missing ANY core data)
    needs_fetch = set()
    for s in missing_transcripts + missing_financials + missing_shareholding + missing_insights:
        needs_fetch.add(s)

    print(f"\n=== COMPANIES NEEDING DATA FETCH: {len(needs_fetch)} ===")
    for s in sorted(needs_fetch):
        base = DATA / s
        t = "T" if count_files(base / "transcripts", ".pdf") > 0 else "."
        p = "P" if count_files(base / "ppt") > 0 else "."
        f = "F" if count_files(base / "financials") > 0 else "."
        sh = "S" if count_files(base / "shareholding") > 0 else "."
        i = "I" if count_files(base / "insights") > 0 else "."
        m = "M" if (base / "reports" / "master_report.md").exists() else "."
        print(f"  {s:25s}  [{t}{p}{f}{sh}{i}{m}]  (T=transcripts P=ppt F=financials S=shareholding I=insights M=master)")


if __name__ == "__main__":
    main()
