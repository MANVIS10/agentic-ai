"""The port is faithful iff the new app exposes exactly the old app's HTTP
contract. Compares the two OpenAPI schemas route-by-route and model-by-model."""
import importlib.util
import sys
from pathlib import Path

import pytest

OLD = Path("stage25_react_ui/backend/main.py")


def _load_original():
    sys.path.insert(0, str(OLD.parent))
    spec = importlib.util.spec_from_file_location("stage25_main", OLD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.app


@pytest.fixture(scope="module")
def schemas(pg_available):
    from app.main import app as new_app

    return _load_original().openapi(), new_app.openapi()


def test_same_routes_and_methods(schemas):
    old, new = schemas
    old_routes = {(p, m) for p, ops in old["paths"].items() for m in ops}
    new_routes = {(p, m) for p, ops in new["paths"].items() for m in ops}
    assert new_routes == old_routes


def test_same_response_models(schemas):
    old, new = schemas
    assert new["components"]["schemas"] == old["components"]["schemas"]
