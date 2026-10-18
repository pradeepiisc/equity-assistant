import os
import re
import json
import argparse
from pathlib import Path
from datetime import datetime

def main():
    parser = argparse.ArgumentParser(description="Parse anchor alignment to calculate relativeness")
    parser.add_argument("--user", default="ZV3899", help="User ID")
    args = parser.parse_args()

    md_path = Path(f"portfolio/{args.user}/anchor_alignment.md")
    out_path = Path(f"portfolio/{args.user}/anchor_relativeness.json")

    if not md_path.exists():
        print(f"Error: {md_path} not found.")
        return

    with open(md_path, "r") as f:
        lines = f.readlines()

    parsed = []
    in_table1 = False
    for line in lines:
        if "Table 1: Anchor-Held Companies by Investment" in line:
            in_table1 = True
            continue
        if in_table1 and "Table 2:" in line:
            break
            
        if in_table1:
            parts = [p.strip() for p in line.split('|') if p.strip()]
            # Expecting: #, Company (SYMBOL), Mcap, Top Investor, Invest, My%, My Rank
            if len(parts) >= 6 and parts[0].isdigit():
                company_col = parts[1]
                match = re.search(r'\(([^)]+)\)', company_col)
                if match:
                    symbol = match.group(1)
                    try:
                        anchor_cr = float(parts[4].replace(',', ''))
                        my_pct = float(parts[5].replace('%', ''))
                        if my_pct > 0:
                            ratio = my_pct / anchor_cr
                            parsed.append({
                                "symbol": symbol,
                                "company": company_col,
                                "anchor_cr": anchor_cr,
                                "my_pct": my_pct,
                                "ratio": ratio
                            })
                    except ValueError:
                        pass
                        
    if not parsed:
        print("No valid data parsed.")
        return

    # Exits: Highest ratio (descending)
    parsed.sort(key=lambda x: x["ratio"], reverse=True)
    exits = parsed[:30]

    # Entries: Lowest ratio (ascending)
    parsed.sort(key=lambda x: x["ratio"])
    entries = parsed[:30]

    output_data = {
        "last_updated": datetime.now().isoformat(),
        "exits": exits,
        "entries": entries
    }

    with open(out_path, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"Successfully generated {out_path} with {len(exits)} exits and {len(entries)} entries.")

if __name__ == "__main__":
    main()
