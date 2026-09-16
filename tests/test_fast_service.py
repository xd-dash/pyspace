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


def make_request(path="/hello", headers=None):
    app = Flask(__name__)
    return app.test_request_context(path, headers=headers or {})


def test_gospace_only_path_does_not_initialize_python_capabilities():
    service = Service()
    backend = FakeGospace()
    service._gospace = backend

    with make_request():
        assert service.dispatch(request) == (
            b"go",
            200,
            [("Content-Type", "text/plain")],
        )

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
        headers = {
            MODULE_HINT_HEADER: module_name,
            CONTROL_TOKEN_HEADER: "secret",
        }
        with make_request(headers=headers):
            assert service.dispatch(request) == "python"

        assert service._has_python is True
        assert service.env is None
        assert backend.calls == 0

        # Once registered, the ordinary route lookup does not require the hint.
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
