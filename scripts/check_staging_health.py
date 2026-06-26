#!/usr/bin/env python3
"""Check Render staging health with retries.

This script is intentionally conservative: intermittent TLS/connectivity
failures from the local test runner should not be reported as application
failures unless every attempt fails.
"""

from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.error
import urllib.request


DEFAULT_URL = "https://ltm-web-staging.onrender.com/api/health"


def probe(url: str, timeout: float) -> dict:
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": "ltm-staging-health-check/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(500).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "elapsed_sec": round(time.perf_counter() - started, 3),
                "body": body,
                "error_type": None,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(500).decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "body": body,
            "error_type": "http",
            "error": str(exc),
        }
    except (TimeoutError, ssl.SSLError, urllib.error.URLError, OSError) as exc:
        return {
            "ok": False,
            "status": None,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "body": "",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check staging /api/health with retry-aware reporting.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--sleep", type=float, default=3)
    args = parser.parse_args()

    results = []
    for attempt in range(1, args.attempts + 1):
        result = probe(args.url, args.timeout)
        result["attempt"] = attempt
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if result["ok"]:
            break
        if attempt < args.attempts:
            time.sleep(args.sleep)

    successes = [item for item in results if item["ok"]]
    http_failures = [item for item in results if item["status"] and item["status"] >= 400]

    if successes:
        print("RESULT: staging reachable; earlier connection failures should be treated as test-channel noise.")
        return 0
    if http_failures:
        print("RESULT: staging returned HTTP errors; investigate Render/app logs.")
        return 2
    print("RESULT: no HTTP response after retries; likely local test-channel or network path issue.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
