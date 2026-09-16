from __future__ import annotations

import argparse
import os
import statistics
import time

from flask import Flask, request

from pyspace.native import GospaceNative
from pyspace.service import Service


def percentile(values: list[int], p: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]


def measure(call, samples: int, warmups: int = 500) -> dict[str, float]:
    for _ in range(warmups):
        call()
    timings = []
    start = time.perf_counter_ns()
    for _ in range(samples):
        before = time.perf_counter_ns()
        call()
        timings.append(time.perf_counter_ns() - before)
    elapsed = time.perf_counter_ns() - start
    return {
        "mean_us": statistics.fmean(timings) / 1_000,
        "median_us": statistics.median(timings) / 1_000,
        "p95_us": percentile(timings, 0.95) / 1_000,
        "p99_us": percentile(timings, 0.99) / 1_000,
        "throughput_rps": samples / (elapsed / 1_000_000_000),
    }


def print_result(name: str, result: dict[str, float]) -> None:
    print(name)
    for key, value in result.items():
        print(f"  {key}={value:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=10_000)
    parser.add_argument("--wasm", required=True)
    parser.add_argument("--assert", dest="assert_result", action="store_true")
    args = parser.parse_args()

    app = Flask(__name__)
    native = GospaceNative(os.environ.get("PYSPACE_GOSPACE_LIBRARY"))

    # Python ROUTES baseline: same Service routing machinery used by pyspace,
    # with gospace disabled so this measures the Python-only warm path.
    service = Service(enable_gospace=False)
    service.register_routes("python-bench", {"/python-hello": lambda: "hello from Python\n"})
    with app.test_request_context("/python-hello", method="GET"):
        python_result = measure(lambda: service.dispatch(request), args.samples)

    # Deployment-composed Go router. The qualification build injects this
    # immutable route into cmd/cshared before libgospace.so is compiled.
    with app.test_request_context("/native-hello", method="GET"):
        def native_call():
            response = native.dispatch(request)
            if response.status_code != 200 or response.get_data() != b"hello from native Go\n":
                raise AssertionError((response.status_code, response.get_data()))
        native_result = measure(native_call, args.samples)

    wasm = open(args.wasm, "rb").read()
    token = os.environ["GOSPACE_CONTROL_TOKEN"]

    # Cold WASM is intentionally measured separately from warm execution: this
    # request crosses the same C ABI and performs digesting, wazero compilation,
    # immutable registration and activation. It executes only once because the
    # router name is immutable.
    with app.test_request_context(
        "/_gospace/wasm/wasm-bench-v1?activate=true",
        method="POST",
        data=wasm,
        headers={
            "X-Gospace-Control-Token": token,
            "X-Gospace-Routes": "GET /users/{id}",
            "Content-Type": "application/wasm",
        },
    ):
        before = time.perf_counter_ns()
        response = native.dispatch(request)
        cold_wasm_us = (time.perf_counter_ns() - before) / 1_000
        if response.status_code != 201:
            raise AssertionError((response.status_code, response.get_data()))

    # Warm WASM means compiled module cached in gospace. The current wazero
    # handler still instantiates a module instance per request; this benchmark
    # therefore measures the actual current warm runtime, not an idealized ABI.
    with app.test_request_context(
        "/users/42",
        method="GET",
        headers={"X-Gospace-Router": "wasm-bench-v1"},
    ):
        def wasm_call():
            response = native.dispatch(request)
            if response.status_code != 200 or response.get_data() != b"user: 42\n":
                raise AssertionError((response.status_code, response.get_data()))
        wasm_result = measure(wasm_call, max(1_000, args.samples // 5), warmups=100)

    print_result("python_routes", python_result)
    print_result("native_go", native_result)
    print(f"wasm_cold_compile_register_us={cold_wasm_us:.3f}")
    print_result("wasm_warm", wasm_result)

    if args.assert_result:
        if native_result["median_us"] > 1_000:
            raise SystemExit("warm native ABI median exceeded 1 ms")
        if cold_wasm_us <= 0:
            raise SystemExit("invalid cold WASM measurement")


if __name__ == "__main__":
    main()
