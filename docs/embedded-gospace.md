# Embedded gospace deployment

Set `PYSPACE_GOSPACE_LIBRARY` to a deployment-specific `libgospace.so` built from gospace's `cmd/cshared`. Pyspace loads it once and dispatches in-process: there is no child process, Unix socket, readiness probe, or internal HTTP serialization.

For the lowest per-request overhead, prebuild `native/_gospace_native.c` for CPython 3.12 and place the resulting `_gospace_native*.so` beside the function. `NativeGospace` prefers that extension and falls back to stdlib `ctypes` when it is absent.

Python router support remains lazy rather than requiring a separate deployment flavor. With no `ROUTER_MODULE`, no customer Flask router is imported and no Jinja environment is constructed. An authenticated `X-Pyspace-Module` request can load a Python `ROUTES` module later. Once a Python router exists, pyspace checks its exact path ownership before falling through to gospace.

This preserves the earlier `pyspace-minimal` Gen 1 contract: Functions Framework owns Flask and calls one exported `main(request)` catchall; pyspace performs request-aware dispatch rather than registering competing Flask routes.

Google Cloud Functions 1st gen Python 3.12 is the intended host. Native Go routers are linked into the deployment's gospace library; OTA routers remain gospace-owned WASM artifacts and are cached inside the same process.
