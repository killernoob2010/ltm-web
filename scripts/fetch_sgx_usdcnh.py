#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from backend.app.sgx_usdcnh import SgxUsdCnhFetchError, fetch_sgx_usdcnh_quote


def parse_months(values):
    months = []
    for value in values:
        month = int(value)
        if month < 1 or month > 12:
            raise argparse.ArgumentTypeError(f"Month must be 1-12, got {value}")
        months.append(month)
    return months


def print_table(quotes) -> None:
    print("contract | symbol | USD/CNH | change | trade_time | source | url")
    print("--- | --- | ---: | --- | --- | --- | ---")
    for item in quotes:
        change = ""
        if item.price_change and item.percent_change:
            change = f"{item.price_change} ({item.percent_change})"
        elif item.price_change:
            change = item.price_change
        print(
            f"{item.year}-{item.month:02d} | {item.symbol} | "
            f"{item.last_price:.4f} | {change or '-'} | "
            f"{item.trade_time or '-'} | {item.source} | {item.url}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch SGX USD/CNH futures quotes from Barchart."
    )
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--months", nargs="+", default=["6", "7", "8"])
    parser.add_argument("--json", action="store_true", help="Output JSON instead of a table.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    try:
        months = parse_months(args.months)
        quotes = [
            fetch_sgx_usdcnh_quote(args.year, month, args.timeout, args.retries)
            for month in months
        ]
    except (argparse.ArgumentTypeError, SgxUsdCnhFetchError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([asdict(item) for item in quotes], ensure_ascii=False, indent=2))
    else:
        print_table(quotes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
