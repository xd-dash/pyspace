# pyspace

`pyspace` is a Python 3.12 Google Cloud Functions Gen 1 host with no built-in app.

Normal requests do not need routing headers. Pyspace first looks for the route in already-registered Python routers, then asks any registered gospace apps. Hints are optional shortcuts for a caller that already knows what should handle the request.

## Python router

```python
# hello_router.py
from flask import request


def hello():
    return "payload=" + request.get_data(as_text=True)


ROUTES = {
    "/hello": hello,
}
```

If `hello_router` is already registered, this is enough:

```http
POST /hello
Content-Type: text/plain

abc
```

If the route is not registered yet, the same request can carry a loader hint:

```http
POST /hello
X-Pyspace-Module: hello_router
X-Pyspace-Control-Token: <PYSPACE_CONTROL_TOKEN>
Content-Type: text/plain

abc
```

Pyspace imports the module, registers it under its module name, then handles that same request. No separate registration call is required.

`X-Pyspace-App` is optional. Use it when you already know the exact registered app and want to skip catchall discovery:

```http
POST /hello
X-Pyspace-App: hello-v1
```

You can also pre-register the router at deployment:

```text
ROUTER_MODULE=hello_router
PYSPACE_ROUTER_NAME=hello-v1
PYSPACE_ROUTER_ACTIVATE=true
```

## Gospace

If a gospace executable is already registered with pyspace, ordinary requests that do not match a Python route fall through to gospace automatically.

A cold request can supply the executable path as a hint:

```http
POST /users/42
X-Pyspace-Gospace-Binary: /workspace/bin/gospace
X-Pyspace-Control-Token: <PYSPACE_CONTROL_TOKEN>
```

Pyspace registers/spawns gospace and forwards the same request over its Unix socket.

If the request also needs a cold WASM router inside gospace, it can carry gospace's own WASM hint in that same request. After both caches are warm, an ordinary request such as:

```http
GET /users/42
```

can resolve through:

```text
Python route lookup
    -> miss
registered gospace
    -> registered native/WASM route lookup
    -> match
```

## Resolution order

```text
explicit X-Pyspace-App, if supplied
    -> direct registered app
    -> load from supplied pyspace hint on a miss

otherwise
    -> exact registered Python ROUTES match
    -> registered gospace app(s)
    -> optional Python/gospace loader hint
    -> 404
```

Hints improve routing when the caller or a CDN already knows the destination, but they are not required for routes that the warm instance can discover itself.

## Cloud Function entry point

```python
import os
from os import path

from pyspace import Service

app = Service(
    root=os.environ.get(
        "PYSPACE_ROOT",
        path.dirname(path.abspath(__file__)),
    )
)
_dispatch = app.build()


def main(request):
    return _dispatch(request)
```

`PYSPACE_ROOT` is optional. Without it, pyspace uses the directory containing `main.py`.

## Optional headers

```text
X-Pyspace-App             direct registered-app hint
X-Pyspace-Module          importable Python module for a cold miss
X-Pyspace-Gospace-Binary  gospace executable path for a cold miss
X-Pyspace-Control-Token   required only when the request performs a runtime load
```

The explicit `/_pyspace/...` control endpoints remain available for management, but normal lazy routing does not require a registration request first.
