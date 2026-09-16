"""Microbenchmark the layers of the embedded gospace request boundary."""
from __future__ import annotations

import argparse
import statistics
import time

from flask import Flask, request

import _gospace_native
from pyspace.embedded import EmbeddedGospaceBackend
from pyspace.native import NativeGospace


def measure(label, fn, iterations, rounds=7):
    samples = []
    for _ in range(rounds):
        start = time.perf_counter_ns()
        for _ in range(iterations):
            fn()
        samples.append((time.perf_counter_ns() - start) / iterations)
    median = statistics.median(samples)
    print(f"{label}: {median / 1000:.2f} us/op (median of {rounds}, n={iterations})")
    return median


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("library")
    parser.add_argument("--iterations", type=int, default=5000)
    args = parser.parse_args()

    runtime = NativeGospace(args.library)
    raw = lambda: _gospace_native.dispatch(b"GET", b"/missing", b"Host:example\nX-Test:value\n", b"")
    wrapped = lambda: runtime.dispatch(b"GET", b"/missing", b"Host:example\nX-Test:value\n", b"")

    app = Flask(__name__)
    backend = EmbeddedGospaceBackend(args.library)
    ctx = app.test_request_context("/missing", headers={"X-Test": "value"})
    ctx.push()
    try:
        # Initialize all lazy runtime state before measuring steady state.
        raw(); wrapped(); backend(request)
        measure("c-extension", raw, args.iterations)
        measure("native-wrapper", wrapped, args.iterations)
        measure("flask-adapter", lambda: backend(request), args.iterations)
    finally:
        ctx.pop()


if __name__ == "__main__":
    main()
