"""
Enhanced DMA Calculator with Multiple Data Sources
==================================================
Calculates accurate 50-day and 200-day moving averages using multiple data sources.
Primary: yfinance (free, reliable)
Fallback: Kite (if permissions available)

Usage:
    python -m integrations.dma_calculator --symbol DCAL
    python -m integrations.dma_calculator --symbol DCAL --source yfinance --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

# Add project root to path for imports
sys.path.append(str(Path(__file__).parent.parent))


def fetch_yfinance_data(symbol: str, days: int = 200) -> pd.DataFrame:
    """
    Fetch historical data from yfinance.
    
    Args:
        symbol: Stock symbol (e.g., 'DCAL.NS' for NSE, 'DCAL.BO' for BSE)
        days: Number of days of historical data to fetch
        
    Returns:
        DataFrame with columns: date, open, high, low, close, volume
    """
    # Try NSE first, then BSE
    for suffix in ['.NS', '.BO']:
        ticker = f"{symbol}{suffix}"
        
        try:
            print(f"Trying yfinance ticker: {ticker}")
            stock = yf.Ticker(ticker)
            
            # Calculate date range - go back further to ensure we get enough data
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days + 100)  # More buffer for weekends/holidays
            
            # Fetch historical data
            df = stock.history(start=start_date, end=end_date)
            
            if df.empty:
                print(f"No data for {ticker}")
                continue
                
            # Reset index to get date as column
            df = df.reset_index()
            df = df.rename(columns={
                'Date': 'date',
                'Open': 'open',
                'High': 'high', 
                'Low': 'low',
                'Close': 'close',
                'Volume': 'volume'
            })
            
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date')
            
            print(f"Fetched {len(df)} total days for {ticker}")
            
            # Take only the most recent 'days' records
            df = df.tail(days)
            print(f"Using last {len(df)} days for DMA calculation")
            
            return df
            
        except Exception as e:
            print(f"Failed to fetch {ticker}: {e}")
            continue
    
    raise ValueError(f"Failed to fetch data for {symbol} from both NSE and BSE")


def fetch_kite_data(symbol: str, days: int = 200) -> Optional[pd.DataFrame]:
    """
    Fetch historical data from Kite (if permissions available).
    
    Args:
        symbol: Stock symbol
        days: Number of days of historical data to fetch
        
    Returns:
        DataFrame or None if permissions insufficient
    """
    try:
        from integrations.kite_connect import _get_kite_client
        
        kite = _get_kite_client()
        
        # Get instrument token
        instruments = kite.instruments("NSE")
        instrument = None
        
        for inst in instruments:
            if inst['tradingsymbol'] == symbol and inst['segment'] == 'NSE':
                instrument = inst
                break
        
        if not instrument:
            return None
            
        instrument_token = instrument['instrument_token']
        
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days + 30)
        
        # Fetch historical data
        historical = kite.historical_data(
            instrument_token=instrument_token,
            from_date=start_date.date(),
            to_date=end_date.date(),
            interval="day"
        )
        
        if not historical:
            return None
            
        # Convert to DataFrame
        df = pd.DataFrame(historical)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        df = df.tail(days)
        
        return df
        
    except Exception as e:
        print(f"Kite data fetch failed: {e}")
        return None


def calculate_dma(df: pd.DataFrame, period: int) -> pd.Series:
    """Calculate simple moving average for given period."""
    return df['close'].rolling(window=period, min_periods=period).mean()


def calculate_dmas(symbol: str, days: int = 200, source: str = "yfinance") -> Tuple[Dict, pd.DataFrame]:
    """
    Calculate 50-day and 200-day moving averages for a symbol.
    
    Args:
        symbol: Stock symbol
        days: Number of days of historical data to use
        source: Data source ('yfinance', 'kite', 'auto')
        
    Returns:
        Tuple of (result_dict, dataframe)
    """
    print(f"Fetching historical data for {symbol} from {source}...")
    
    # Try different data sources based on preference
    df = None
    actual_source = None
    
    if source == "kite" or source == "auto":
        df = fetch_kite_data(symbol, days)
        if df is not None:
            actual_source = "kite"
    
    if df is None and (source == "yfinance" or source == "auto"):
        try:
            df = fetch_yfinance_data(symbol, days)
            actual_source = "yfinance"
        except Exception as e:
            print(f"yfinance failed: {e}")
    
    if df is None:
        raise ValueError(f"Failed to fetch data for {symbol} from any source")
    
    if len(df) < 200:
        raise ValueError(f"Insufficient data: only {len(df)} days available, need at least 200")
    
    # Calculate DMAs
    df['dma_50'] = calculate_dma(df, 50)
    df['dma_200'] = calculate_dma(df, 200)
    
    # Get latest values
    latest = df.iloc[-1]
    latest_date = latest['date'].date()
    
    current_price = latest['close']
    dma_50 = latest['dma_50']
    dma_200 = latest['dma_200']
    
    # Calculate percentages
    pct_vs_50 = ((current_price - dma_50) / dma_50 * 100) if pd.notna(dma_50) else None
    pct_vs_200 = ((current_price - dma_200) / dma_200 * 100) if pd.notna(dma_200) else None
    
    result = {
        'symbol': symbol,
        'date': latest_date.isoformat(),
        'current_price': round(float(current_price), 2),
        'dma_50': round(float(dma_50), 2) if pd.notna(dma_50) else None,
        'dma_200': round(float(dma_200), 2) if pd.notna(dma_200) else None,
        'pct_vs_50': round(float(pct_vs_50), 2) if pct_vs_50 is not None else None,
        'pct_vs_200': round(float(pct_vs_200), 2) if pct_vs_200 is not None else None,
        'data_points': len(df),
        'data_source': actual_source
    }
    
    return result, df


def print_dma_results(result: Dict, verbose: bool = False) -> None:
    """Print DMA results in a formatted way."""
    print(f"\n{'='*60}")
    print(f"DMA CALCULATION: {result['symbol']} ({result['date']}) - Source: {result['data_source']}")
    print(f"{'='*60}")
    print(f"Current Price : ₹{result['current_price']:>8.2f}")
    print(f"50 DMA        : ₹{result['dma_50']:>8.2f}" if result['dma_50'] else "50 DMA        : N/A")
    print(f"200 DMA       : ₹{result['dma_200']:>8.2f}" if result['dma_200'] else "200 DMA       : N/A")
    
    if result['pct_vs_50'] is not None:
        sign = "+" if result['pct_vs_50'] >= 0 else ""
        print(f"vs 50 DMA     : {sign}{result['pct_vs_50']:>6.2f}%")
    
    if result['pct_vs_200'] is not None:
        sign = "+" if result['pct_vs_200'] >= 0 else ""
        print(f"vs 200 DMA    : {sign}{result['pct_vs_200']:>6.2f}%")
    
    print(f"Data Points   : {result['data_points']} days")
    
    if verbose and result['pct_vs_200'] is not None:
        print(f"\nSignal: ", end="")
        if result['pct_vs_200'] > 10:
            print("🟢 Strongly Above 200 DMA (Bullish)")
        elif result['pct_vs_200'] > 0:
            print("🔵 Above 200 DMA (Mildly Bullish)")
        elif result['pct_vs_200'] > -10:
            print("🟡 Below 200 DMA (Mildly Bearish)")
        else:
            print("🔴 Strongly Below 200 DMA (Bearish)")


def compare_with_existing(result: Dict) -> None:
    """Compare with existing DMA values from daily report if available."""
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
            
        # Read the report and find DCAL
        content = daily_report_path.read_text()
        symbol = result['symbol']
        
        # Look for the symbol in the DMA status section
        for line in content.split('\n'):
            if symbol in line and 'DMA' in line:
                print(f"\n{'─'*60}")
                print(f"COMPARISON WITH EXISTING DAILY REPORT:")
                print(f"Existing: {line.strip()}")
                print(f"New:      {symbol} ₹{result['current_price']}  200DMA=₹{result['dma_200']}  ({result['pct_vs_200']:+.1f}%)")
                break
                
    except Exception as e:
        print(f"Could not compare with existing data: {e}")


def main():
    parser = argparse.ArgumentParser(description="Calculate accurate DMAs using multiple data sources")
    parser.add_argument("--symbol", required=True, help="Stock symbol (e.g., DCAL)")
    parser.add_argument("--days", type=int, default=200, help="Days of historical data (default: 200)")
    parser.add_argument("--source", choices=["yfinance", "kite", "auto"], default="yfinance", help="Data source (default: yfinance)")
    parser.add_argument("--verbose", action="store_true", help="Verbose output with signals")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--save-csv", action="store_true", help="Save historical data to CSV")
    parser.add_argument("--compare", action="store_true", help="Compare with existing daily report values")
    
    args = parser.parse_args()
    
    try:
        result, df = calculate_dmas(args.symbol, args.days, args.source)
        
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print_dma_results(result, args.verbose)
            
        if args.compare:
            compare_with_existing(result)
        
        if args.save_csv:
            csv_path = Path(f"dma_data_{args.symbol}_{args.days}days_{result['data_source']}.csv")
            df.to_csv(csv_path, index=False)
            print(f"\nHistorical data saved to: {csv_path}")
            
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
