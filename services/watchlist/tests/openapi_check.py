"""Compares what the service really answers with what openapi.yaml says.

watch(app) hooks into a Flask app and checks every answer it gives: the status
code must be listed for that endpoint and the JSON must have the documented
shape. The app fixture in conftest.py uses it, so every API test also proves
the document. A test that sees an undocumented answer fails.

Only the parts of OpenAPI this document uses are understood. Not a test file.
"""

from pathlib import Path

import yaml
from flask import request

DOCUMENT_PATH = Path(__file__).resolve().parent.parent / "openapi.yaml"
DOCUMENT = yaml.safe_load(DOCUMENT_PATH.read_text())

METHODS = ("get", "post", "put", "patch", "delete")
# Any endpoint can give these, and the document covers them with "default":
# 413 is a body over 16 KB and 500 is a fault on our side.
GENERAL_ERRORS = (413, 500)

PYTHON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
}


def resolve(node: dict, document: dict = DOCUMENT) -> dict:
    """Follow {"$ref": "#/components/schemas/Item"} to what it points at."""
    while "$ref" in node:
        target = document
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def fits(schema: dict, value, where: str = "body", document: dict = DOCUMENT) -> list[str]:
    """Every way value differs from schema, as sentences. An empty list means it fits."""
    schema = resolve(schema, document)
    if value is None:
        return [] if schema.get("nullable") else [f"{where} is null, which is not allowed"]
    problems = []
    for part in schema.get("allOf", []):
        problems += fits(part, value, where, document)
    kind = schema.get("type")
    if kind:
        # In Python True is also an int, but in JSON true is not a number.
        wrong_bool = isinstance(value, bool) and kind != "boolean"
        if wrong_bool or not isinstance(value, PYTHON_TYPES[kind]):
            return problems + [f"{where} should be {kind}, got {value!r}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{where} is {value!r}, not one of {schema['enum']}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{where}.{name} is missing")
        for name, item in value.items():
            if name in properties:
                problems += fits(properties[name], item, f"{where}.{name}", document)
            elif isinstance(extra, dict):
                problems += fits(extra, item, f"{where}.{name}", document)
            elif kind == "object":
                problems.append(f"{where}.{name} is not in the document")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            problems += fits(schema["items"], item, f"{where}[{index}]", document)
    return problems


def document_path(rule: str) -> str:
    """Flask writes /alerts/<int:rule_id>, OpenAPI writes /alerts/{rule_id}."""
    parts = []
    for part in rule.split("/"):
        if part.startswith("<") and part.endswith(">"):
            part = "{" + part[1:-1].split(":")[-1] + "}"
        parts.append(part)
    return "/".join(parts)


def routes_of(app) -> set[tuple[str, str]]:
    """Every (METHOD, path) the app answers, written the OpenAPI way."""
    return {
        (method, document_path(rule.rule))
        for rule in app.url_map.iter_rules()
        for method in rule.methods
        if method.lower() in METHODS
    }


def documented_routes(document: dict = DOCUMENT) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, operations in document["paths"].items()
        for method in operations
        if method in METHODS
    }


def check_answer(method: str, path: str, status: int, body, document: dict = DOCUMENT) -> list[str]:
    """Problems with one answer. body is the parsed JSON, or None when there is none."""
    name = f"{method} {path} -> {status}"
    operation = document["paths"].get(path, {}).get(method.lower())
    if operation is None:
        return [f"{name}: the endpoint is not in the document"]
    answers = operation["responses"]
    answer = answers.get(str(status))
    if answer is None and status in GENERAL_ERRORS:
        answer = answers.get("default")
    if answer is None:
        return [f"{name}: this status code is not in the document"]
    content = resolve(answer, document).get("content")
    if content is None:
        return [] if body is None else [f"{name}: the document says no body, got {body!r}"]
    schema = content["application/json"]["schema"]
    return [f"{name}: {problem}" for problem in fits(schema, body, "body", document)]


def watch(app) -> list[str]:
    """Check every answer of app from now on. Returns the list the problems go into."""
    problems = []

    @app.after_request
    def compare_with_document(response):
        # A request no route matched (404, 405) has no endpoint to compare with.
        # OPTIONS is the browser's CORS preflight, answered by Flask-CORS.
        if request.url_rule is not None and request.method.lower() in METHODS:
            body = response.get_json(silent=True) if response.get_data() else None
            problems.extend(
                check_answer(
                    request.method,
                    document_path(request.url_rule.rule),
                    response.status_code,
                    body,
                )
            )
        return response

    return problems
