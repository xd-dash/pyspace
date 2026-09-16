from .registry import ApplicationExists, ApplicationRegistry, UnknownApplication
from .service import CloudFunctionApp, Service

__all__ = [
    "ApplicationExists", "ApplicationRegistry", "CloudFunctionApp", "Service", "UnknownApplication",
    "GospaceBackend", "GospaceConfig", "ProcessHandle", "ProcessSpec", "ProcessSupervisor",
]


def __getattr__(name):
    # Legacy subprocess types remain available without importing subprocess,
    # http.client, or supervisor machinery on the embedded fast path.
    if name in {"GospaceBackend", "GospaceConfig"}:
        from .gospace import GospaceBackend, GospaceConfig
        return {"GospaceBackend": GospaceBackend, "GospaceConfig": GospaceConfig}[name]
    if name in {"ProcessHandle", "ProcessSpec", "ProcessSupervisor"}:
        from .supervisor import ProcessHandle, ProcessSpec, ProcessSupervisor
        return {"ProcessHandle": ProcessHandle, "ProcessSpec": ProcessSpec, "ProcessSupervisor": ProcessSupervisor}[name]
    raise AttributeError(name)
