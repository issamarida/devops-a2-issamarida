"""Accounts and access control for the watchlist service. Not a business domain.

The database holds the accounts and the fixed-window rate limits. Sign in answers
with a signed token (app/tokens.py) and every later request carries it in the
Authorization header. There are no cookies and no server-side sessions, so
the alerts service can check the same token without calling this service.
"""

import hashlib
import math
import re
import secrets
import time
from contextlib import closing

from flask import Blueprint, g, request
from werkzeug.security import check_password_hash, generate_password_hash

from app.db import IntegrityError, get_connection
from app.tokens import is_service_token, issue_token, read_token

USERNAME = re.compile(r"[a-z0-9_]{3,30}")


class AccessStore:
    def __init__(self, database_url, max_accounts=50, clock=time.time):
        self.database_url = database_url
        self.max_accounts = max_accounts
        self.clock = clock
        # Unknown usernames still perform the same password-hash work.
        self.dummy_hash = generate_password_hash(secrets.token_urlsafe(32))

    def register(self, username, password):
        username = username.strip().lower()
        if not USERNAME.fullmatch(username):
            raise ValueError(
                "Use 3–30 lowercase letters, numbers or underscores for your username."
            )
        if not 15 <= len(password) <= 128:
            raise ValueError(
                "Use a password of 15–128 characters. A memorable passphrase works well."
            )
        password_hash = generate_password_hash(password)
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            # The two hints lock the table until commit, so two registrations
            # at the same moment cannot both pass the limit.
            cursor.execute("SELECT COUNT(*) AS total FROM accounts WITH (UPDLOCK, HOLDLOCK)")
            if cursor.fetchone()["total"] >= self.max_accounts:
                raise ValueError(
                    "Registration is unavailable. Please contact the app owner."
                )
            try:
                cursor.execute(
                    "INSERT INTO accounts (username, password_hash)"
                    " OUTPUT INSERTED.id VALUES (%s, %s)",
                    (username, password_hash),
                )
            except IntegrityError:
                raise ValueError(
                    "Unable to create this account. Choose another username or sign in."
                ) from None
            new_id = cursor.fetchone()["id"]
            conn.commit()
        return new_id

    def sign_in(self, username, password):
        """The account if the password is right, otherwise None."""
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM accounts WHERE username = %s", (username.strip().lower(),)
            )
            user = cursor.fetchone()
        stored = user["password_hash"] if user else self.dummy_hash
        valid = check_password_hash(stored, password)
        if not user or not valid:
            return None
        return {"id": user["id"], "username": user["username"]}

    def consume(self, key, limit, window):
        """Atomically reserve an attempt. Return Retry-After seconds when blocked."""
        key = hashlib.sha256(key.encode()).hexdigest()
        now = self.clock()
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            # Clear out the windows that are over, and save that first. From
            # here on this attempt only locks its own key, so two attempts for
            # different keys can never wait for each other.
            cursor.execute("DELETE FROM rate_limits WHERE reset_at <= %s", (now,))
            conn.commit()
            # The two hints lock this key until commit, also when it has no row
            # yet, so two attempts at the same moment are counted one by one.
            cursor.execute(
                "SELECT attempts, reset_at FROM rate_limits WITH (UPDLOCK, HOLDLOCK)"
                " WHERE [key] = %s",
                (key,),
            )
            row = cursor.fetchone()
            if row and row["attempts"] >= limit:
                conn.commit()
                return max(1, math.ceil(row["reset_at"] - now))
            if row:
                cursor.execute(
                    "UPDATE rate_limits SET attempts = attempts + 1 WHERE [key] = %s", (key,)
                )
            else:
                cursor.execute(
                    "INSERT INTO rate_limits ([key], attempts, reset_at) VALUES (%s, 1, %s)",
                    (key, now + window),
                )
            conn.commit()
        return 0


def bearer_token() -> str:
    """The token from "Authorization: Bearer <token>", or "" if there is none."""
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else ""


def text(body, name) -> str:
    """A JSON field as text. Anything that is not text counts as empty."""
    value = body.get(name) if isinstance(body, dict) else None
    return value if isinstance(value, str) else ""


def install_access(app, config):
    store = AccessStore(config.database_url, config.max_accounts)
    app.extensions["access_store"] = store
    # No request body here needs more than a few hundred bytes.
    app.config["MAX_CONTENT_LENGTH"] = 16384

    def limit(key, attempts, window):
        retry = store.consume(key, attempts, window)
        if retry:
            error = {"error": "Too many attempts. Please wait and try again."}
            return error, 429, {"Retry-After": str(retry)}
        return None

    @app.before_request
    def protect_request():
        g.user = None
        if request.method == "OPTIONS":
            # The browser's CORS preflight carries no token. Flask-CORS answers it.
            return None
        if request.blueprint == "internal":
            if not is_service_token(config.token_secret, bearer_token()):
                return {"error": "This endpoint is for the alerts service."}, 401
            return None
        if request.blueprint in ("watchlist", "market"):
            g.user = read_token(config.token_secret, bearer_token())
            if not g.user:
                return {"error": "Sign in to continue."}, 401
            if request.method != "GET":
                return limit(f"write:{g.user['id']}", 60, 60)
        return None

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    bp = Blueprint("auth", __name__)

    @bp.post("/auth/register")
    def register():
        blocked = limit(
            f"register:{request.remote_addr}",
            config.auth_attempts,
            config.auth_window_seconds,
        )
        if blocked is not None:
            return blocked
        body = request.get_json(silent=True)
        try:
            store.register(text(body, "username"), text(body, "password"))
        except ValueError as error:
            return {"error": str(error)}, 400
        return {"username": text(body, "username").strip().lower()}, 201

    @bp.post("/auth/login")
    def sign_in():
        body = request.get_json(silent=True)
        username = text(body, "username").strip().lower()
        for key, count in (
            (f"login-ip:{request.remote_addr}", config.auth_attempts * 5),
            (f"login-user:{username}", config.auth_attempts),
        ):
            blocked = limit(key, count, config.auth_window_seconds)
            if blocked is not None:
                return blocked
        password = text(body, "password")
        user = store.sign_in(username, password) if len(password) <= 128 else None
        if user is None:
            # The same answer for an unknown username and a wrong password.
            return {"error": "Incorrect username or password."}, 401
        token = issue_token(
            config.token_secret, user["id"], user["username"], config.token_seconds
        )
        return {
            "token": token,
            "username": user["username"],
            "expires_in": config.token_seconds,
        }

    app.register_blueprint(bp)
    return store
