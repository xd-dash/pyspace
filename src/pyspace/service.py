"""Functions Framework host and runtime composition surface for pyspace."""

from __future__ import annotations

import importlib
import os
import secrets
from collections.abc import Mapping

from flask import current_app, jsonify
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .gospace import GospaceBackend, GospaceConfig
from .registry import ApplicationExists, ApplicationRegistry, UnknownApplication
from .supervisor import ProcessSupervisor


CONTROL_PREFIX = "/_pyspace/"
CONTROL_TOKEN_HEADER = "X-Pyspace-Control-Token"
APP_HEADER = "X-Pyspace-App"
MODULE_HINT_HEADER = "X-Pyspace-Module"
GOSPACE_HINT_HEADER = "X-Pyspace-Gospace-Binary"
HEALTH_PATH = "/_pyspace/healthz"


class Service:
    """Stable Gen 1 HTTP entry point with catchall runtime composition."""

    def __init__(
        self,
        *,
        root: str | None = None,
        router_env_var: str = "ROUTER_MODULE",
        control_token: str | None = None,
        supervisor: ProcessSupervisor | None = None,
    ) -> None:
        self.root = root
        self.router_env_var = router_env_var
        self.control_token = (
            os.environ.get("PYSPACE_CONTROL_TOKEN")
            if control_token is None
            else control_token
        )
        self.registry = ApplicationRegistry()
        self.supervisor = supervisor or ProcessSupervisor()
        self.app = None
        self.env: Environment | None = None
        self._python_routes: dict[str, list[str]] = {}
        self._gospace_apps: list[str] = []

    def build_app(self):
        self.app = current_app
        return self.app

    def build_jinja_env(self) -> Environment:
        root = self.root or os.getcwd()
        self.env = Environment(
            loader=FileSystemLoader(root),
            autoescape=select_autoescape(["html", "xml", "htm", ".xhtml", ".svg"]),
        )
        return self.env

    def register_routes(self, name: str, routes: Mapping[str, object]) -> None:
        normalized: dict[str, object] = {}
        for rule, view in routes.items():
            if not isinstance(rule, str) or not rule.startswith("/"):
                raise ValueError(f"invalid route rule {rule!r}")
            if not callable(view):
                raise TypeError(f"view for {rule!r} is not callable")
            normalized[rule] = view

        frozen_routes = dict(normalized)

        def handler(request):
            view = frozen_routes.get(request.path)
            if view is None:
                return "Not Found", 404
            return view()

        self.registry.register(name, handler)
        for rule in frozen_routes:
            self._python_routes.setdefault(rule, []).append(name)

    def register_module(self, name: str, module_name: str) -> None:
        module = importlib.import_module(module_name)
        routes = getattr(module, "ROUTES", None)
        if routes is None:
            raise ValueError(f"module {module_name!r} does not expose ROUTES")
        if not isinstance(routes, Mapping):
            raise TypeError("ROUTES must be a mapping of path to view callable")
        self.register_routes(name, routes)

    def register_gospace(
        self,
        name: str,
        binary: str,
        *,
        socket_dir: str = "/tmp/pyspace",
        request_timeout: float = 10.0,
        ready_timeout: float = 5.0,
    ) -> GospaceConfig:
        config = GospaceConfig.from_binary(
            binary,
            name=name,
            socket_dir=socket_dir,
            request_timeout=request_timeout,
            ready_timeout=ready_timeout,
        )
        self.registry.register(name, GospaceBackend(self.supervisor, name, config))
        self._gospace_apps.append(name)
        return config

    def activate(self, name: str) -> None:
        self.registry.activate(name)

    def compose_from_environment(self) -> None:
        module_name = os.environ.get(self.router_env_var)
        if module_name:
            name = os.environ.get("PYSPACE_ROUTER_NAME", module_name)
            self.register_module(name, module_name)
            if os.environ.get("PYSPACE_ROUTER_ACTIVATE", "true").lower() == "true":
                self.activate(name)

        gospace_binary = os.environ.get("PYSPACE_GOSPACE_BINARY")
        if gospace_binary:
            name = os.environ.get("PYSPACE_GOSPACE_NAME", "gospace")
            self.register_gospace(
                name,
                gospace_binary,
                socket_dir=os.environ.get("PYSPACE_SOCKET_DIR", "/tmp/pyspace"),
                request_timeout=float(os.environ.get("PYSPACE_GOSPACE_REQUEST_TIMEOUT", "10")),
                ready_timeout=float(os.environ.get("PYSPACE_GOSPACE_READY_TIMEOUT", "5")),
            )
            if os.environ.get("PYSPACE_GOSPACE_ACTIVATE", "false").lower() == "true":
                self.activate(name)

    def build(self):
        self.build_app()
        self.build_jinja_env()
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
                loaded = self._load_from_hint(requested, request)
                if loaded is not None:
                    return loaded
                return "unknown application", 404

        active = self.registry.active()
        owners = self._python_routes.get(request.path, ())
        if active in owners:
            return self.registry.dispatch(active, request)
        for owner in owners:
            return self.registry.dispatch(owner, request)

        ordered_gospace = list(self._ordered_gospace_apps(active))
        if len(ordered_gospace) == 1:
            # There is no ambiguity at the pyspace layer, so forward once and
            # let gospace's own non-executing route index decide whether it owns
            # the request. This avoids a resolver+request double UDS round trip.
            backend = self.registry.handler(ordered_gospace[0])
            if isinstance(backend, GospaceBackend):
                return backend(request)
        else:
            # With multiple gospace applications, resolve ownership first so
            # only one application process ever executes the request.
            for name in ordered_gospace:
                backend = self.registry.handler(name)
                if isinstance(backend, GospaceBackend) and backend.matches(request):
                    return backend(request)

        module_hint = request.headers.get(MODULE_HINT_HEADER)
        gospace_hint = request.headers.get(GOSPACE_HINT_HEADER)
        if module_hint or gospace_hint:
            inferred = module_hint or "gospace"
            loaded = self._load_from_hint(inferred, request)
            if loaded is not None:
                return loaded

        return "Not Found", 404

    def _ordered_gospace_apps(self, active: str | None):
        if active in self._gospace_apps:
            yield active
        for name in self._gospace_apps:
            if name != active:
                yield name

    def _load_from_hint(self, name: str, request):
        """Load a missing application from the same request and dispatch it."""
        module_name = request.headers.get(MODULE_HINT_HEADER)
        gospace_binary = request.headers.get(GOSPACE_HINT_HEADER)
        if bool(module_name) == bool(gospace_binary):
            return None if not module_name and not gospace_binary else ("exactly one pyspace loader hint is allowed", 400)
        if not self._authorized(request):
            return "Not Found", 404

        try:
            if module_name:
                self.register_module(name, module_name)
            else:
                self.register_gospace(
                    name,
                    gospace_binary,
                    socket_dir=os.environ.get("PYSPACE_SOCKET_DIR", "/tmp/pyspace"),
                    request_timeout=float(os.environ.get("PYSPACE_GOSPACE_REQUEST_TIMEOUT", "10")),
                    ready_timeout=float(os.environ.get("PYSPACE_GOSPACE_READY_TIMEOUT", "5")),
                )
        except ApplicationExists:
            pass
        except (ImportError, OSError, TypeError, ValueError) as exc:
            return str(exc), 400

        try:
            return self.registry.dispatch(name, request)
        except UnknownApplication:
            return "unknown application", 404

    def _authorized(self, request) -> bool:
        if not self.control_token:
            return False
        provided = request.headers.get(CONTROL_TOKEN_HEADER, "")
        return secrets.compare_digest(provided, self.control_token)

    def _control(self, request):
        if not self._authorized(request):
            return "Not Found", 404

        if request.path == "/_pyspace/apps" and request.method == "GET":
            return jsonify({"active": self.registry.active(), "apps": list(self.registry.names())})

        prefix = "/_pyspace/activate/"
        if request.path.startswith(prefix) and request.method == "POST":
            name = request.path[len(prefix) :]
            try:
                self.activate(name)
            except UnknownApplication:
                return "unknown application", 404
            return jsonify({"active": name})

        prefix = "/_pyspace/module/"
        if request.path.startswith(prefix) and request.method == "POST":
            name = request.path[len(prefix) :]
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
                self.activate(name)
            return jsonify({"name": name, "module": module_name, "active": self.registry.active() == name}), 201

        prefix = "/_pyspace/gospace/"
        if request.path.startswith(prefix) and request.method == "POST":
            name = request.path[len(prefix) :]
            body = request.get_json(silent=True) or {}
            binary = body.get("binary")
            if not isinstance(binary, str) or not binary:
                return "binary is required", 400
            try:
                config = self.register_gospace(
                    name,
                    binary,
                    socket_dir=str(body.get("socket_dir", "/tmp/pyspace")),
                    request_timeout=float(body.get("request_timeout", 10.0)),
                    ready_timeout=float(body.get("ready_timeout", 5.0)),
                )
            except ApplicationExists:
                return "application already registered; use an immutable/versioned name", 409
            except (OSError, TypeError, ValueError) as exc:
                return str(exc), 400
            if body.get("activate") is True:
                self.activate(name)
            return jsonify({"name": name, "binary": binary, "sha256": config.binary_sha256, "active": self.registry.active() == name}), 201

        return "Not Found", 404


CloudFunctionApp = Service
