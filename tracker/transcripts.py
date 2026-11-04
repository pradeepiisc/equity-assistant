"""
Old concall transcripts on disk (data/companies/*/transcripts/*.pdf): which ones the tracker has read, and moving
the read ones to the macOS Trash — only when you run `python -m tracker transcripts --trash` and confirm.

"Read" = the document pipeline extracted the PDF (its figures, quotes and page links are in the database; the
quarter's insights are in the company's notes). Unread, failed or skipped PDFs are never moved. A moved file is
listed in transcripts/.archived.json so the concall fetcher does not download it again.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .config import COMPANIES_DIR, ROOT
from .db import session

TRASH = Path.home() / ".Trash"


def inventory() -> dict:
    files = sorted(COMPANIES_DIR.glob("*/transcripts/*.pdf"))
    with session() as conn:
        st = {r["path"]: r["status"] for r in conn.execute(
            "SELECT path, status FROM documents WHERE path LIKE '%/transcripts/%'")}
    read, unread = [], []
    for f in files:
        rel = str(f.relative_to(ROOT))
        (read if st.get(rel) == "extracted" else unread).append({"path": rel, "status": st.get(rel) or "not queued",
                                                                    "mb": round(f.stat().st_size / 1e6, 2)})
    return {"files": len(files), "read": read, "unread": unread,
            "read_mb": round(sum(x["mb"] for x in read), 1), "unread_mb": round(sum(x["mb"] for x in unread), 1)}


def _archive(f: Path) -> None:
    idx = f.parent / ".archived.json"
    names = json.loads(idx.read_text()) if idx.exists() else []
    if f.name not in names:
        names.append(f.name)
    idx.write_text(json.dumps(sorted(names), indent=1))


def trash(paths: list[str]) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_root = TRASH / f"equity-assistant transcripts {stamp}"
    n = 0
    for rel in paths:
        f = ROOT / rel
        if not f.exists():
            continue
        dest = dest_root / f.relative_to(COMPANIES_DIR)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _archive(f)
        shutil.move(str(f), str(dest))           # into the Trash: Finder can put it back
        n += 1
    return n


def cli(trash: bool = False) -> int:
    inv = inventory()
    print(f"{inv['files']} transcript PDFs: {len(inv['read'])} read ({inv['read_mb']} MB), "
          f"{len(inv['unread'])} not read yet ({inv['unread_mb']} MB).")
    by = {}
    for x in inv["unread"]:
        by[x["status"]] = by.get(x["status"], 0) + 1
    if by:
        print("Not read yet: " + ", ".join(f"{v} {k}" for k, v in sorted(by.items())))
    if not trash:
        print("Nothing moved. `python -m tracker transcripts --trash` moves the read ones to the Trash (asks first).")
        return 0
    if not inv["read"]:
        print("Nothing to move yet.")
        return 0
    ans = input(f"Move {len(inv['read'])} read transcripts ({inv['read_mb']} MB) to the Trash? [y/N] ").strip().lower()
    if ans != "y":
        print("Nothing moved.")
        return 0
    print(f"Moved {trash_paths(inv['read'])} files to the Trash.")
    return 0


def trash_paths(items: list[dict]) -> int:
    return trash([x["path"] for x in items])
