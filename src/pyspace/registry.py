"""Thread-safe immutable application registry for pyspace.

The registry mirrors gospace's distinction between registration and activation:
registration publishes a named immutable application handler, while activation
only chooses the default application for requests that do not explicitly name
one.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Any


AppHandler = Callable[[Any], Any]


class UnknownApplication(KeyError):
    pass


class ApplicationExists(ValueError):
    pass


class ApplicationRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._apps: dict[str, AppHandler] = {}
        self._active: str | None = None

    def register(self, name: str, handler: AppHandler) -> None:
        if not name:
            raise ValueError("application name is required")
        if not callable(handler):
            raise TypeError("application handler must be callable")

        with self._lock:
            if name in self._apps:
                raise ApplicationExists(name)
            self._apps[name] = handler

    def handler(self, name: str) -> AppHandler:
        with self._lock:
            try:
                return self._apps[name]
            except KeyError as exc:
                raise UnknownApplication(name) from exc

    def activate(self, name: str) -> None:
        # Validate existence before publishing the new default.
        self.handler(name)
        with self._lock:
            self._active = name

    def active(self) -> str | None:
        with self._lock:
            return self._active

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._apps))

    def dispatch(self, name: str, request: Any) -> Any:
        return self.handler(name)(request)

    def dispatch_active(self, request: Any) -> Any:
        name = self.active()
        if name is None:
            return "no application is active", 503
        return self.dispatch(name, request)
