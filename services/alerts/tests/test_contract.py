"""Checks the rules this service must keep: how it starts, /health and what the folder may hold."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from app import create_app
from app.config import load_config

SERVICE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = SERVICE_DIR.parent.parent


def free_port() -> int:
    """Ask the OS for a port nobody is using, then let it go again."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def get_status(url: str) -> int | None:
    """The HTTP status code, or None if nothing answered yet."""
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except OSError:
        return None


def test_python_app_py_starts_and_reports_healthy(tmp_path, database_url):
    port = free_port()
    env = dict(os.environ)
    env["PORT"] = str(port)
    env["POLL_INTERVAL_SECONDS"] = "0"
    env["GIT_SHA"] = "abc1234"

    log_path = tmp_path / "app.log"
    with open(log_path, "w") as log:
        process = subprocess.Popen(
            [sys.executable, "app.py"], cwd=SERVICE_DIR, env=env, stdout=log, stderr=log
        )
        try:
            status = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and process.poll() is None:
                status = get_status(f"http://127.0.0.1:{port}/health")
                if status is not None:
                    break
                time.sleep(0.1)

            assert status == 200, log_path.read_text()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                body = json.load(response)
            # Brief section 3: status, db and the version, which is the git SHA.
            assert body == {"status": "ok", "db": "ok", "version": "abc1234"}
        finally:
            process.terminate()
            process.wait(timeout=5)


def test_python_app_py_refuses_to_start_without_the_token_secret(database_url):
    env = dict(os.environ)
    env.pop("TOKEN_SECRET", None)
    # An empty value in the shell wins over any local .env file.
    env["TOKEN_SECRET"] = ""

    result = subprocess.run(
        [sys.executable, "app.py"], cwd=SERVICE_DIR, env=env, capture_output=True, text=True, timeout=20
    )

    assert result.returncode != 0
    assert "TOKEN_SECRET must be set" in result.stderr


def test_python_app_py_refuses_to_start_without_the_database_url():
    env = dict(os.environ)
    # An empty value in the shell wins over any local .env file.
    env["DATABASE_URL"] = ""

    result = subprocess.run(
        [sys.executable, "app.py"], cwd=SERVICE_DIR, env=env, capture_output=True, text=True, timeout=20
    )

    assert result.returncode != 0
    assert "DATABASE_URL must be set" in result.stderr


def test_health_returns_503_without_details_when_the_database_is_unreachable(database_url):
    config = load_config()
    client = create_app(config).test_client()
    assert client.get("/health").get_json() == {"status": "ok", "db": "ok", "version": "dev"}

    # Nothing listens on port 1, so the next connection is refused at once.
    config.database_url = "mssql+pymssql://someone:not-a-password@127.0.0.1:1/nowhere"

    response = client.get("/health")
    assert response.status_code == 503
    assert response.get_json() == {"status": "error", "db": "error", "version": "dev"}


def test_health_needs_no_token_and_ignores_a_bad_one(database_url):
    client = create_app(load_config()).test_client()

    assert client.get("/health", headers={"Authorization": "Bearer nonsense"}).status_code == 200


def test_requirements_txt_is_the_only_manifest_and_every_line_is_pinned():
    lines = (SERVICE_DIR / "requirements.txt").read_text().split()
    assert lines and all("==" in line for line in lines)
    for name in ("package.json", "pyproject.toml", "uv.lock", "Pipfile", "setup.py"):
        assert not (SERVICE_DIR / name).exists(), f"{name} must not exist"
        assert not (REPO_DIR / name).exists(), f"{name} must not exist"


def test_no_env_or_database_file_is_tracked():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_DIR, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    bad = [path for path in tracked if Path(path).name == ".env" or path.endswith(".db")]
    assert bad == []


def test_code_has_no_forbidden_settings():
    paths = [SERVICE_DIR / "app.py"] + sorted((SERVICE_DIR / "app").rglob("*.py"))
    problems = []
    for path in paths:
        text = path.read_text()
        for banned in ("journal_mode", "FileHandler", "debug=True", "127.0.0.1"):
            if banned in text:
                problems.append(f"{path.relative_to(SERVICE_DIR)} contains {banned}")
        # Addresses are settings: the defaults for WATCHLIST_URL and CORS_ORIGINS
        # live in config.py and nowhere else.
        for address in ("localhost", "http://", "https://"):
            if address in text and path.name != "config.py":
                problems.append(f"{path.relative_to(SERVICE_DIR)} contains {address}")
    assert problems == [], "\n".join(problems)


def test_the_poller_is_started_from_app_py_only():
    starters = [
        path.name
        for path in [SERVICE_DIR / "app.py"] + sorted((SERVICE_DIR / "app").rglob("*.py"))
        if "start_poller(" in path.read_text() and path.name != "poller.py"
    ]
    assert starters == ["app.py"]
