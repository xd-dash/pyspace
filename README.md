# pyspace

`pyspace` is a Python 3.12 Google Cloud Functions Gen 1 composition shell. Flask/Functions Framework owns the single HTTP entry point; pyspace keeps routing below Flask so runtime registration never mutates Flask's URL map.

## Runtime shape

```text
Python / Functions Framework
          |
        pyspace
       /       \
Python ROUTES   gospace C ABI
                   |
             native Go + WASM
```

Gospace is in-process. There is no child process, Unix socket, readiness protocol, internal HTTP proxy, or supervisor. `PYSPACE_GOSPACE_LIBRARY` points at a `libgospace.so` built from gospace's `cmd/cshared` ABI v1. If unset, the dynamic linker searches for `libgospace.so`.

`PYSPACE_GOSPACE=auto` (default) lazily loads gospace only after Python routing misses. `off` produces a Python-only deployment with no native-library load attempt. `required` makes a missing native library fatal. This keeps Python support optional without introducing separate pyspace implementations.

The hot native path performs one Python→C call per request. Method, URI, headers and body cross as coarse byte spans; gospace owns native/WASM route resolution, WASM compilation/cache lifecycle and response generation.

## Bundled Python routers

An importable module can expose the same minimal contract as `pyspace-minimal`:

```python
from flask import request

def hello():
    return {"hello": request.args.get("name", "world")}

ROUTES = {"/hello": hello}
```

Set `ROUTER_MODULE=hello_router`. Optional `PYSPACE_ROUTER_NAME` changes its immutable registry name and `PYSPACE_ROUTER_ACTIVATE=true` makes it win collisions with other Python routers.

## Dynamic Python

Trusted single-file Python routers can be compiled directly from bytes without a `/tmp` write/read cycle:

```http
POST /_pyspace/source/foo-v1
X-Pyspace-Control-Token: ...
X-Pyspace-Python-Sha256: sha256:<digest>
Content-Type: text/x-python

...module source exposing ROUTES...
```

The digest is checked before execution. The source is compiled with a synthetic `pyspace://...` filename, executed as an ordinary module, and published only after its `ROUTES` mapping validates. Names are immutable: publish `foo-v2` rather than replacing `foo-v1`. Dynamic Python executes in the Cloud Function's trust domain and may import dependencies already installed in the deployment.

Importable modules can also cold-load on the first application request with `X-Pyspace-Module` plus `X-Pyspace-Control-Token`. Once registered, subsequent requests need no hint.

## Dispatch

```text
health/control
    -> pyspace
explicit X-Pyspace-App
    -> named Python app
Python route index
    -> active owner, then first immutable owner
optional import-module cold hint
    -> register + dispatch same request
otherwise
    -> gospace gs_dispatch
    -> native registry / dynamic WASM registry
```

Python's route index uses copy-on-write publication. Warm reads therefore require no Python lock; registration is the uncommon synchronized path.

## Cloud Function entry point

```python
import os
from os import path
from pyspace import Service

app = Service(root=os.environ.get("PYSPACE_ROOT", path.dirname(path.abspath(__file__))))
_dispatch = app.build()

def main(request):
    return _dispatch(request)
```

## Building gospace

From the gospace module on its embedded-C-ABI branch/design:

```sh
go build -buildmode=c-shared -trimpath -ldflags='-s -w' -o libgospace.so ./cmd/cshared
```

Deployment-specific native routers are linked into that artifact at build time. Routers absent from the build remain eligible for gospace's immutable OTA WASM path.
