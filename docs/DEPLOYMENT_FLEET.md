# Multi-replica (fleet) deployment runbook

This runbook covers running the authenticated Streamable HTTP server as several
replicas behind a load balancer, with a shared Redis, S3-compatible upload
storage and dedicated task workers. A single HTTP process and the stdio bundle
need none of this: they use in-memory backends, and Redis stays optional.

Everything marked **qualified** was exercised by the fleet qualification suite
(`tests/test_fleet_qualification.py`, W7a) against a real deployment of the
production image: two replicas and one worker in separate containers, Redis
8.6.7 over TLS with ACL users and AOF, an S3 API (versitygw 1.8.0), and
nginx 1.30.5 terminating TLS under a `/gw` path prefix. Google was answered by a
test-only fake transport (see [Qualification stack](#qualification-stack)), so
the Google APIs themselves are not part of that evidence. Library versions:
FastMCP / fastmcp-tasks 4.0.10, MCP SDK 2.2.0, pydocket 0.25.2, redis-py 8.

## Topology

```
clients ──TLS──> reverse proxy (/gw) ──round robin──> replica 1..N  (mcp-google-workspace-http)
                        │                                   │
                        └─ legacy traffic: address hash ────┘
replicas + workers ──rediss://──> Redis (state, grants, operations, uploads metadata, admission, task queue)
replicas + workers ──https──────> S3 bucket (encrypted upload blobs)
task workers (mcp-google-workspace-worker) consume the same queue
```

A process is part of a fleet when `MCP_WORKERS > 1`, `MCP_REPLICAS > 1` or
`MCP_REDIS_URL` is set; `/health/ready` then requires the whole shared
contract below.

## Required configuration

Set these **identically on every replica and worker** (secrets from your
secret store, never baked into the image):

| Setting | Purpose |
| --- | --- |
| `MCP_HTTP_BASE_URL` | Public URL including the proxy prefix, e.g. `https://mcp.example.com/gw`. The resource identifier is `<base>/mcp`; allowed `Host` / `Origin` default to its authority. |
| `MCP_HTTP_JWT_ISSUER`, `MCP_HTTP_JWT_AUDIENCE`, `MCP_HTTP_JWKS_URI` | Bearer-token verification (JWKS over HTTPS). |
| `MCP_GOOGLE_OAUTH_REDIRECT_URL`, `MCP_CREDENTIALS_DIR` | Google OAuth callback below the base URL, and the directory holding the server's Google OAuth client `credentials.json`. |
| `MCP_SECRET_FILE` (or `MCP_TOKEN_ENCRYPTION_KEYS` / `MCP_TOKEN_ENCRYPTION_KEY`) | Token key ring. Encrypts Google grants, OAuth state, dashboard state, operation records and upload blobs. |
| `MCP_REQUEST_STATE_KEYS` | Shared key ring sealing multi-round-trip confirmation continuations (each ≥ 32 bytes). |
| `FASTMCP_TASKS_ENCRYPTION_KEY` | Encrypts the caller snapshot stored with each queued task (≥ 32 random characters). **Must not be empty**: an empty value is refused by fastmcp-tasks at the first submission, and readiness reports it (qualified). |
| `MCP_REDIS_URL` | `rediss://<acl-user>:<password>@host:6379/0`. |
| `MCP_UPLOAD_S3_BUCKET`, `MCP_UPLOAD_S3_ENDPOINT` (non-AWS), AWS credentials | Shared upload objects. |
| `MCP_REPLICAS` | Number of replicas (declares the fleet even with one worker per replica). |
| `MCP_SHUTDOWN_GRACE_SECONDS` | Drain window after SIGTERM (default 30, max 300). |
| `SSL_CERT_FILE` (only for a private CA) | See [TLS trust](#tls-trust). |

Recommended for dedicated workers (qualified): `FASTMCP_DOCKET_CONCURRENCY=0`
on the HTTP replicas and `FASTMCP_DOCKET_CONCURRENCY=<n>` on the workers. Each
HTTP replica otherwise also runs an in-process queue consumer, and
[readiness does not stop it](#known-limitations).

Readiness (`GET /health/ready`, 200 or 503) checks: `encryption`,
`token_storage` (must be `redis`), `redis`, `upload_object_storage`,
`app_state`, `task_queue` (Redis, snapshot-encrypted), `operation_records`,
`operation_lease`, `continuation_keys`, `fleet_storage`, plus the advisory
`legacy_session_affinity`. Qualified: green on every replica; red for a
replica without `MCP_REQUEST_STATE_KEYS` and for one with an empty
`FASTMCP_TASKS_ENCRYPTION_KEY`.

### TLS trust

The JWKS client (httpx2) verifies against the OS trust store through
`truststore`, and Redis TLS uses Python's default SSL context (OpenSSL default
paths). For a private IdP or Redis CA, either add it to the image's system
store or set `SSL_CERT_FILE` to a bundle. `SSL_CERT_FILE` **replaces** the
trust store for both, so the bundle must also hold any public root the IdP
needs. The qualification stack sets it to the throwaway CA alone (JWKS over
TLS and `rediss://` both verified against it). Google API calls (httplib2) and
S3 (botocore) use their own certifi bundles per their documented defaults
(`HTTPLIB2_CA_CERTS`, `AWS_CA_BUNDLE` to override); that path was not
exercised here because Google is faked and the test S3 endpoint is plain
HTTP on the internal network.

## Redis requirements

| Requirement | Recommendation (qualified values in parentheses) |
| --- | --- |
| Version | Redis 7.2+ or 8.x with Lua scripting and Streams (8.6.7). Cluster mode is **not** qualified. |
| Persistence | AOF on, `appendfsync everysec`, plus RDB snapshots (`appendonly yes`, `appendfsync everysec`). Back up the AOF/RDB: Redis holds the only copy of every user's Google grant. |
| Eviction | `maxmemory-policy noeviction` (qualified). Every key is either durable (grants), security-relevant (operation records that prevent duplicate mutations, OAuth one-time state, revocation) or a work item (task queue). Evicting any of them silently turns into re-consent, duplicate or lost work. With `noeviction`, a full Redis fails writes loudly; the application fails closed (admission, revocation) and readiness shows it. Size `maxmemory` with headroom and alert at 70 %. |
| ACL | A dedicated user, default user off. Qualified rule: `user mcp on >… ~* &* +@all -@admin -@dangerous` (application, Docket queue and task results all work with it). Keep an administrative user separate. |
| TLS | `rediss://` with certificate verification (qualified: TLS-only Redis, `tls-port`, `port 0`). |
| Clock | Fleet rate limiting uses each replica's clock for a sliding window; keep replicas NTP-synchronized. |
| Topology | One logical database shared by all replicas and workers. `MCP_TOKEN_REDIS_URL` can move OAuth credentials/state to a separate Redis. |

### What lives in Redis, and what a Redis restart or loss means

| Keys | Data | Restart with AOF (qualified) | Loss of the data set |
| --- | --- | --- | --- |
| `mcp:google-oauth:*` | Encrypted Google grants, one-time OAuth state, refresh locks | Survive | **Every user must reconnect Google.** In-flight consent flows fail. |
| `mcp:operation:v1:*` | W4b operation records (prepare/commit, confirmations, uncertain outcomes) | Survive: a repeated confirmation or commit still replays the saved result | Replay protection and `outcome_unknown` evidence are gone for recent operations; a client retrying an old continuation could execute again if its seal is still valid. Rotate `MCP_REQUEST_STATE_KEYS` after a loss. |
| `mcp:appstate:v1:*` | Dashboard view state | Survives | Views return `view_handle_invalid`; the UI launches a new view. |
| `mcp:uploads:*` | Upload metadata and quota (blobs are in S3) | Survives | Handles become unknown; S3 objects are orphaned until the bucket lifecycle rule removes them. |
| `mcp:admission:*` | Fleet rate/concurrency counters | Survive | Counters reset (harmless). |
| `mcp:revoked_principals` | Emergency revocation set | Survives | **Revocations are lost**: re-apply them (or keep them in `MCP_REVOKED_PRINCIPALS` too). |
| `mcp-google-workspace:*` (`FASTMCP_DOCKET_NAME`) | Task queue, task state and results, caller snapshots | Survive: queued tasks run after the restart, finished tasks stay readable | Queued and running tasks and their results are lost; clients polling them get "not found". |

The "restart" column is qualified; the "loss" column is derived from the code
(no test deletes the data set). With `appendfsync everysec`, a Redis crash
(not a clean restart) can lose up to about one second of writes. Qualified: restarting Redis mid-run keeps
dashboard state, uploads, operation replay and the queue; every replica and the
worker reconnect by themselves and the next requests succeed.

## Object storage

Use a private bucket with server-side encryption and a lifecycle rule that
deletes objects older than `MCP_UPLOAD_TTL_SECONDS` plus a margin (the
application also encrypts each blob with the token key ring). The replicas
need `PutObject`, `GetObject`, `DeleteObject` and `HeadBucket` on the upload
prefix only.

## Reverse proxy

The qualified configuration is `deploy/fleet-test/nginx/nginx.conf` (parts
marked `TEST-ONLY` must not be copied). Production essentials:

```nginx
resolver <your DNS> valid=10s;          # with "resolve" below: follows replica address changes

map $http_mcp_protocol_version $mcp_pool {
    "2026-07-28" mcp_modern;            # modern: no affinity
    default      mcp_legacy;            # legacy handshake sessions: pinned
}
upstream mcp_modern {
    zone mcp_modern 64k;
    server replica-1:8000 resolve max_fails=1 fail_timeout=2s;
    server replica-2:8000 resolve max_fails=1 fail_timeout=2s;
    keepalive 16;
}
upstream mcp_legacy {
    zone mcp_legacy 64k;
    hash $binary_remote_addr consistent;   # see "Legacy clients and affinity"
    server replica-1:8000 resolve max_fails=1 fail_timeout=2s;
    server replica-2:8000 resolve max_fails=1 fail_timeout=2s;
    keepalive 16;
}

proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_set_header Host $http_host;       # the app validates Host against MCP_HTTP_BASE_URL
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_buffering off;                    # request-scoped SSE (progress) must stream
proxy_request_buffering off;            # the app bounds chunked bodies while they stream
proxy_connect_timeout 2s;
proxy_read_timeout 700s;                # > MCP_EXPENSIVE_DEADLINE_SECONDS (600)
proxy_send_timeout 700s;
proxy_next_upstream error timeout;      # only when the request never reached a replica
client_max_body_size 31m;               # >= MCP_MAX_REQUEST_BYTES (30 MiB default)

server {
    listen 443 ssl;
    location = /gw/mcp { proxy_pass http://$mcp_pool/mcp; }
    location /gw/      { proxy_pass http://mcp_modern/; }        # health, version, OAuth callback
    location /.well-known/oauth-protected-resource/gw/ { proxy_pass http://mcp_modern; }
}
```

Qualified through this proxy over TLS: round robin alternates every modern
request between replicas; request-scoped SSE progress reaches the client
while the tool is still running (no buffering); `MCP-Protocol-Version` /
`Mcp-Name` mismatches get `-32020`, a foreign `Origin` 403 and a foreign `Host`
421; the RFC 9728 metadata is reachable at
`https://<host>/.well-known/oauth-protected-resource/gw/mcp` and names
`<base>/mcp`; a chunked body over `MCP_MAX_REQUEST_BYTES` gets the
application's 413 while the client is still sending (with request buffering
on, nginx would first spool the whole body up to `client_max_body_size`), and a
declared length over `client_max_body_size` gets nginx's own 413.

- Never add `non_idempotent` to `proxy_next_upstream`: a retried tool call can
  repeat a Google mutation.
- Health endpoints: `/health/live` for liveness, `/health/ready` for
  load-balancer membership; `/metrics` should not be public.
- Several proxy instances: the modern pool needs nothing shared. The legacy
  pool's consistent hash gives the same mapping on every instance with the
  same server list.

### Legacy clients and affinity

Handshake-era clients (protocol 2025-11-25 and older) create an
`Mcp-Session-Id` session held in the memory of the replica that answered
`initialize`. Qualified findings:

- **Plain round robin breaks legacy sessions**: every request routed to the
  other replica gets `404 Session not found` (TEST-ONLY route `/rr/mcp`).
- **Hashing on `Mcp-Session-Id` does not work either**: `initialize` carries
  no session id, so it is placed without regard to the later hash. In a probe
  of 20 sessions through `hash $http_mcp_session_id consistent`, 16 broke.
- **Qualified rule**: route requests whose `MCP-Protocol-Version` is not
  `2026-07-28` (legacy requests, including `initialize`, which sends no header)
  to an upstream hashed on the client address. A 2025-11-25 client then lists
  tools, calls them and completes an elicitation confirmation through the
  proxy, while modern traffic keeps round-robin.
- Behind another load balancer, hash a trusted client address
  (`set_real_ip_from` + `real_ip_header X-Forwarded-For`). Many clients behind
  one NAT all land on one replica (uneven, still correct). A replica restart
  drops its legacy sessions; clients must re-initialize.
- Set `MCP_SESSION_AFFINITY=true` once the rule is in place; readiness reports
  it as the advisory `legacy_session_affinity` check.
- Sticky cookies are not a substitute: MCP clients are not browsers.

## Readiness, drain and rolling restarts

On SIGTERM a replica (qualified):

1. closes its listener at once, so readiness probes and new connections fail
   and the proxy's next request goes to another replica
   (`proxy_next_upstream error`);
2. lets in-flight requests finish for up to `MCP_SHUTDOWN_GRACE_SECONDS`
   (an 8-second tool call completed normally after SIGTERM);
3. then stops its in-process task consumer (if `FASTMCP_DOCKET_CONCURRENCY` is
   above zero), which first finishes the tasks it is running, waits up to the
   same window for any remaining task accounting, and exits.

Set the orchestrator's stop grace period (Kubernetes
`terminationGracePeriodSeconds`, compose `stop_grace_period`) above
`2 × MCP_SHUTDOWN_GRACE_SECONDS` plus a margin, and above the longest task
deadline if replicas also execute tasks. For load balancers that do not retry
on connection errors, add a short pre-stop delay so they observe the failed
readiness probe before the listener closes.

Before W7a, FastMCP's `run()` capped the in-flight wait at 2 seconds: every
rolling restart cut ongoing tool calls off with `500`. `server_http` now passes
the drain window to uvicorn.

Task workers (`mcp-google-workspace-worker`) have no HTTP listener. Their
health is the Docket heartbeat (the qualification stack's healthcheck checks
that the host's worker is registered on the queue). On SIGTERM a worker stops
taking tasks, finishes the ones already running (each bounded by its tool
deadline) and exits 0; qualified: a task in flight during a worker restart
completed exactly once and the restart took seconds. Before W7a the upstream
worker CLI ignored SIGTERM as PID 1 and was SIGKILLed mid-task after the grace
period. Give workers a stop grace period above `MCP_EXPENSIVE_DEADLINE_SECONDS`
if tasks must never be interrupted. A task interrupted by SIGKILL (or a crash)
is redelivered by Docket after `FASTMCP_DOCKET_REDELIVERY_TIMEOUT` (default
300 s). Each delivery is claimed in the operation store under its task id, so
the redelivered task does **not** run its body again: it finishes with an
`outcome_unknown` tool error and verification guidance (qualified: one Google
call, then `outcome_unknown`). Before W7a the body ran a second time, repeating
the non-idempotent batchUpdate.

Qualified recovery: after restarting one replica and the worker mid-flow,
dashboard state (and its revision), uploads, saved operation results and a
pending confirmation survive, the pending confirmation executes exactly once
when answered on the restarted replica, and a task queued while no worker ran
executes when the worker returns.

## Key rotation

| Key ring | Procedure |
| --- | --- |
| Token key ring (`MCP_SECRET_FILE`) | Add the new key id, make it `active_token_encryption_key_id`, keep the old key listed, roll out to every replica and worker. Every ciphertext starts with its key id (`<key_id>.<token>`), and reads accept any listed key. The Redis grant store re-encrypts a grant only when it is saved again (a Google token refresh or reconnect), so a grant of an inactive user keeps the old key id: before removing a key, scan `mcp:google-oauth:*` values and the S3 upload objects for that prefix (uploads expire after `MCP_UPLOAD_TTL_SECONDS`). Dashboard state and operation records expire on their own TTL. |
| `MCP_REQUEST_STATE_KEYS` | `<new>,<old>` on every replica, wait `MCP_CONFIRMATION_TTL_SECONDS`, then `<new>`. No drain needed; a continuation sealed only with a removed key fails closed (the user confirms again). |
| `FASTMCP_TASKS_ENCRYPTION_KEY` | Single key, no ring: [drain the queue](#queue-draining-before-upgrades-or-task-key-rotation), then change it everywhere at once. A worker with a different key fails the task (it never runs it anonymously). |
| IdP signing keys | Handled by the IdP's JWKS; an unknown `kid` triggers a refetch at most every 30 s. Publish the new key before signing with it. |
| Redis ACL password | Redis users accept several passwords: `ACL SETUSER mcp >new`, roll `MCP_REDIS_URL`, then `ACL SETUSER mcp <old`. |
| S3 credentials | Issue new credentials, roll out, revoke the old ones. |

## Queue draining before upgrades (or task key rotation)

The task-queue format belongs to the fastmcp-tasks/Docket versions; the plan's
cutover rule (W8) is to drain before switching versions or the task key:

1. Stop new submissions: take the fleet out of the load balancer (maintenance
   page), or scale the HTTP replicas to zero. Keep the workers running.
2. Wait until nothing is queued or running, for example
   `XLEN mcp-google-workspace:stream` is `0`, `ZCARD mcp-google-workspace:queue`
   is `0`, and `XPENDING mcp-google-workspace:stream mcp-google-workspace:workers`
   (the consumer group) reports no pending entries.
3. Stop the workers (SIGTERM), deploy the new release and configuration to
   replicas and workers, start them, and re-enable traffic.

Finished task results that clients have not fetched yet are not migrated.

## Known limitations

- **Readiness does not stop an in-process task consumer** (from the
  fastmcp-tasks lifespan code, not exercised by the suite): the consumer starts
  with the server whenever task tools exist, so a replica that is not ready
  (for example with a wrong task key) still consumes tasks if its
  `FASTMCP_DOCKET_CONCURRENCY` is above zero. Run dedicated workers and set the
  replicas to `0`, as qualified.
- **A redelivered task reports `outcome_unknown`** rather than re-running (see
  above); even a task that had not yet reached Google by the time its worker
  died is not retried automatically. The caller verifies and resubmits.
- **Cancellation is not an undo.** Cancelling a running task whose Google call
  was in flight records `outcome_unknown` on the worker, while the task status
  is `cancelled`; the mutation may have happened.
- Task keys, results and arguments in Redis are not encrypted by the
  application (only the caller snapshot is), and Docket's task keys and log
  lines include the OAuth client id and subject of the submitter.
- Legacy sessions are lost when their replica restarts.

## Qualification stack

`deploy/fleet-test/` runs the qualification locally and in CI
(`.github/workflows/fleet.yml`):

```bash
MCP_FLEET_TEST=1 uv run pytest -m fleet tests/test_container_image.py tests/test_fleet_qualification.py
# or keep a stack for manual work:
uv run python deploy/fleet-test/fleet.py up      # ... and later: fleet.py down
```

`fleet.py` generates every secret at run time into the gitignored
`deploy/fleet-test/.runtime/` (throwaway CA and certificates, JWT signing key
and JWKS, token key ring, continuation and task keys, Redis ACL passwords, S3
credentials); nothing is committed. The suite talks only to nginx over TLS.

**Fake Google safety.** Google is replaced at the socket layer (the real
credential loading and googleapiclient request path still run). The fake:

1. exists only in a derived image (`deploy/fleet-test/fake_google/Dockerfile`)
   that is built locally and never published; the production image contains
   no fake module or startup hook (asserted by `tests/test_container_image.py`);
2. activates only when `MCP_FLEET_FAKE_GOOGLE` equals its sentinel; any other
   value terminates the process;
3. refuses to activate unless the JWT issuer and JWKS hosts are under the
   reserved `.test` TLD and the public base URL is loopback, which a real
   deployment cannot satisfy;
4. is rejected by the production entrypoints: with `MCP_FLEET_FAKE_GOOGLE` set
   and no fake loaded, `mcp-google-workspace-http` and the worker exit at
   startup;
5. is visible (`/version` reports `test_only_fake_google: true`) and has no
   route out: every Python container sits on an internal Docker network and
   trusts only the throwaway CA.
