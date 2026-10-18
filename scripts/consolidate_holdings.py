import pandas as pd
from pathlib import Path
import os
import re
import sys

def consolidate(user_id):
    base_dir = Path(f"portfolio/{user_id}/holdings")
    if not base_dir.exists():
        print(f"No holdings directory found for {user_id}")
        return
    
    date_folders = sorted([d for d in base_dir.iterdir() if d.is_dir() and re.match(r'\d{4}-\d{2}-\d{2}', d.name)])
    
    if not date_folders:
        print(f"No date folders found for {user_id}")
        return
        
    print(f"Found {len(date_folders)} date folders for {user_id}")
    
    all_data = {}
    all_dates = []
    
    for folder in date_folders:
        date_str = folder.name
        csv_path = folder / "holdings.csv"
        if not csv_path.exists():
            continue
            
        all_dates.append(date_str)
        try:
            df = pd.read_csv(csv_path)
            for _, row in df.iterrows():
                sym = row.get('tradingsymbol', row.get('symbol', ''))
                if not sym:
                    continue
                    
                if sym not in all_data:
                    all_data[sym] = {
                        'symbol': sym,
                        'quantity': row.get('quantity', 0),
                        'avg_price': row.get('average_price', 0)
                    }
                else:
                    all_data[sym]['quantity'] = row.get('quantity', 0)
                    all_data[sym]['avg_price'] = row.get('average_price', 0)
                    
                all_data[sym][date_str] = row.get('last_price', 0)
        except Exception as e:
            print(f"Error reading {csv_path}: {e}")
            
    df_out = pd.DataFrame(list(all_data.values()))
    
    # Fill missing dates with 0 before picking columns
    for date_str in all_dates:
        if date_str not in df_out.columns:
            df_out[date_str] = 0
            
    cols = ['symbol', 'quantity', 'avg_price'] + all_dates
    # Only keep columns that exist in the dataframe
    cols = [c for c in cols if c in df_out.columns]
    
    df_out = df_out[cols].fillna(0)
    
    out_path = base_dir / "running_holdings.csv"
    df_out.to_csv(out_path, index=False)
    print(f"Saved {out_path}")

if __name__ == "__main__":
    consolidate("ZV3899")
    consolidate("WN5759")
