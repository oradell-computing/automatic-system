"""
Application Health Check

Confirms, on demand, that the application can securely retrieve its database
login and reach the database. Callers see only the outcome. Login details and
error messages stay in your private logs.

Fast and economical
The database address is remembered for five minutes, so busy periods do not
multiply lookups. The connection test itself is always live, and the password
is never kept.
"""

import json
import logging
import os
import socket
import time

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

HEALTH_PATHS = ("/", "/health")
ADDRESS_LIFETIME_SECONDS = 300

# Set up once and reused across requests for faster responses. Short timeouts and a
# single retry keep every outcome quick, even when something is wrong.
secrets_manager = boto3.client(
    "secretsmanager",
    config=Config(connect_timeout=2, read_timeout=2, retries={"total_max_attempts": 2}),
)

# Only the database address is remembered, never the login itself.
remembered = {"address": None, "expires": 0.0}


def respond(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def database_address():
    if remembered["address"] is None or time.monotonic() >= remembered["expires"]:
        # The login is fetched securely at run time and is never stored in code.
        response = secrets_manager.get_secret_value(
            SecretId=os.environ["DB_SECRET_ARN"]
        )
        secret = json.loads(response["SecretString"])
        remembered["address"] = (secret["host"], int(secret["port"]))
        remembered["expires"] = time.monotonic() + ADDRESS_LIFETIME_SECONDS
    return remembered["address"]


def handler(event, context):
    # Unknown addresses are turned away before anything sensitive is touched.
    path = (event.get("path") or "").rstrip("/") or "/"
    if event.get("httpMethod") != "GET" or path not in HEALTH_PATHS:
        return respond(404, {"message": "Not found"})

    try:
        # Opening a connection proves the private network path to the database is healthy.
        with socket.create_connection(database_address(), timeout=3):
            pass
    except Exception:
        # Look the address up afresh next time, in case the database has moved.
        remembered["address"] = None
        logger.exception("Database health check failed")
        return respond(503, {"status": "unavailable"})

    return respond(200, {"status": "ok"})
