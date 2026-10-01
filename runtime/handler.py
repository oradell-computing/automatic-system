"""
Application Health Check

Confirms that the application can fetch its database login and reach the
database, then reports only "ok" or "unavailable". Login details and error
messages stay in your private logs.

No extra code libraries
AWS's Parameters and Secrets extension fetches the login and caches it
briefly, so the handler needs only Python's standard library.
"""

import json
import logging
import os
import socket
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# The extension answers on this local address and keeps a short-lived copy of the login.
SECRETS_ENDPOINT = "http://localhost:2773/secretsmanager/get?secretId="

# Short waits keep every answer well inside the function's time limit.
EXTENSION_TIMEOUT_SECONDS = 3
DATABASE_TIMEOUT_SECONDS = 3


def database_address() -> tuple[str, int]:
    """Return the database host and port from the login secret."""
    secret_id = urllib.parse.quote(os.environ["DB_SECRET_ARN"], safe="")
    request = urllib.request.Request(
        SECRETS_ENDPOINT + secret_id,
        headers={"X-Aws-Parameters-Secrets-Token": os.environ["AWS_SESSION_TOKEN"]},
    )
    with urllib.request.urlopen(request, timeout=EXTENSION_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read())
    secret = json.loads(payload["SecretString"])
    return secret["host"], int(secret["port"])


def respond(status_code: int, body: dict[str, str]) -> dict[str, Any]:
    """Build an API Gateway proxy response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Answer GET /health and GET /items with the login and database health."""
    try:
        # Opening a connection proves the private network path to the database works.
        with socket.create_connection(
            database_address(), timeout=DATABASE_TIMEOUT_SECONDS
        ):
            pass
    except (OSError, KeyError, ValueError):
        logger.exception("Health check failed")
        return respond(503, {"status": "unavailable"})

    return respond(200, {"status": "ok"})
