from pyspace.service import APP_HEADER, CONTROL_TOKEN_HEADER, HEALTH_PATH, MODULE_HINT_HEADER, PYTHON_SHA256_HEADER, Service


class Headers(dict):
    pass


class Request:
    def __init__(self, path, *, method="GET", headers=None, body=b"", app=None):
        self.path = path; self.method = method; self.headers = Headers(headers or {}); self._body = body
        if app: self.headers[APP_HEADER] = app
        self.full_path = path
    def get_data(self, cache=True): return self._body
    def get_json(self, silent=True): return None


class Native:
    def __init__(self): self.calls = 0
    def dispatch(self, request): self.calls += 1; return "native"


def test_health_never_enters_native():
    native = Native(); service = Service(control_token="test", gospace=native)
    assert service.dispatch(Request(HEALTH_PATH)) == ("ok", 200)
    assert native.calls == 0


def test_python_route_precedes_native():
    native = Native(); service = Service(control_token="test", gospace=native)
    service.register_routes("py-v1", {"/value": lambda: "python"})
    assert service.dispatch(Request("/value")) == "python"
    assert native.calls == 0
    assert service.dispatch(Request("/other")) == "native"


def test_importable_module_hint_loads_on_cold_request(monkeypatch):
    import sys, types
    module = types.ModuleType("hinted_router"); module.ROUTES = {"/value": lambda: "hinted"}; sys.modules[module.__name__] = module
    service = Service(control_token="test", enable_gospace=False)
    request = Request("/value", headers={MODULE_HINT_HEADER: module.__name__, CONTROL_TOKEN_HEADER: "test"})
    assert service.dispatch(request) == "hinted"
    assert service.dispatch(Request("/value")) == "hinted"


def test_dynamic_source_registers_without_flask_mutation():
    service = Service(control_token="test", enable_gospace=False)
    source = b"def value():\n    return 'dynamic'\nROUTES={'/value': value}\n"
    app = service.register_source("dynamic-v1", source)
    assert app.identity.startswith("sha256:")
    assert service.dispatch(Request("/value")) == "dynamic"
