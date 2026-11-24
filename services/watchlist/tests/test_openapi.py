"""openapi.yaml must describe this service as it really is.

Three things keep it honest:

- The list of routes in the code and the list of paths in the document must
  be the same, in both directions.
- The app fixture compares every answer in every API test with the document
  (tests/openapi_check.py), so a status code or a field that is not written
  down fails the test that saw it.
- The tests at the bottom prove that the comparison itself notices mistakes.
"""

import pytest

from openapi_check import DOCUMENT, check_answer, documented_routes, fits, routes_of


def answer_schema(path, method="get", status="200"):
    return DOCUMENT["paths"][path][method]["responses"][status]["content"]["application/json"]["schema"]


def test_every_route_is_documented_and_every_documented_path_is_a_route(app):
    assert routes_of(app) == documented_routes()


def test_every_endpoint_says_what_an_unlisted_error_looks_like():
    for path, operations in DOCUMENT["paths"].items():
        for method, operation in operations.items():
            assert "default" in operation["responses"], f"{method.upper()} {path}"


def test_health_is_documented_with_both_answers():
    health = DOCUMENT["paths"]["/health"]["get"]
    assert {"200", "503"} <= set(health["responses"])
    assert "security" not in health  # it needs no token
    for status, state in (("200", "ok"), ("503", "error")):
        body = {"status": state, "db": state, "version": "abc1234"}
        assert check_answer("GET", "/health", int(status), body) == []


def test_every_endpoint_except_health_and_sign_in_needs_a_token():
    open_paths = {"/health", "/auth/register", "/auth/login"}
    for path, operations in DOCUMENT["paths"].items():
        for method, operation in operations.items():
            needs_token = "security" in operation
            assert needs_token == (path not in open_paths), f"{method.upper()} {path}"
            if needs_token:
                assert "401" in operation["responses"], f"{method.upper()} {path}"


def test_the_seam_endpoint_only_opens_for_the_service_token():
    snapshot = DOCUMENT["paths"]["/internal/snapshot"]["get"]
    assert snapshot["security"] == [{"serviceToken": []}]


# --- The comparison itself ---------------------------------------------------

ITEM = {"$ref": "#/components/schemas/Item"}


def test_a_correct_answer_fits():
    assert fits(ITEM, dict(GOOD)) == []


@pytest.mark.parametrize(
    "change, complaint",
    [
        (lambda body: body.pop("ticker"), "body.ticker is missing"),
        (lambda body: body.update(id="7"), "body.id should be integer, got '7'"),
        (lambda body: body.update(id=True), "body.id should be integer, got True"),
        (lambda body: body.update(ticker=None), "body.ticker is null, which is not allowed"),
        (lambda body: body.update(owner_id=3), "body.owner_id is not in the document"),
    ],
)
def test_a_wrong_answer_is_noticed(change, complaint):
    body = dict(GOOD)
    change(body)
    assert fits(ITEM, body) == [complaint]


def test_an_undocumented_status_code_is_noticed():
    problems = check_answer("GET", "/health", 418, {"error": "teapot"})
    assert problems == ["GET /health -> 418: this status code is not in the document"]


def test_an_undocumented_endpoint_is_noticed():
    problems = check_answer("GET", "/secret", 200, {})
    assert problems == ["GET /secret -> 200: the endpoint is not in the document"]


def test_a_general_error_is_accepted_only_in_the_error_shape():
    assert check_answer("GET", "/health", 500, {"error": "Something went wrong."}) == []
    assert check_answer("GET", "/health", 500, {"oops": 1}) != []


def test_a_body_where_the_document_says_none_is_noticed():
    assert check_answer("DELETE", "/watchlist/{ticker}", 204, None) == []
    assert check_answer("DELETE", "/watchlist/{ticker}", 204, {"deleted": True}) != []


GOOD = {"id": 7, "ticker": "AAPL", "name": "Apple", "notes": "", "added_at": "2026-11-24T10:15:00+00:00"}
