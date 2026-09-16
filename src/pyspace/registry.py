"""Immutable-snapshot application registry for pyspace.

Registration is serialized and publishes a fresh mapping. Request dispatch only
reads an already-published snapshot, matching gospace's immutable registration
model without taking a Python lock on every warm route invocation.
"""
from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

AppHandler = Callable[[Any], Any]

class UnknownApplication(KeyError):
    pass

class ApplicationExists(ValueError):
    pass

class ApplicationRegistry:
    def __init__(self) -> None:
        self._write_lock = Lock()
        self._apps: dict[str, AppHandler] = {}
        self._active: str | None = None

    def register(self, name: str, handler: AppHandler) -> None:
        if not name:
            raise ValueError("application name is required")
        if not callable(handler):
            raise TypeError("application handler must be callable")
        with self._write_lock:
            current = self._apps
            if name in current:
                raise ApplicationExists(name)
            updated = current.copy()
            updated[name] = handler
            self._apps = updated

    def handler(self, name: str) -> AppHandler:
        snapshot = self._apps
        try:
            return snapshot[name]
        except KeyError as exc:
            raise UnknownApplication(name) from exc

    def activate(self, name: str) -> None:
        with self._write_lock:
            if name not in self._apps:
                raise UnknownApplication(name)
            self._active = name

    def active(self) -> str | None:
        return self._active

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._apps))

    def dispatch(self, name: str, request: Any) -> Any:
        return self.handler(name)(request)

    def dispatch_active(self, request: Any) -> Any:
        name = self._active
        if name is None:
            return "no application is active", 503
        return self.dispatch(name, request)
