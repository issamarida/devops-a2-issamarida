"""system.yaml at the repository root must describe this service as the code runs it.

Brief section 8a: every environment variable the service reads is listed
with its default, and secrets are marked secret with no default. These tests
read the variables the way load_config() really asks for them, so a new
variable or a changed default fails here until system.yaml says the same.
"""

import os
from pathlib import Path

import pytest
import yaml

from app.config import load_config

SERVICE = "alerts"
ROOT = Path(__file__).resolve().parents[3]
SYSTEM = yaml.safe_load((ROOT / "system.yaml").read_text())
DESCRIBED = SYSTEM["services"][SERVICE]


class RecordingEnviron(dict):
    """An environment that remembers each name the code asked for, and its default."""

    def __init__(self, values):
        super().__init__(values)
        self.asked = {}

    def get(self, name, default=None):
        self.asked[name] = default
        return super().get(name, default)


@pytest.fixture
def asked(monkeypatch):
    """Every variable load_config() reads, with the default the code gives it."""
    environ = RecordingEnviron(
        {
            "DATABASE_URL": "mssql+pymssql://someone:not-a-password@db.example:1433/x",
            "TOKEN_SECRET": "test-only-token-secret-not-a-real-one",
        }
    )
    monkeypatch.setattr(os, "environ", environ)
    load_config()
    return environ.asked


def test_every_variable_the_code_reads_is_listed_and_nothing_else(asked):
    assert set(asked) == set(DESCRIBED["env"])


def test_each_default_is_the_one_in_the_code(asked):
    for name, variable in DESCRIBED["env"].items():
        if not variable.get("secret"):
            assert str(variable["default"]) == asked[name], name


def test_the_secrets_are_the_variables_with_no_default_in_the_code(asked):
    secrets = {name for name, variable in DESCRIBED["env"].items() if variable.get("secret")}
    assert secrets == {name for name, default in asked.items() if default == ""}


def test_the_port_and_the_paths_match_this_folder():
    assert DESCRIBED["port_default"] == int(DESCRIBED["env"]["PORT"]["default"])
    for key in ("path", "openapi", "migrations"):
        assert (ROOT / DESCRIBED[key]).exists(), key
    assert DESCRIBED["path"] == f"services/{SERVICE}"


def test_the_call_across_the_seam_is_in_the_watchlist_openapi_document():
    (call,) = DESCRIBED["calls"]
    assert call["url_env"] in DESCRIBED["env"]
    assert call["timeout_s"] == float(DESCRIBED["env"]["WATCHLIST_TIMEOUT_SECONDS"]["default"])
    other = yaml.safe_load((ROOT / SYSTEM["services"][call["service"]]["openapi"]).read_text())
    for endpoint in call["endpoints"]:
        method, path = endpoint.split(" ")
        assert method.lower() in other["paths"][path], endpoint
