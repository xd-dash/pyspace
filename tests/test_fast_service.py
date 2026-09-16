import sys
import types

from flask import Flask, request

from pyspace.fast_service import (
    CONTROL_TOKEN_HEADER,
    MODULE_HINT_HEADER,
    Service,
)


class FakeGospace:
    def __init__(self):
        self.calls = 0

    def __call__(self, req):
        self.calls += 1
        return b"go", 200, [("Content-Type", "text/plain")]


def make_request(path="/hello", headers=None, method="GET", data=None, query_string=None):
    app = Flask(__name__)
    return app.test_request_context(path, headers=headers or {}, method=method, data=data, query_string=query_string)


def test_gospace_only_path_does_not_initialize_python_capabilities():
    service = Service()
    backend = FakeGospace()
    service._gospace = backend

    with make_request():
        assert service.dispatch(request) == (b"go", 200, [("Content-Type", "text/plain")])

    assert backend.calls == 1
    assert service.env is None
    assert service._has_python is False
    assert service.registry.names() == ()


def test_python_router_can_be_loaded_lazily_without_changing_deployment_shape():
    module_name = "_pyspace_test_router"
    module = types.ModuleType(module_name)
    module.ROUTES = {"/hello": lambda: "python"}
    sys.modules[module_name] = module
    try:
        service = Service(control_token="secret")
        backend = FakeGospace()
        service._gospace = backend
        headers = {MODULE_HINT_HEADER: module_name, CONTROL_TOKEN_HEADER: "secret"}
        with make_request(headers=headers):
            assert service.dispatch(request) == "python"

        assert service._has_python is True
        assert service.env is None
        assert backend.calls == 0

        with make_request():
            assert service.dispatch(request) == "python"
    finally:
        sys.modules.pop(module_name, None)


def test_unauthorized_python_hint_falls_through_without_importing():
    service = Service(control_token="secret")
    backend = FakeGospace()
    service._gospace = backend

    with make_request(headers={MODULE_HINT_HEADER: "does_not_exist"}):
        assert service.dispatch(request) == ("Not Found", 404)

    assert service._has_python is False
    assert backend.calls == 0


def test_dynamic_python_source_uses_minimal_routes_contract_and_becomes_hot():
    service = Service(control_token="secret")
    backend = FakeGospace()
    service._gospace = backend
    source = b'''from flask import request\n\ndef hello():\n    return {"hello": request.args.get("name", "world")}\n\nROUTES = {"/hello": hello}\n'''
    headers = {CONTROL_TOKEN_HEADER: "secret", "Content-Type": "text/x-python"}

    with make_request("/_pyspace/python/foo-v1", headers=headers, method="POST", data=source, query_string={"activate": "true"}):
        response, status = service.dispatch(request)
        assert status == 201
        assert response.get_json()["name"] == "foo-v1"

    assert service._has_python is True
    assert service.env is None
    assert backend.calls == 0

    with make_request("/hello", query_string={"name": "warren"}):
        assert service.dispatch(request) == {"hello": "warren"}
    assert backend.calls == 0


def test_dynamic_python_source_is_not_imported_before_control_path_is_used():
    sys.modules.pop("pyspace.dynamic", None)
    service = Service()
    backend = FakeGospace()
    service._gospace = backend

    with make_request():
        service.dispatch(request)

    assert "pyspace.dynamic" not in sys.modules


def test_failed_dynamic_source_does_not_publish_application():
    service = Service(control_token="secret")
    headers = {CONTROL_TOKEN_HEADER: "secret", "Content-Type": "text/x-python"}
    source = b'ROUTES = {"/hello": 42}\n'

    with make_request("/_pyspace/python/bad-v1", headers=headers, method="POST", data=source):
        _, status = service.dispatch(request)
        assert status == 400

    assert service.registry.names() == ()
    assert service._has_python is False
