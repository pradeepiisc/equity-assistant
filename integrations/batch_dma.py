"""
Batch DMA Calculator
===================
Calculate accurate DMAs for multiple stocks and compare with existing values.

Usage:
    python -m integrations.batch_dma --symbols DCAL BETA RELIANCE
    python -m integrations.batch_dma --watchlist  # Use watchlist.yaml
    python -m integrations.batch_dma --portfolio  # Use portfolio_companies.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

# Add project root to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from integrations.dma_calculator import calculate_dmas
from llm.client import get_config


def load_symbols_from_yaml(yaml_file: str) -> List[str]:
    """Load symbols from watchlist.yaml or portfolio_companies.yaml"""
    import yaml
    
    yaml_path = Path(yaml_file)
    if not yaml_path.exists():
        raise FileNotFoundError(f"{yaml_file} not found")
    
    with open(yaml_path, 'r') as f:
        data = yaml.safe_load(f)
    
    symbols = [item['symbol'] for item in data.get('stocks', [])]
    return symbols


def calculate_batch_dmas(symbols: List[str], source: str = "yfinance") -> List[Dict]:
    """Calculate DMAs for multiple symbols"""
    results = []
    
    for i, symbol in enumerate(symbols, 1):
        print(f"\n[{i}/{len(symbols)}] Processing {symbol}...")
        
        try:
            result, _ = calculate_dmas(symbol, 200, source)
            results.append(result)
            
            # Print summary
            pct_200 = result.get('pct_vs_200', 0)
            signal = "🟢" if pct_200 > 10 else "🔵" if pct_200 > 0 else "🟡" if pct_200 > -10 else "🔴"
            print(f"  {signal} {symbol}: ₹{result['current_price']} vs 200DMA: {pct_200:+.1f}%")
            
        except Exception as e:
            print(f"  ❌ {symbol}: Error - {e}")
            results.append({'symbol': symbol, 'error': str(e)})
    
    return results


def compare_with_daily_report(results: List[Dict]) -> None:
    """Compare batch results with existing daily report"""
    try:
        # Try to read the latest daily report
        report_path = Path("portfolio/ZV3899/holdings")
        if not report_path.exists():
            return
            
        # Find the latest date folder
        date_folders = [d for d in report_path.iterdir() if d.is_dir()]
        if not date_folders:
            return
            
        latest_folder = max(date_folders, key=lambda x: x.name)
        daily_report_path = latest_folder / "daily_report.md"
        
        if not daily_report_path.exists():
            return
            
        # Read the report
        content = daily_report_path.read_text()
        
        print(f"\n{'='*80}")
        print("COMPARISON WITH DAILY REPORT")
        print(f"{'='*80}")
        
        differences = []
        for result in results:
            if 'error' in result:
                continue
                
            symbol = result['symbol']
            new_200dma = result.get('dma_200')
            new_pct = result.get('pct_vs_200')
            
            # Look for the symbol in the DMA status section
            for line in content.split('\n'):
                if symbol in line and 'DMA' in line and '|' in line:
                    # Extract existing values from the line
                    parts = line.split('|')
                    if len(parts) >= 7:
                        try:
                            existing_price = float(parts[1].strip().replace('₹', '').replace(',', ''))
                            existing_200dma = float(parts[4].strip().replace('₹', '').replace(',', ''))
                            existing_pct = float(parts[6].strip().replace('%', '').replace('(', '').replace(')', ''))
                            
                            # Calculate differences
                            price_diff = result['current_price'] - existing_price
                            dma_diff = new_200dma - existing_200dma if new_200dma else 0
                            pct_diff = new_pct - existing_pct if new_pct else 0
                            
                            if abs(price_diff) > 0.1 or abs(dma_diff) > 0.1 or abs(pct_diff) > 0.1:
                                differences.append({
                                    'symbol': symbol,
                                    'price_diff': price_diff,
                                    'dma_diff': dma_diff,
                                    'pct_diff': pct_diff
                                })
                                print(f"🔄 {symbol}: Price ₹{price_diff:+.2f}, 200DMA ₹{dma_diff:+.2f}, Pct {pct_diff:+.1f}%")
                            
                        except (ValueError, IndexError):
                            pass
                    break
        
        if not differences:
            print("✅ All values match closely (differences < ₹0.1 or <0.1%)")
        else:
            print(f"\nFound {len(differences)} stocks with notable differences")
            
    except Exception as e:
        print(f"Could not compare with daily report: {e}")


def save_results(results: List[Dict], filename: str = "dma_batch_results.json") -> None:
    """Save results to JSON file"""
    output_path = Path(filename)
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Calculate DMAs for multiple stocks")
    parser.add_argument("--symbols", nargs="+", help="List of stock symbols")
    parser.add_argument("--watchlist", action="store_true", help="Use symbols from watchlist.yaml")
    parser.add_argument("--portfolio", action="store_true", help="Use symbols from portfolio_companies.yaml")
    parser.add_argument("--source", choices=["yfinance", "kite", "auto"], default="yfinance", help="Data source")
    parser.add_argument("--compare", action="store_true", help="Compare with daily report")
    parser.add_argument("--save", action="store_true", help="Save results to JSON")
    parser.add_argument("--limit", type=int, help="Limit number of symbols to process")
    
    args = parser.parse_args()
    
    # Get symbols
    symbols = []
    
    if args.symbols:
        symbols = args.symbols
    elif args.watchlist:
        symbols = load_symbols_from_yaml("watchlist.yaml")
    elif args.portfolio:
        symbols = load_symbols_from_yaml("portfolio_companies.yaml")
    else:
        print("Error: Must provide --symbols, --watchlist, or --portfolio")
        sys.exit(1)
    
    # Apply limit
    if args.limit:
        symbols = symbols[:args.limit]
    
    print(f"Processing {len(symbols)} symbols...")
    
    # Calculate DMAs
    results = calculate_batch_dmas(symbols, args.source)
    
    # Compare with existing report
    if args.compare:
        compare_with_daily_report(results)
    
    # Save results
    if args.save:
        save_results(results)
    
    # Summary
    successful = len([r for r in results if 'error' not in r])
    failed = len([r for r in results if 'error' in r])
    
    print(f"\n{'='*60}")
    print(f"BATCH DMA CALCULATION COMPLETE")
    print(f"{'='*60}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    
    if failed > 0:
        print(f"\nFailed symbols:")
        for result in results:
            if 'error' in result:
                print(f"  ❌ {result['symbol']}: {result['error']}")


if __name__ == "__main__":
    main()
