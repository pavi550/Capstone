"""Authenticate and queue Jira notifications forwarded by an operator-controlled relay."""

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime


def accept(store, config, body, *, delivery_id, timestamp, signature):
    if len(body) > 256_000 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", delivery_id):
        raise ValueError("Invalid Jira intake envelope")
    key = config.secret("enterprise.intake.key_env")
    projects = config.get("enterprise.intake.projects", [])
    if not key or len(key.encode()) < 32 or not isinstance(projects, list) or not projects:
        raise ValueError("Configure the Jira relay signing key and eligible projects")
    if not re.fullmatch(r"[0-9]{1,12}", timestamp) or abs(datetime.now(UTC).timestamp() - int(timestamp)) > 300:
        raise ValueError("Jira intake envelope is outside the replay window")
    if not re.fullmatch(r"[a-f0-9]{64}", signature):
        raise ValueError("Invalid Jira intake signature")
    signed = f"{delivery_id}.{timestamp}.".encode() + body
    if not hmac.compare_digest(hmac.new(key.encode(), signed, hashlib.sha256).hexdigest(), signature):
        raise ValueError("Invalid Jira intake signature")
    event = json.loads(body)
    issue = event["issue"]["key"]
    if (event.get("webhookEvent") != "jira:issue_created" or not re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", issue)
            or issue.rsplit("-", 1)[0] not in projects):
        raise ValueError("Jira intake event or project is ineligible")
    return store.enqueue_issue(delivery_id, hashlib.sha256(body).hexdigest(), issue)


def poll(store, config):
    from .jira import JiraClient

    projects = config.get("enterprise.intake.projects", [])
    query = config.get("jira.jql")
    if not config.get("enterprise.intake.allow_poll", False) or not projects or not query:
        raise ValueError("Jira polling requires an enabled policy, allowed projects and a backlog query")
    events = []
    for issue in JiraClient(config).search(query, config.get("jira.max_results", 50)):
        key = issue["key"]
        if (not re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", key)
                or key.rsplit("-", 1)[0] not in projects):
            continue
        revision = issue.get("fields", {}).get("updated")
        if not isinstance(revision, str) or not revision:
            continue
        identity = hashlib.sha256(json.dumps([key, revision], separators=(",", ":")).encode()).hexdigest()
        status = store.enqueue_issue(f"poll-{identity}", identity, key)
        events.append(status)
    return events


def webhook(config, operation, *, hook_id=None, authorized=False):
    from urllib.parse import urlsplit

    from .httpclient import HttpError, request_json

    policy = config.get("enterprise.intake.webhook", {})
    if policy.get("authentication") != "oauth2":
        raise ValueError("Dynamic registration requires Jira OAuth 2.0; use administrator-managed webhook setup otherwise")
    cloud_id = policy.get("cloud_id", "")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", cloud_id):
        raise ValueError("Configure the Jira OAuth cloud ID")
    if operation != "list" and (not authorized or not policy.get("allow_write")):
        raise ValueError("Webhook changes require policy enablement and explicit authorization")
    token = config.secret("enterprise.intake.webhook.token_env")
    if not token:
        raise ValueError("Configure an OAuth token for this Jira integration")
    base = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/webhook"
    method, suffix, data = "GET", "", None
    if operation == "register":
        url = policy.get("callback", "")
        parsed = urlsplit(url)
        projects = config.get("enterprise.intake.projects", [])
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.query
                or parsed.fragment or not projects or any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) for key in projects)):
            raise ValueError("Configure an authorized HTTPS relay callback and valid project keys")
        method = "POST"
        data = {"url": url, "webhooks": [{"events": ["jira:issue_created"],
                                        "jqlFilter": f"project in ({','.join(projects)})"}]}
    elif operation in {"renew", "remove"}:
        if type(hook_id) is not int or hook_id <= 0 or hook_id not in policy.get("managed_ids", []):
            raise ValueError("Supply a webhook ID in the configured managed scope")
        method, suffix = ("PUT", "/refresh") if operation == "renew" else ("DELETE", "")
        data = {"webhookIds": [hook_id]}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if operation == "list":
            values = []
            for start in range(0, 1000, 50):
                page = request_json(method, f"{base}?startAt={start}&maxResults=50", headers=headers)
                values.extend({key: row.get(key) for key in ("id", "expirationDate", "events", "jqlFilter")}
                              for row in page["values"])
                if page.get("isLast") or not page["values"]:
                    return {"webhooks": values}
            raise ValueError("Webhook listing exceeds the configured pagination bound")
        response = request_json(method, base + suffix, headers=headers, body=data)
    except HttpError as exc:
        raise RuntimeError(f"Jira webhook operation failed (HTTP {exc.status})") from None
    if operation == "register":
        rows = response.get("webhookRegistrationResult", [])
        if len(rows) != 1 or type(rows[0].get("createdWebhookId")) is not int:
            raise ValueError("Jira did not return an unambiguous webhook registration ID; reconcile with list")
        return {"status": "REGISTERED", "id": rows[0]["createdWebhookId"]}
    return {"status": "RENEWED" if operation == "renew" else "REMOVED", "id": hook_id,
            "expiration": response.get("expirationDate") if response else None}


def relay_application(store, config):
    """WSGI endpoint for an independently authenticated and HMAC-signing relay."""
    def app(environ, start_response):
        try:
            if environ.get("REQUEST_METHOD") == "GET" and environ.get("PATH_INFO") == "/health":
                start_response("200 OK", [("Content-Type", "application/json")])
                return [b'{"status":"UP"}']
            length = int(environ.get("CONTENT_LENGTH") or 0)
            if environ.get("REQUEST_METHOD") != "POST" or environ.get("PATH_INFO") != "/jira" or not 0 < length <= 256_000:
                raise ValueError("Invalid webhook request")
            body = environ["wsgi.input"].read(length)
            if len(body) != length:
                raise ValueError("Incomplete webhook request")
            accept(store, config, body, delivery_id=environ.get("HTTP_X_CAPSTONE_DELIVERY", ""),
                   timestamp=environ.get("HTTP_X_CAPSTONE_TIMESTAMP", ""),
                   signature=environ.get("HTTP_X_CAPSTONE_SIGNATURE", ""))
        except (KeyError, TypeError, ValueError, UnicodeError):
            start_response("400 Bad Request", [("Content-Type", "text/plain")])
            return [b"Rejected Jira delivery"]
        start_response("202 Accepted", [("Content-Type", "text/plain")])
        return [b"Queued"]
    return app


def renewal_tick(store, config, *, now=None):
    """One durable scheduler tick; reconcile remote expiry before any refresh."""
    import sqlite3
    from contextlib import closing

    policy = config.get("enterprise.intake.webhook", {})
    if not policy.get("auto_renew") or not policy.get("allow_write"):
        raise ValueError("Automatic renewal requires explicit persistent write authorization")
    stamp = now if now is not None else datetime.now(UTC).timestamp()
    window = policy.get("renew_before_seconds", 259200)
    retry = policy.get("renew_retry_seconds", 3600)
    if any(type(value) is not int or not 60 <= value <= 604800 for value in (window, retry)):
        raise ValueError("Renewal intervals must be between one minute and seven days")
    # Process death releases this lock. A recovered tick always lists first.
    lock_path = store.path.with_name(f".{store.path.name}.webhook.lock")
    with closing(sqlite3.connect(lock_path, timeout=0)) as lock:
        try:
            lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            return {"status": "ALREADY_RUNNING"}
        with closing(store._connect()) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS webhook_schedule "
                               "(hook_id INTEGER PRIMARY KEY, expires REAL, next_poll REAL, state TEXT, failure TEXT)")
            connection.execute("CREATE TABLE IF NOT EXISTS webhook_history "
                               "(hook_id INTEGER, observed REAL, state TEXT, failure TEXT)")
        results = []
        remote = {row["id"]: row for row in webhook(config, "list")["webhooks"]}
        for hook_id in policy.get("managed_ids", []):
            with closing(store._connect()) as connection:
                old = connection.execute("SELECT * FROM webhook_schedule WHERE hook_id=?", (hook_id,)).fetchone()
            if old and old["next_poll"] > stamp:
                continue
            expiration, failure = None, None
            row = remote.get(hook_id)
            if row is None:
                state = "MISSING_REGISTRATION"
            else:
                expiry = row.get("expirationDate")
                if type(expiry) not in {int, float}:
                    state = "INVALID_EXPIRATION"
                else:
                    expiration = expiry / 1000
                    state = "ACTIVE" if expiration > stamp else "EXPIRED"
                    if expiration > stamp and expiration - stamp <= window:
                        try:
                            response = webhook(config, "renew", hook_id=hook_id, authorized=True)
                            refreshed = response.get("expiration")
                            if type(refreshed) not in {int, float} or refreshed / 1000 <= expiration:
                                raise ValueError("Renewal needs reconciliation")
                            expiration, state = refreshed / 1000, "RENEWED"
                        except (RuntimeError, ValueError, OSError) as exc:
                            state, failure = "RECONCILE_REQUIRED", type(exc).__name__
            with closing(store._connect()) as connection, connection:
                connection.execute("INSERT OR REPLACE INTO webhook_schedule VALUES (?, ?, ?, ?, ?)",
                                   (hook_id, expiration, stamp + retry, state, failure))
                connection.execute("INSERT INTO webhook_history VALUES (?, ?, ?, ?)",
                                   (hook_id, stamp, state, failure))
            results.append({"id": hook_id, "status": state, "expires": expiration, "failure": failure})
        return {"status": "COMPLETED", "registrations": results}


def serve(store, config):
    """Run behind an operator-managed TLS reverse proxy; no live registration."""
    from wsgiref.simple_server import make_server

    policy = config.get("enterprise.intake.hosted", {})
    if not policy.get("enabled"):
        raise ValueError("Hosted Jira ingress is disabled")
    host, port = policy.get("host", "127.0.0.1"), policy.get("port", 8787)
    if host not in {"127.0.0.1", "::1"} or type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Bind ingress to loopback behind an authenticated TLS reverse proxy")
    with make_server(host, port, relay_application(store, config)) as server:
        server.serve_forever(poll_interval=0.5)
