"""The port is faithful iff the new app exposes exactly the old app's HTTP
contract. Compares the two OpenAPI schemas route-by-route and model-by-model."""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
OLD = REPO_ROOT / "stages" / "stage25_react_ui" / "backend" / "main.py"


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


def test_no_original_route_was_removed(schemas):
    """Phase 1 asserted the two route sets were EQUAL, which was exactly
    right when the only goal was proving a port changed nothing.

    Adding authentication adds POST /auth/token, so equality now fails for a
    route that no stage 25 client ever called. The property still worth
    enforcing - and still enforced here - is that no original route
    disappeared or lost a method.
    """
    old, new = schemas
    old_routes = {(p, m) for p, ops in old["paths"].items() for m in ops}
    new_routes = {(p, m) for p, ops in new["paths"].items() for m in ops}
    assert old_routes <= new_routes, f"routes removed: {old_routes - new_routes}"


def _allowed_values(spec: dict) -> set | None:
    """The set of values a property accepts, or None if it isn't constrained.

    Pydantic emits `const` for a single-value Literal and `enum` for a
    multi-value one, so a widening changes the key too.
    """
    if "enum" in spec:
        return set(spec["enum"])
    if "const" in spec:
        return {spec["const"]}
    return None


def test_no_response_model_was_removed_or_narrowed(schemas):
    """Phase 1 asserted byte-equality here, which was exactly right when the
    only goal was proving a port changed nothing.

    Phase 3A deliberately makes ADDITIVE changes (SubtaskTrace.status gains
    "needs_review"), so byte-equality now fails for a change that cannot
    break a client. What still matters - and is still fully enforced below -
    is that nothing was REMOVED or NARROWED: every original model still
    exists, every original property still exists, and every original enum
    value is still accepted. A client written against stage 25 keeps working.
    """
    old, new = schemas
    old_models, new_models = old["components"]["schemas"], new["components"]["schemas"]

    assert set(old_models) <= set(new_models), (
        f"models removed: {set(old_models) - set(new_models)}"
    )

    for name, old_model in old_models.items():
        new_model = new_models[name]
        old_props = old_model.get("properties", {})
        new_props = new_model.get("properties", {})
        assert set(old_props) <= set(new_props), (
            f"{name}: properties removed: {set(old_props) - set(new_props)}"
        )
        for prop, old_spec in old_props.items():
            new_spec = new_props[prop]
            old_allowed, new_allowed = _allowed_values(old_spec), _allowed_values(new_spec)
            if old_allowed is not None:
                # A one-value Literal serializes as `const`, a multi-value one
                # as `enum`, so widening changes the KEY as well as the values -
                # compare normalized value sets, not raw specs.
                assert new_allowed is not None and old_allowed <= new_allowed, (
                    f"{name}.{prop}: allowed values narrowed "
                    f"{old_allowed} -> {new_allowed} - this breaks existing clients"
                )
            elif old_spec != new_spec:
                raise AssertionError(f"{name}.{prop} changed: {old_spec} -> {new_spec}")

        # A field that was optional must not become required.
        assert set(new_model.get("required", [])) <= set(old_model.get("required", [])), (
            f"{name}: new required fields would reject existing valid requests"
        )
