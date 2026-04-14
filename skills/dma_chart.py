"""
DMA Chart Generator
===================
Creates a price chart with 40 DMA (yellow) and 99 DMA (black).

Usage:
    python -m skills.dma_chart DCAL
    python -m skills.dma_chart DCAL --exchange NSE --out portfolio/charts
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import matplotlib.pyplot as plt
import yfinance as yf


def _yf_ticker(symbol: str, exchange: str) -> str:
    sym = symbol.upper().replace(" ", "")
    exch = exchange.upper()
    suffix = ".BO" if exch == "BSE" else ".NS"
    return sym + suffix


def _fetch_prices(symbol: str, exchange: str, lookback_days: int):
    sym_orig = symbol.upper().replace(" ", "")
    sym_clean = sym_orig.replace("-SM", "").replace("-BE", "")
    exch = exchange.upper()
    primary, alt_exch = (".NS", ".BO") if exch != "BSE" else (".BO", ".NS")
    candidates = list(dict.fromkeys(
        s + sfx
        for s in ([sym_orig] if sym_orig != sym_clean else []) + [sym_clean]
        for sfx in [primary, alt_exch]
    ))
    best_ticker, best_hist = None, None
    for ticker in candidates:
        hist = yf.Ticker(ticker).history(period=f"{lookback_days}d", auto_adjust=False)
        if not hist.empty and (best_hist is None or len(hist) > len(best_hist)):
            best_ticker, best_hist = ticker, hist
    return best_ticker, best_hist


def _compute_sma(series, window: int):
    return series.rolling(window=window, min_periods=window).mean()


def generate_dma_chart(
    symbol: str,
    exchange: str = "NSE",
    output_dir: str | Path | None = None,
    lookback_days: int = 365,
    periods: Sequence[int] = (40, 99),
) -> Path | None:
    output_dir = Path(output_dir) if output_dir else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)

    ticker, hist = _fetch_prices(symbol, exchange, lookback_days)
    if hist is None:
        return None

    close = hist["Close"].dropna()
    if close.empty:
        return None

    sma_short = _compute_sma(close, periods[0])
    sma_long = _compute_sma(close, periods[1])

    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.plot(close.index, close.values, label="Price", color="#1f2937", linewidth=1.5)
    ax.plot(sma_short.index, sma_short.values, label=f"{periods[0]} DMA", color="#fbbf24", linewidth=1.6)
    ax.plot(sma_long.index, sma_long.values, label=f"{periods[1]} DMA", color="#111827", linewidth=1.6)
    ax.set_title(f"{symbol} ({ticker}) — {lookback_days}d")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper left", frameon=False)
    fig.tight_layout()

    out_path = output_dir / f"{symbol.upper()}_dma_chart.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate 40/99 DMA chart")
    parser.add_argument("symbol", help="Trading symbol e.g. DCAL")
    parser.add_argument("--exchange", default="NSE", help="NSE or BSE")
    parser.add_argument("--out", default=".", help="Output directory")
    parser.add_argument("--lookback", type=int, default=365, help="Lookback days")
    args = parser.parse_args()

    path = generate_dma_chart(
        symbol=args.symbol,
        exchange=args.exchange,
        output_dir=args.out,
        lookback_days=args.lookback,
    )
    if not path:
        print("Chart generation failed (no data).")
        return 1
    print(f"Chart saved → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
