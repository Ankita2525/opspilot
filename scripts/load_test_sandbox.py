#!/usr/bin/env python3
"""Lightweight concurrency checks for sandbox lease and rate limits."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from backend.app.sandbox.lease_store import InMemoryGlobalSandboxLeaseStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Sandbox lease concurrency probe")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    store = InMemoryGlobalSandboxLeaseStore()
    results = []

    def attempt(i: int):
        return store.acquire(
            session_id=f"session-{i}",
            incident_id=f"incident-{i}",
            ttl_seconds=600,
        )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(attempt, i) for i in range(args.workers)]
        for future in as_completed(futures):
            results.append(future.result())

    acquired = sum(1 for r in results if r.acquired)
    print(f"workers={args.workers} acquired={acquired} (expected 1)")


if __name__ == "__main__":
    main()
