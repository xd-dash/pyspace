import hashlib

import pytest

from pyspace.dynamic import load_source


def test_load_source_exposes_routes_and_identity():
    source = b"def hello():\n    return 'ok'\nROUTES = {'/hello': hello}\n"
    app = load_source("hello-v1", source)
    assert app.identity == "sha256:" + hashlib.sha256(source).hexdigest()
    assert app.routes["/hello"]() == "ok"


def test_load_source_checks_digest_before_execution():
    with pytest.raises(ValueError, match="sha256 mismatch"):
        load_source("bad", b"raise RuntimeError('must not execute')", expected_sha256="0" * 64)
