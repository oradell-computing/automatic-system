"""
Application Health Check

Confirms, on demand, that the application can securely retrieve its database
login and reach the database. Callers see only the outcome. Login details and
error messages stay in your private logs.
"""

import json
import logging
import os
import socket

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

# Set up once and reused across requests for faster responses; short timeouts surface problems quickly.
secrets_manager = boto3.client(
    "secretsmanager",
    config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 2}),
)


def respond(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    # Unknown addresses are turned away before anything sensitive is touched.
    if event.get("httpMethod") != "GET" or event.get("path") not in ("/", "/health"):
        return respond(404, {"message": "Not found"})

    try:
        # The login is fetched securely at run time and is never stored in code.
        response = secrets_manager.get_secret_value(
            SecretId=os.environ["DB_SECRET_ARN"]
        )
        secret = json.loads(response["SecretString"])

        # Opening a connection proves the private network path to the database is healthy.
        with socket.create_connection((secret["host"], int(secret["port"])), timeout=3):
            pass
    except Exception:
        logger.exception("Database health check failed")
        return respond(503, {"status": "unavailable"})

    return respond(200, {"status": "ok"})
