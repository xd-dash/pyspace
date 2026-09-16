"""Low-overhead Gen 1 host: embedded gospace first, Python routers lazy."""
from __future__ import annotations

import importlib
import os
import secrets
from collections.abc import Mapping

from flask import current_app, jsonify

from .embedded import EmbeddedGospaceBackend
from .registry import ApplicationExists, ApplicationRegistry, UnknownApplication

CONTROL_PREFIX = "/_pyspace/"
CONTROL_TOKEN_HEADER = "X-Pyspace-Control-Token"
APP_HEADER = "X-Pyspace-App"
MODULE_HINT_HEADER = "X-Pyspace-Module"
HEALTH_PATH = "/_pyspace/healthz"
PYTHON_SOURCE_PREFIX = "/_pyspace/python/"


class Service:
    """Stable Functions Framework target with an in-process gospace fast path.

    Python router support is capability-lazy: deployments with no ROUTER_MODULE
    import no customer Python router and construct no Jinja environment. A
    router may still be loaded later through authenticated control requests.
    Dynamic source loading deliberately imports its loader only on that cold
    control path, so gospace-only requests do not pay for it.
    """

    def __init__(self, *, root=None, router_env_var="ROUTER_MODULE", control_token=None, **_compat):
        self.root = root
        self.router_env_var = router_env_var
        self.control_token = os.environ.get("PYSPACE_CONTROL_TOKEN") if control_token is None else control_token
        self.registry = ApplicationRegistry()
        self.app = None
        self.env = None
        self._python_routes: dict[str, list[str]] = {}
        self._has_python = False
        self._gospace = None
        self._python_modules: dict[str, object] = {}

    def build_app(self):
        self.app = current_app
        return self.app

    def build_jinja_env(self):
        from jinja2 import Environment, FileSystemLoader, select_autoescape
        root = self.root or os.getcwd()
        self.env = Environment(loader=FileSystemLoader(root), autoescape=select_autoescape(["html", "xml", "htm", ".xhtml", ".svg"]))
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
        for rule in frozen:
            self._python_routes.setdefault(rule, []).append(name)
        self._has_python = True

    def register_module(self, name: str, module_name: str) -> None:
        module = importlib.import_module(module_name)
        routes = getattr(module, "ROUTES", None)
        if not isinstance(routes, Mapping):
            raise TypeError(f"module {module_name!r} must expose a ROUTES mapping")
        self.register_routes(name, routes)
        self._python_modules[name] = module

    def register_source(self, name: str, source: bytes, *, filename: str | None = None) -> None:
        # Cold capability path: keep compile/ModuleType/sys.modules machinery out
        # of module import and out of gospace-only request handling.
        from .dynamic import load_source
        loaded = load_source(name, source, filename=filename)
        try:
            self.register_routes(name, loaded.routes)
        except BaseException:
            import sys
            sys.modules.pop(loaded.module_name, None)
            raise
        self._python_modules[name] = loaded.module

    def compose_from_environment(self) -> None:
        library = os.environ.get("PYSPACE_GOSPACE_LIBRARY")
        if library:
            self._gospace = EmbeddedGospaceBackend(library)

        module_name = os.environ.get(self.router_env_var)
        if module_name:
            name = os.environ.get("PYSPACE_ROUTER_NAME", module_name)
            self.register_module(name, module_name)
            if os.environ.get("PYSPACE_ROUTER_ACTIVATE", "true").lower() == "true":
                self.registry.activate(name)

    def build(self):
        self.build_app()
        self.compose_from_environment()
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
                loaded = self._load_python_hint(requested, request)
                if loaded is not None:
                    return loaded
                return "unknown application", 404

        if self._gospace is not None and not self._has_python:
            if not request.headers.get(MODULE_HINT_HEADER):
                return self._gospace(request)

        active = self.registry.active()
        owners = self._python_routes.get(request.path, ())
        if active in owners:
            return self.registry.dispatch(active, request)
        if owners:
            return self.registry.dispatch(owners[0], request)

        module_hint = request.headers.get(MODULE_HINT_HEADER)
        if module_hint:
            loaded = self._load_python_hint(module_hint, request)
            if loaded is not None:
                return loaded

        if self._gospace is not None:
            return self._gospace(request)
        return "Not Found", 404

    def _load_python_hint(self, name: str, request):
        module_name = request.headers.get(MODULE_HINT_HEADER)
        if not module_name:
            return None
        if not self._authorized(request):
            return "Not Found", 404
        try:
            self.register_module(name, module_name)
        except ApplicationExists:
            pass
        except (ImportError, TypeError, ValueError) as exc:
            return str(exc), 400
        try:
            return self.registry.dispatch(name, request)
        except UnknownApplication:
            return "unknown application", 404

    def _authorized(self, request) -> bool:
        if not self.control_token:
            return False
        return secrets.compare_digest(request.headers.get(CONTROL_TOKEN_HEADER, ""), self.control_token)

    def _control(self, request):
        if not self._authorized(request):
            return "Not Found", 404
        if request.path == "/_pyspace/apps" and request.method == "GET":
            return jsonify({"active": self.registry.active(), "apps": list(self.registry.names()), "gospace": self._gospace is not None})

        if request.path.startswith(PYTHON_SOURCE_PREFIX) and request.method == "POST":
            name = request.path[len(PYTHON_SOURCE_PREFIX):]
            if not name or "/" in name:
                return "invalid application name", 400
            try:
                self.register_source(name, request.get_data(cache=False), filename=f"pyspace://{name}/router.py")
            except ApplicationExists:
                return "application already registered; use a versioned name", 409
            except (SyntaxError, TypeError, ValueError, ImportError) as exc:
                return str(exc), 400
            if request.args.get("activate", "").lower() == "true":
                self.registry.activate(name)
            return jsonify({"name": name, "source": "python", "active": self.registry.active() == name}), 201

        prefix = "/_pyspace/module/"
        if request.path.startswith(prefix) and request.method == "POST":
            name = request.path[len(prefix):]
            body = request.get_json(silent=True) or {}
            module_name = body.get("module")
            if not isinstance(module_name, str) or not module_name:
                return "module is required", 400
            try:
                self.register_module(name, module_name)
            except ApplicationExists:
                return "application already registered; use an immutable/versioned name", 409
            except (ImportError, TypeError, ValueError) as exc:
                return str(exc), 400
            if body.get("activate") is True:
                self.registry.activate(name)
            return jsonify({"name": name, "module": module_name, "active": self.registry.active() == name}), 201
        return "Not Found", 404


CloudFunctionApp = Service
