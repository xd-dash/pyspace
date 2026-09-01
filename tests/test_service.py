import sys
import types

from pyspace.service import (
    APP_HEADER,
    CONTROL_TOKEN_HEADER,
    HEALTH_PATH,
    MODULE_HINT_HEADER,
    Service,
)


class Request:
    def __init__(self, path: str, app: str | None = None, headers: dict | None = None):
        self.path = path
        self.headers = dict(headers or {})
        if app is not None:
            self.headers[APP_HEADER] = app


def test_shell_health_does_not_require_an_application():
    service = Service(control_token="test")
    assert service.registry.names() == ()
    assert service.dispatch(Request(HEALTH_PATH)) == ("ok", 200)


def test_registered_python_route_is_found_without_app_hint():
    module = types.ModuleType("test_router_discovered")
    module.ROUTES = {"/discovered": lambda: "discovered"}
    sys.modules[module.__name__] = module

    service = Service(control_token="test")
    service.register_module("discovered-v1", module.__name__)

    assert service.dispatch(Request("/discovered")) == "discovered"


def test_active_python_app_wins_when_two_apps_own_same_route():
    first = types.ModuleType("test_router_first")
    first.ROUTES = {"/value": lambda: "first"}
    second = types.ModuleType("test_router_second")
    second.ROUTES = {"/value": lambda: "second"}
    sys.modules[first.__name__] = first
    sys.modules[second.__name__] = second

    service = Service(control_token="test")
    service.register_module("first-v1", first.__name__)
    service.register_module("second-v1", second.__name__)
    service.activate("second-v1")

    assert service.dispatch(Request("/value")) == "second"
    assert service.dispatch(Request("/value", "first-v1")) == "first"


def test_missing_module_is_hotloaded_and_dispatched_by_same_request():
    module = types.ModuleType("test_router_hinted")
    module.ROUTES = {"/value": lambda: "hinted"}
    sys.modules[module.__name__] = module

    service = Service(control_token="test")
    request = Request(
        "/value",
        headers={
            MODULE_HINT_HEADER: module.__name__,
            CONTROL_TOKEN_HEADER: "test",
        },
    )

    # No X-Pyspace-App is required. The module name is the natural cache key.
    assert service.dispatch(request) == "hinted"
    assert service.registry.names() == (module.__name__,)

    # Once warm, ordinary route discovery finds it without any hint headers.
    assert service.dispatch(Request("/value")) == "hinted"


def test_loader_hint_requires_control_token_only_when_cold():
    module = types.ModuleType("test_router_unauthorized")
    module.ROUTES = {"/value": lambda: "nope"}
    sys.modules[module.__name__] = module

    service = Service(control_token="test")
    request = Request("/value", headers={MODULE_HINT_HEADER: module.__name__})

    assert service.dispatch(request) == ("Not Found", 404)
    assert service.registry.names() == ()
