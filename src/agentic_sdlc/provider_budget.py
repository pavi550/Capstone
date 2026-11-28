"""Transactional reservations shared by parallel agents and resumed attempts."""

import json
import math
from contextlib import closing
from uuid import uuid4


def reserve(context, config, agent, input_bound, output_bound, rates):
    policies = config.get("enterprise.budgets", {})
    if context is None:
        if any(policies.values()):
            raise ValueError("A budgeted model request requires a stored lifecycle stage")
        return None
    store, run_id, stage, attempt = context
    journal_stage = stage
    parts = stage.split("/")
    stage = parts[2] if parts[0] == "workflow" and len(parts) > 2 else parts[0]
    policy_agent = {"test": "testcase", "code": "coding", "requirement": "requirements"}.get(agent, agent)
    scopes = [("run", "", policies.get("run", {})),
              ("run", "", policies.get("requirement", {})),
              ("agent", agent, policies.get("agents", {}).get(policy_agent, policies.get("agents", {}).get(agent, {}))),
              ("stage", stage, policies.get("stages", {}).get(stage, {})),
              ("request", "", policies.get("request", {}))]
    enabled = any(policy for _, _, policy in scopes) or bool(policies.get("experiment"))
    if enabled and output_bound is None:
        raise ValueError("A budgeted provider needs an explicit max_tokens bound")
    price = None
    if output_bound is not None and all(rate is not None for rate in rates) and config.get("llm.currency", "USD") == "USD":
        price = math.ceil(1000 * (input_bound * rates[0] + output_bound * rates[1]))
    identity = uuid4().hex
    with closing(store._connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        parent = connection.execute("SELECT experiment_id FROM experiment_runs WHERE run_id=?", (run_id,)).fetchone()
        if policies.get("experiment"):
            if parent is None:
                raise ValueError("Experiment budgets require a registered experiment trial")
            scopes.append(("experiment", parent[0], policies["experiment"]))
        for scope, subject, policy in scopes:
            if not policy:
                continue
            selector = {"run": "", "agent": " AND agent=?", "stage": " AND stage=?", "request": " AND 0", "experiment": ""}[scope]
            condition = "run_id=?" + selector
            parameters = (run_id, subject) if subject else (run_id,)
            if scope == "experiment":
                condition = "run_id IN (SELECT run_id FROM experiment_runs WHERE experiment_id=?)"
                parameters = (subject,)
            # Conditions come from fixed SQL fragments and values are bound separately.
            totals = connection.execute(
                "SELECT COALESCE(SUM(charged_input),0), COALESCE(SUM(charged_output),0), "  # nosec B608
                "COALESCE(SUM(charged_cost),0), COUNT(*)-COUNT(charged_cost) FROM provider_requests WHERE "
                + condition, parameters,
            ).fetchone()
            proposed = {"max_input_tokens": totals[0] + input_bound,
                        "max_output_tokens": totals[1] + (output_bound or 0),
                        "max_total_tokens": totals[0] + totals[1] + input_bound + (output_bound or 0),
                        "max_cost_usd": None if price is None or totals[3] else (totals[2] + price) / 1_000_000_000}
            for key, limit in policy.items():
                if key not in proposed or type(limit) not in {float, int} or not math.isfinite(limit) or limit < 0:
                    raise ValueError("Invalid provider budget configuration")
                if proposed[key] is None or proposed[key] > limit:
                    raise ValueError(f"Provider {scope} budget exhausted or pricing unavailable")
        connection.execute("INSERT INTO provider_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'RESERVED')",
                           (identity, run_id, stage, attempt, agent, config.get("llm.provider"), config.get("llm.model"),
                            input_bound, output_bound or 0, price))
        connection.execute("INSERT INTO provider_request_ownership VALUES (?, ?)", (identity, journal_stage))
    return store, identity


def reconcile(reservation, inputs=None, outputs=None, cost=None, status="FAILED", usage=None):
    if reservation is None:
        return
    store, identity = reservation
    with closing(store._connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        if usage is not None:
            connection.execute("INSERT OR IGNORE INTO provider_usage VALUES (?, ?)",
                               (identity, json.dumps(usage, allow_nan=False)))
        connection.execute(
            "UPDATE provider_requests SET actual_input=?, actual_output=?, state=?, "
            "charged_input=COALESCE(?,charged_input), charged_output=COALESCE(?,charged_output), "
            "charged_cost=COALESCE(?,charged_cost) WHERE request_id=? AND state='RESERVED'",
            (inputs, outputs, status, inputs, outputs, math.ceil(cost * 1_000_000_000) if cost is not None else None, identity),
        )


def normalize_usage(provider, reply, config):
    """Normalize reported counts only; monetary estimates are never invoices."""
    def count(value):
        return value if type(value) is int and value >= 0 else None
    raw = reply.get("usage") or {}
    if provider == "anthropic":
        cached = count(raw.get("cache_read_input_tokens"))
        written = count(raw.get("cache_creation_input_tokens"))
        inputs, outputs = count(raw.get("input_tokens")), count(raw.get("output_tokens"))
        # Anthropic's base input count excludes cache reads and writes.
        if inputs is not None:
            inputs += (cached or 0) + (written or 0)
        reasoning = None
    else:
        inputs, outputs = count(raw.get("prompt_tokens")), count(raw.get("completion_tokens"))
        cached = count((raw.get("prompt_tokens_details") or {}).get("cached_tokens"))
        written = None
        reasoning = count((raw.get("completion_tokens_details") or {}).get("reasoning_tokens"))
    rates = (config.get("llm.input_cost_per_million"), config.get("llm.output_cost_per_million"))
    estimate = None
    if all(value is not None for value in (inputs, outputs, *rates)):
        estimate = (inputs * rates[0] + outputs * rates[1]) / 1_000_000
    return {"input_tokens": inputs, "output_tokens": outputs, "cached_input_tokens": cached,
            "cache_write_tokens": written, "reasoning_tokens": reasoning,
            "provider_reported_usage": inputs is not None and outputs is not None,
            "estimated_usage": False, "estimated_cost": estimate, "provider_reported_cost": None,
            "pricing_version": config.get("llm.pricing_version"), "pricing_source": "operator_configuration",
            "cost_method": "uncached_token_rates_conservative_estimate", "currency": config.get("llm.currency", "USD")}
