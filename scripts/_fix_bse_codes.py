"""Fix meta.json for companies with BSE-code Screener URLs."""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "companies"

fixes = {
    "ASMTEC": {"screener_symbol": "526433", "screener_url": "https://www.screener.in/company/526433/"},
    "COSMICCRF": {"screener_symbol": "543928", "screener_url": "https://www.screener.in/company/543928/consolidated/"},
    "CHTR": {"screener_symbol": "544546", "screener_url": "https://www.screener.in/company/544546/"},
    "INFLAME": {"screener_symbol": "541083", "screener_url": "https://www.screener.in/company/541083/consolidated/"},
    "PATELCHEM": {"screener_symbol": "544460", "screener_url": "https://www.screener.in/company/544460/consolidated/", "name": "Patel Chem Specialities Ltd"},
}

for sym, updates in fixes.items():
    meta_path = DATA / sym / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
    else:
        meta = {"symbol": sym}
    meta.update(updates)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    print("Updated " + sym + ": screener_symbol=" + updates["screener_symbol"])

print("Done!")
