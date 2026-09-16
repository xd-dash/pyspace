from __future__ import annotations

import argparse
import statistics
import time

from flask import Flask, request

from pyspace.gospace import GospaceBackend, GospaceConfig
from pyspace.supervisor import ProcessSupervisor


def percentile(values: list[int], p: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--samples", type=int, default=2_000)
    args = parser.parse_args()

    app = Flask(__name__)
    supervisor = ProcessSupervisor()
    config = GospaceConfig.from_binary(args.binary, name="uds-benchmark")
    backend = GospaceBackend(supervisor, "uds-benchmark", config)
    try:
        with app.test_request_context("/__pyspace_uds_miss", method="GET"):
            # First call includes process spawn/readiness and is reported
            # separately. Warm calls preserve the old implementation exactly,
            # including supervisor acquire/readiness probing and UDS HTTP proxy.
            before = time.perf_counter_ns()
            first = backend(request)
            cold_us = (time.perf_counter_ns() - before) / 1_000
            if first.status_code != 404:
                raise AssertionError(first.status_code)

            for _ in range(50):
                backend(request)
            timings = []
            start = time.perf_counter_ns()
            for _ in range(args.samples):
                before = time.perf_counter_ns()
                response = backend(request)
                timings.append(time.perf_counter_ns() - before)
                if response.status_code != 404:
                    raise AssertionError(response.status_code)
            elapsed = time.perf_counter_ns() - start

        print("uds_subprocess")
        print(f"  cold_spawn_us={cold_us:.3f}")
        print(f"  mean_us={statistics.fmean(timings) / 1_000:.3f}")
        print(f"  median_us={statistics.median(timings) / 1_000:.3f}")
        print(f"  p95_us={percentile(timings, 0.95) / 1_000:.3f}")
        print(f"  p99_us={percentile(timings, 0.99) / 1_000:.3f}")
        print(f"  throughput_rps={args.samples / (elapsed / 1_000_000_000):.3f}")
    finally:
        supervisor.shutdown()


if __name__ == "__main__":
    main()
