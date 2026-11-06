"""Routes that return a ``typing.TypedDict`` must opt out of FastAPI response models.

Pydantic rejects ``typing.TypedDict`` on Python below 3.12, and FastAPI turns a return annotation
into a response model, so the whole app fails to import on 3.10 and 3.11.  Those versions are
supported and run in CI, so the check cannot rely on the interpreter running the tests.
"""

import typing

from fastapi.routing import APIRoute

from reacher.api.app import create_app


def test_typed_dict_returns_opt_out_of_response_models():
    """Every route annotated with a TypedDict return sets ``response_model=None``."""
    offenders = []
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        returned = typing.get_type_hints(route.endpoint).get("return")
        if returned is not None and typing.is_typeddict(returned) and route.response_model is not None:
            offenders.append(f"{route.path} -> {returned.__name__}")
    assert not offenders, f"TypedDict response models break import on Python < 3.12: {offenders}"
