"""Public service surface backed by the low-overhead embedded host."""
from .fast_service import (
    APP_HEADER,
    CONTROL_PREFIX,
    CONTROL_TOKEN_HEADER,
    HEALTH_PATH,
    MODULE_HINT_HEADER,
    Service as _EmbeddedService,
)


class Service(_EmbeddedService):
    def activate(self, name: str) -> None:
        self.registry.activate(name)


CloudFunctionApp = Service
