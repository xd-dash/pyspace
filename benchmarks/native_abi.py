from __future__ import annotations

import argparse
import os
import statistics
import time

from flask import Flask, request

from pyspace.native import GospaceNative


def percentile(values: list[int], p: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]


def run(samples: int = 20_000) -> dict[str, float]:
    app = Flask(__name__)
    native = GospaceNative(os.environ.get("PYSPACE_GOSPACE_LIBRARY"))

    # The stock c-shared build has no deployment-native router. A 404 still
    # traverses Python -> C -> Go service -> C -> Python and therefore measures
    # the complete warm ABI boundary without network/process noise.
    with app.test_request_context("/__pyspace_abi_miss", method="GET"):
        for _ in range(500):
            response = native.dispatch(request)
            if response.status_code != 404:
                raise AssertionError(response.status_code)

        timings: list[int] = []
        start = time.perf_counter_ns()
        for _ in range(samples):
            before = time.perf_counter_ns()
            response = native.dispatch(request)
            timings.append(time.perf_counter_ns() - before)
            if response.status_code != 404:
                raise AssertionError(response.status_code)
        elapsed = time.perf_counter_ns() - start

    return {
        "mean_us": statistics.fmean(timings) / 1_000,
        "median_us": statistics.median(timings) / 1_000,
        "p95_us": percentile(timings, 0.95) / 1_000,
        "p99_us": percentile(timings, 0.99) / 1_000,
        "throughput_rps": samples / (elapsed / 1_000_000_000),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=20_000)
    parser.add_argument("--assert", dest="assert_result", action="store_true")
    args = parser.parse_args()
    result = run(args.samples)
    for key, value in result.items():
        print(f"{key}={value:.3f}")

    # This is a regression tripwire, not a performance promise. GitHub-hosted
    # runners vary substantially; a real latency budget belongs on a pinned
    # runner after we have collected a baseline.
    if args.assert_result and result["median_us"] > 1_000:
        raise SystemExit("warm embedded ABI median exceeded 1 ms")


if __name__ == "__main__":
    main()
