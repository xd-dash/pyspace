"""Functions Framework composition shell for Python routers and embedded gospace."""
from __future__ import annotations

import importlib
import os
import secrets
from collections.abc import Mapping
from threading import Lock

from flask import current_app, jsonify
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .dynamic import load_source
from .native import GospaceNative
from .registry import ApplicationExists, ApplicationRegistry, UnknownApplication

CONTROL_PREFIX = "/_pyspace/"
CONTROL_TOKEN_HEADER = "X-Pyspace-Control-Token"
APP_HEADER = "X-Pyspace-App"
MODULE_HINT_HEADER = "X-Pyspace-Module"
PYTHON_SOURCE_HEADER = "X-Pyspace-Python-Source"
PYTHON_SHA256_HEADER = "X-Pyspace-Python-Sha256"
HEALTH_PATH = "/_pyspace/healthz"


class Service:
    """Static Flask catch-all; routing state lives entirely below Flask."""
    def __init__(self, *, root: str | None = None, router_env_var: str = "ROUTER_MODULE", control_token: str | None = None, gospace: GospaceNative | None = None, enable_gospace: bool | None = None) -> None:
        self.root = root
        self.router_env_var = router_env_var
        self.control_token = os.environ.get("PYSPACE_CONTROL_TOKEN") if control_token is None else control_token
        self.registry = ApplicationRegistry()
        self.app = None
        self.env: Environment | None = None
        self._python_routes: dict[str, tuple[str, ...]] = {}
        self._route_lock = Lock()
        self._gospace = gospace
        self._enable_gospace = (os.environ.get("PYSPACE_GOSPACE", "auto").lower() != "off") if enable_gospace is None else enable_gospace
        self._gospace_lock = Lock()

    def build_app(self):
        self.app = current_app
        return self.app

    def build_jinja_env(self) -> Environment:
        self.env = Environment(loader=FileSystemLoader(self.root or os.getcwd()), autoescape=select_autoescape(["html", "xml", "htm", ".xhtml", ".svg"]))
        return self.env

    def register_routes(self, name: str, routes: Mapping[str, object]) -> None:
        normalized = {}
        for rule, view in routes.items():
            if not isinstance(rule, str) or not rule.startswith("/"):
                raise ValueError(f"invalid route rule {rule!r}")
            if not callable(view):
                raise TypeError(f"view for {rule!r} is not callable")
            normalized[rule] = view
        frozen = dict(normalized)
        def handler(request):
            view = frozen.get(request.path)
            return ("Not Found", 404) if view is None else view()
        self.registry.register(name, handler)
        with self._route_lock:
            index = dict(self._python_routes)
            for rule in frozen:
                index[rule] = (*index.get(rule, ()), name)
            self._python_routes = index

    def register_module(self, name: str, module_name: str) -> None:
        routes = getattr(importlib.import_module(module_name), "ROUTES", None)
        if not isinstance(routes, Mapping):
            raise TypeError(f"module {module_name!r} must expose a ROUTES mapping")
        self.register_routes(name, routes)

    def register_source(self, name: str, source: bytes, *, sha256: str | None = None):
        app = load_source(name, source, expected_sha256=sha256)
        self.register_routes(name, app.routes)
        return app

    def activate(self, name: str) -> None:
        self.registry.activate(name)

    def _native(self) -> GospaceNative | None:
        if not self._enable_gospace:
            return None
        if self._gospace is not None:
            return self._gospace
        with self._gospace_lock:
            if self._gospace is None:
                try:
                    self._gospace = GospaceNative()
                except OSError:
                    if os.environ.get("PYSPACE_GOSPACE", "auto").lower() == "required":
                        raise
                    self._enable_gospace = False
        return self._gospace

    def compose_from_environment(self) -> None:
        module_name = os.environ.get(self.router_env_var)
        if module_name:
            name = os.environ.get("PYSPACE_ROUTER_NAME", module_name)
            self.register_module(name, module_name)
            if os.environ.get("PYSPACE_ROUTER_ACTIVATE", "true").lower() == "true":
                self.activate(name)

    def build(self):
        self.build_app(); self.build_jinja_env(); self.compose_from_environment()
        return self.dispatch

    def dispatch(self, request):
        if request.path == HEALTH_PATH:
            return "ok", 200
        if request.path.startswith(CONTROL_PREFIX):
            return self._control(request)
        requested = request.headers.get(APP_HEADER)
        if requested:
            try:
                return self.registry.dispatch(requested, request)
            except UnknownApplication:
                loaded = self._load_module_hint(requested, request)
                if loaded is not None:
                    return loaded
                return "unknown application", 404
        active = self.registry.active()
        owners = self._python_routes.get(request.path, ())
        if active in owners:
            return self.registry.dispatch(active, request)
        if owners:
            return self.registry.dispatch(owners[0], request)
        module_name = request.headers.get(MODULE_HINT_HEADER)
        if module_name:
            loaded = self._load_module_hint(module_name, request)
            if loaded is not None:
                return loaded
        native = self._native()
        return native.dispatch(request) if native is not None else ("Not Found", 404)

    def _load_module_hint(self, name: str, request):
        module_name = request.headers.get(MODULE_HINT_HEADER)
        if not module_name or not self._authorized(request):
            return None
        try:
            self.register_module(name, module_name)
        except ApplicationExists:
            pass
        except (ImportError, TypeError, ValueError) as exc:
            return str(exc), 400
        return self.registry.dispatch(name, request)

    def _authorized(self, request) -> bool:
        return bool(self.control_token) and secrets.compare_digest(request.headers.get(CONTROL_TOKEN_HEADER, ""), self.control_token)

    def _control(self, request):
        if not self._authorized(request):
            return "Not Found", 404
        if request.path == "/_pyspace/apps" and request.method == "GET":
            return jsonify({"active": self.registry.active(), "apps": list(self.registry.names()), "gospace": self._enable_gospace})
        prefix = "/_pyspace/activate/"
        if request.path.startswith(prefix) and request.method == "POST":
            name = request.path[len(prefix):]
            try: self.activate(name)
            except UnknownApplication: return "unknown application", 404
            return jsonify({"active": name})
        prefix = "/_pyspace/module/"
        if request.path.startswith(prefix) and request.method == "POST":
            name = request.path[len(prefix):]
            module_name = (request.get_json(silent=True) or {}).get("module")
            if not isinstance(module_name, str) or not module_name: return "module is required", 400
            try: self.register_module(name, module_name)
            except ApplicationExists: return "application already registered; use an immutable/versioned name", 409
            except (ImportError, TypeError, ValueError) as exc: return str(exc), 400
            return jsonify({"name": name, "module": module_name}), 201
        prefix = "/_pyspace/source/"
        if request.path.startswith(prefix) and request.method == "POST":
            name = request.path[len(prefix):]
            source = request.get_data(cache=False)
            if not source: return "Python source body is required", 400
            try: app = self.register_source(name, source, sha256=request.headers.get(PYTHON_SHA256_HEADER))
            except ApplicationExists: return "application already registered; use an immutable/versioned name", 409
            except (SyntaxError, TypeError, ValueError) as exc: return str(exc), 400
            return jsonify({"name": name, "identity": app.identity}), 201
        return "Not Found", 404


CloudFunctionApp = Service
