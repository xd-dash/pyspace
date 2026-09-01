import os
from os import path

from pyspace import Service

# Default to the composed deployment root while allowing a deployment to
# explicitly relocate pyspace's application/template root.
app = Service(
    root=os.environ.get(
        "PYSPACE_ROOT",
        path.dirname(path.abspath(__file__)),
    )
)
_dispatch = app.build()


def main(request):
    """Google Functions Framework requires the exported target to be a function."""
    return _dispatch(request)
