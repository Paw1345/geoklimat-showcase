"""Local load smoke test for GeoKlimat pages (S9, docs/SECURITY-REVIEW-2026-10-15.md).

Standard library only. Refuses any host other than loopback or a Compose service name,
so it can never be pointed at a provider or a public deployment by mistake.

    python scripts/load_smoke.py --base http://127.0.0.1:8000 --concurrency 8 --rounds 25
"""

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "web", "loadweb"}
PAGES = [
    "/",
    "/search/?q=klimat",
    "/search/?q=woda&sort=newest",
    "/legislation/",
    "/changes/",
    "/data/",
    "/registry/",
    "/information/",
    "/health/ready/",
]


HEADERS = {}


def fetch(base, path):
    started = time.perf_counter()
    try:
        request = urllib.request.Request(base + path, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except OSError:
        status = 0
    return path, status, (time.perf_counter() - started) * 1000


def percentile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=25)
    parser.add_argument(
        "--behind-proxy",
        action="store_true",
        help="send X-Forwarded-Proto: https like the production reverse proxy",
    )
    args = parser.parse_args()
    if args.behind_proxy:
        HEADERS["X-Forwarded-Proto"] = "https"
    if urlsplit(args.base).hostname not in LOCAL_HOSTS:
        raise SystemExit("Only local targets are allowed.")
    if not 1 <= args.concurrency <= 64 or not 1 <= args.rounds <= 1000:
        raise SystemExit("Concurrency 1-64, rounds 1-1000.")
    for path in PAGES:  # warm-up, not measured
        fetch(args.base, path)
    jobs = [path for _ in range(args.rounds) for path in PAGES]
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda path: fetch(args.base, path), jobs))
    wall = time.perf_counter() - started
    report = {"concurrency": args.concurrency, "requests": len(results), "seconds": wall}
    report["requests_per_second"] = round(len(results) / wall, 1)
    report["pages"] = {}
    for path in PAGES:
        rows = [row for row in results if row[0] == path]
        times = [row[2] for row in rows]
        report["pages"][path] = {
            "ok": sum(1 for row in rows if row[1] == 200),
            "errors": sum(1 for row in rows if row[1] != 200),
            "p50_ms": round(statistics.median(times), 1),
            "p95_ms": round(percentile(times, 0.95), 1),
            "max_ms": round(max(times), 1),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if any(page["errors"] for page in report["pages"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
