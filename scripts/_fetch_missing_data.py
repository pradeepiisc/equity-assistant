"""
Batch data_fetch for all companies missing core data
(transcripts, financials, shareholding, insights).
Skips numeric duplicate folders.
"""
from __future__ import annotations
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "companies"
PYTHON = sys.executable

SKIP = {"543619", "544091", "544213", "544458", "544681"}


def count_files(folder: Path, ext: str | None = None) -> int:
    if not folder.exists():
        return 0
    if ext:
        return len([f for f in folder.iterdir() if f.suffix.lower() == ext])
    return len([f for f in folder.iterdir() if f.is_file()])


def get_needs_fetch() -> list[str]:
    needs = []
    for d in sorted(DATA.iterdir()):
        sym = d.name
        if sym in SKIP or not d.is_dir():
            continue
        t = count_files(d / "transcripts", ".pdf")
        f = count_files(d / "financials")
        s = count_files(d / "shareholding")
        i = count_files(d / "insights")
        if t == 0 or f == 0 or s == 0 or i == 0:
            needs.append(sym)
    return needs


def main():
    symbols = get_needs_fetch()
    total = len(symbols)
    print(f"\n{'='*60}")
    print(f"  BATCH DATA FETCH: {total} companies")
    print(f"{'='*60}\n")

    results = {}
    for i, sym in enumerate(symbols, 1):
        print(f"\n{'#'*60}")
        print(f"  [{i}/{total}] {sym}")
        print(f"{'#'*60}\n")

        t0 = time.time()
        try:
            proc = subprocess.run(
                [PYTHON, "-m", "workflows.data_fetch", sym, "--skip-valuepickr", "--non-interactive"],
                cwd=str(ROOT),
                timeout=300,
                capture_output=False,
                input="0\n" * 5,  # auto-skip any interactive prompts
                text=True,
            )
            elapsed = time.time() - t0
            if proc.returncode == 0:
                results[sym] = ("SUCCESS", elapsed)
            else:
                results[sym] = ("FAILED", elapsed)
        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            results[sym] = ("TIMEOUT", elapsed)
            print(f"  => TIMEOUT after {elapsed:.0f}s")
        except Exception as e:
            elapsed = time.time() - t0
            results[sym] = ("ERROR", elapsed)
            print(f"  => ERROR: {e}")

    print(f"\n{'='*60}")
    print(f"  BATCH DATA FETCH COMPLETE: {total} companies")
    print(f"{'='*60}")
    success = sum(1 for v in results.values() if v[0] == "SUCCESS")
    failed = sum(1 for v in results.values() if v[0] != "SUCCESS")
    print(f"  Success: {success}  |  Failed: {failed}\n")
    for sym, (status, elapsed) in results.items():
        print(f"  {sym:25s} {status:10s} {elapsed:.0f}s")


if __name__ == "__main__":
    main()
