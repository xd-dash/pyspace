from pyspace.registry import ApplicationRegistry


class Request:
    def __init__(self, path: str):
        self.path = path


def test_named_dispatch_does_not_change_active_application():
    registry = ApplicationRegistry()
    registry.register("a", lambda request: "a")
    registry.register("b", lambda request: "b")
    registry.activate("a")

    assert registry.dispatch("b", Request("/")) == "b"
    assert registry.active() == "a"
    assert registry.dispatch_active(Request("/")) == "a"


def test_registry_starts_without_an_application():
    registry = ApplicationRegistry()
    assert registry.names() == ()
    assert registry.active() is None
    assert registry.dispatch_active(Request("/")) == ("no application is active", 503)
