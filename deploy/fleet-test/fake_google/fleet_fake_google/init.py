"""One-shot fleet initialization (``fleet-init`` service). TEST ONLY.

Creates the upload bucket and seeds encrypted Google grants for the test
principals through the application's own Redis token store, exactly as the
OAuth callback would store them (the access token is fake and never leaves
the fake transport; its expiry is far in the future so no refresh happens).
"""

from __future__ import annotations

import json
import os
import time

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

from . import ENV, SENTINEL

SUBJECTS = ("alice", "bob", "carol", "dave", "erin", "frank", "grace", "heidi")
CAPABILITIES = ["people", "sheets", "calendar", "gmail", "drive"]


def main() -> None:
    if os.environ.get(ENV) != SENTINEL:
        raise SystemExit("fleet-init only runs in the fleet qualification stack")
    s3 = boto3.client("s3", endpoint_url=os.environ["MCP_UPLOAD_S3_ENDPOINT"])
    bucket = os.environ["MCP_UPLOAD_S3_BUCKET"]
    deadline = time.monotonic() + 60
    while True:
        try:
            s3.create_bucket(Bucket=bucket)
            break
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                break
            raise
        except EndpointConnectionError:
            # The S3 image has no healthcheck binary; wait for it here, bounded.
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)

    from mcp_google_workspace.auth import google_auth
    from mcp_google_workspace.auth.identity import Principal

    store = google_auth.get_token_store()
    assert store.backend_name == "redis", store.backend_name
    issuer = os.environ["MCP_HTTP_JWT_ISSUER"]
    for subject in SUBJECTS:
        store.save_credentials_json(
            Principal(issuer=issuer, subject=subject),
            json.dumps(
                {
                    "token": f"fleet-fake-access-{subject}",
                    "refresh_token": f"fleet-fake-refresh-{subject}",
                    "client_id": "fleet-fake-client",
                    "client_secret": "fleet-fake-secret",  # nosec B105 - test-only fake
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "scopes": google_auth.get_google_scopes(CAPABILITIES),
                    "expiry": "2099-01-01T00:00:00Z",
                }
            ),
        )
    # Idempotent: docker compose re-runs this service whenever a dependent
    # service is started through compose; the Google call log (CALLS_KEY) is
    # therefore never cleared here (a cold start has a fresh Redis volume).
    print(f"fleet-init: bucket {bucket!r}, grants for {len(SUBJECTS)} principals", flush=True)


if __name__ == "__main__":
    main()
