import hashlib
import hmac
import io
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from agentic_sdlc import (
    delivery_evidence,
    experiments,
    github_evidence,
    intake,
    lifecycle,
    remote_dev,
    scenarios,
)
from agentic_sdlc.approval import LocalApprovalAuthority
from agentic_sdlc.config import Config
from agentic_sdlc.provider_budget import normalize_usage, reserve
from agentic_sdlc.specifications import SpecificationStore, specification_digest


def store_run(tmp_path):
    store = SpecificationStore(tmp_path / 'state.db')
    return store, store.begin_intake('FIX-1')


def test_pre_intake_identity_survives_failure_and_duplicate_delivery(tmp_path):
    store = SpecificationStore(tmp_path / 'state.db')
    store.enqueue_issue('delivery', 'a' * 64, 'FIX-1')
    run = store.begin_intake('FIX-1', delivery_id='delivery')
    assert store.begin_intake('FIX-1', delivery_id='delivery') == run
    assert store.intake_queue()[0]['run_id'] == run
    with pytest.raises(ValueError, match='fixture failure'):
        lifecycle.execute(store, run, 'requirements', lambda: (_ for _ in ()).throw(ValueError('fixture failure')))
    draft = {'issue': 'FIX-1', 'status': 'BLOCKED', 'acceptance_criteria': []}
    draft['spec_sha256'] = specification_digest(draft)
    store.finish_intake(run, draft)
    assert store.intake_queue() == []
    assert store.lifecycle_stages(run)[0]['state'] == 'FAILED'
    with pytest.raises(ValueError, match='replace'):
        store.finish_intake(run, draft)


def test_parallel_child_journals_keep_parent_running(tmp_path):
    from contextvars import copy_context
    store, run = store_run(tmp_path)

    def parent():
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(copy_context().run, lifecycle.child, name, lambda: {'status': 'COMPLETED'})
                       for name in ('code', 'testcase')]
            for future in futures:
                future.result()
        assert next(r for r in store.lifecycle_stages(run) if r['stage'] == 'parallel')['state'] == 'RUNNING'
        return {}

    lifecycle.execute(store, run, 'parallel', parent)
    rows = store.lifecycle_stages(run)
    assert len(rows) == 3
    assert all(row['state'] == 'COMPLETED' for row in rows)


def test_shared_experiment_budget_atomic_across_runs(tmp_path):
    store, first = store_run(tmp_path)
    second = store.begin_intake('FIX-2')
    with closing(store._connect()) as connection, connection:
        connection.executemany('INSERT INTO experiment_runs VALUES (?, ?, ?)', [('exp', 'a', first), ('exp', 'b', second)])
    cfg = Config.load(overrides={'enterprise': {'budgets': {'experiment': {'max_total_tokens': 12}}}})

    def attempt(run):
        try:
            reserve((store, run, 'code', 1), cfg, 'code', 4, 4, (None, None))
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(attempt, (first, second))) == 1


def test_provider_usage_preserves_unknown_billing_and_cache_counts():
    config = Config.load(overrides={'llm': {'input_cost_per_million': 2, 'output_cost_per_million': 4}})
    usage = normalize_usage('anthropic', {'usage': {'input_tokens': 10, 'output_tokens': 2,
                            'cache_read_input_tokens': 3, 'cache_creation_input_tokens': 4}}, config)
    assert usage['input_tokens'] == 17
    assert usage['cached_input_tokens'] == 3
    assert usage['provider_reported_cost'] is None
    assert usage['estimated_cost'] == pytest.approx(42 / 1_000_000)
    assert normalize_usage('openai', {}, config)['input_tokens'] is None


def test_raw_measurements_reject_tampering_and_attempt_replay(tmp_path, monkeypatch):
    monkeypatch.setenv('SYNTHETIC_VERIFIER_KEY', 'v' * 32)
    cfg = Config.load(overrides={'enterprise': {'verifiers': {'verifier': 'SYNTHETIC_VERIFIER_KEY'}}})
    authority = LocalApprovalAuthority(cfg)
    raw = tmp_path / 'lint.json'
    raw.write_text('{"exit_code":0}')
    attachment = {'path': raw.name, 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}
    binding = {'run_id': 'a' * 32, 'manifest_sha256': 'b' * 64, 'spec_sha256': 'c' * 64}
    payload = {**binding, 'criterion_id': 'build_lint', 'attempt_id': 1, 'method': 'deterministic',
               'rubric_version': '1.0', 'source': 'LOCAL_ACTUAL', 'attachments': [attachment],
               'observations': [{'id': 'lint', 'expected': 0, 'observed': 0}]}
    record = {'actor': 'verifier', 'payload': payload,
              'mac': authority.seal('verifiers', 'criterion-source', 'verifier', payload)}
    source = tmp_path / 'observations.json'
    source.write_text(json.dumps(record))
    doc = {'raw_evidence': [{'path': source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]}
    result = delivery_evidence.collect_measurements(cfg, doc, tmp_path, binding, authority, 'verifier', 1)
    assert result['build_lint']['passed'] == 1
    with pytest.raises(ValueError, match='stale'):
        delivery_evidence.collect_measurements(cfg, doc, tmp_path, binding, authority, 'verifier', 2)
    raw.write_text('{"exit_code":1}')
    with pytest.raises(ValueError, match='digest'):
        delivery_evidence.collect_measurements(cfg, doc, tmp_path, binding, authority, 'verifier', 1)


def test_managed_trials_are_idempotent_and_preserve_approvals(tmp_path):
    directory, evaluator = tmp_path / 'trials', tmp_path / 'trusted'
    scenarios.materialize('bug-fix', directory, evaluator)
    store = SpecificationStore(tmp_path / 'state.db')
    first = experiments.schedule(store, directory)
    assert experiments.schedule(store, directory) == first
    assert len(first['trials']) == 3
    run = first['trials'][0]['run_id']
    assert store.get(run)['status'] == 'AWAITING_APPROVAL'
    with pytest.raises(ValueError, match='human'):
        experiments.execute_trial(store, directory, first['trials'][0]['id'], 'run',
                                  config_path=None, output=tmp_path / 'output')


def test_review_categories_are_executable_seeds(tmp_path):
    categories = {row[4] for row in scenarios.TASKS.values()}
    assert {'boundary', 'business_logic', 'validation', 'exception_handling', 'missing_authorization',
            'regression', 'missing_negative_tests', 'maintainability', 'resource_cleanup', 'concurrency'} <= categories
    for name, row in scenarios.TASKS.items():
        compile(row[2], name + '.py', 'exec')
    scenarios.materialize('authorization', tmp_path / 'trials', tmp_path / 'evaluator')
    findings = [{'id': 'f1', 'origin': 'ai', 'adjudication': 'confirmed',
                 'defect_id': 'authorization-01', 'severity': 'medium'}]
    result = scenarios.review_measurement(tmp_path / 'evaluator', findings)
    assert result['reviewers']['ai']['tp'] == 1
    assert result['reviewers']['ai']['severity_accuracy'] == 1


def test_ingress_health_and_authentication(tmp_path):
    store, _ = store_run(tmp_path)
    app = intake.relay_application(store, Config.load())
    status = []
    assert b'UP' in app({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/health'}, lambda s, h: status.append(s))[0]
    assert status == ['200 OK']
    status.clear()
    app({'REQUEST_METHOD': 'POST', 'PATH_INFO': '/jira', 'CONTENT_LENGTH': '999999999'}, lambda s, h: status.append(s))
    assert status == ['400 Bad Request']
    assert store.intake_queue() == []


def test_webhook_renewal_reconciles_and_respects_retry_interval(tmp_path, monkeypatch):
    store, _ = store_run(tmp_path)
    config = Config.load(overrides={'enterprise': {'intake': {'webhook': {'auto_renew': True,
            'allow_write': True, 'managed_ids': [5], 'renew_before_seconds': 100, 'renew_retry_seconds': 60}}}})
    calls = []

    def webhook(cfg, operation, **kwargs):
        calls.append(operation)
        return {'webhooks': [{'id': 5, 'expirationDate': 1_050_000}]} if operation == 'list' else {'expiration': 5_000_000}

    monkeypatch.setattr(intake, 'webhook', webhook)
    assert intake.renewal_tick(store, config, now=1000)['registrations'][0]['status'] == 'RENEWED'
    assert intake.renewal_tick(store, config, now=1001)['registrations'] == []
    assert calls == ['list', 'renew', 'list']


def test_broker_enforces_environment_authentication_and_idempotency(tmp_path, monkeypatch):
    monkeypatch.setenv('SYNTHETIC_BROKER_KEY', 'k' * 32)
    store, _ = store_run(tmp_path)
    cfg = Config.load(overrides={'enterprise': {'remote_dev': {'server': {'enabled': True,
        'token_env': 'SYNTHETIC_BROKER_KEY', 'environments': {'dev-test': {'adapter': 'docker'}}}}}})
    broker = remote_dev.Broker(store, cfg)
    binding = {'environment': 'dev-test', 'tier': 'DEV', 'manifest_sha256': 'a' * 64,
               'spec_sha256': 'b' * 64, 'review_sha256': 'c' * 64}
    deployment = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    first = broker.request('PUT', deployment, binding)
    assert broker.request('PUT', deployment, binding) == first
    assert broker.request('DELETE', deployment)['status'] == 'CANCELLED'
    with pytest.raises(ValueError, match='environment'):
        broker.request('PUT', deployment, {**binding, 'environment': 'prod'})
    status = []
    broker.application({'REQUEST_METHOD': 'PUT', 'PATH_INFO': '/deployments/' + deployment,
                       'wsgi.input': io.BytesIO(b'{}')}, lambda s, h: status.append(s))
    assert status == ['401 Unauthorized']


def test_ci_monitor_binds_exact_attempt_and_persists(tmp_path, monkeypatch):
    store, run = store_run(tmp_path)
    monkeypatch.setenv('SYNTHETIC_GITHUB_TOKEN', 'fixture')
    cfg = Config.load(overrides={'github': {'token_env': 'SYNTHETIC_GITHUB_TOKEN'},
                                'enterprise': {'delivery_policy': {'remote_ci': {'repository': 'owner/repo', 'workflow': 'trusted.yml'}}}})
    identity = {'id': 123, 'run_attempt': 2, 'head_sha': 'a' * 40, 'path': '.github/workflows/trusted.yml',
                'head_repository': {'full_name': 'owner/repo'}, 'status': 'completed'}
    monkeypatch.setattr(github_evidence, 'request_json', lambda *a, **k: identity)
    monkeypatch.setattr(github_evidence, 'collect', lambda *a: {'conclusion': 'success'})
    assert github_evidence.monitor(store, run, cfg, {'run_id': 123, 'attempt': 2, 'commit': 'a' * 40})['conclusion'] == 'success'
    identity['run_attempt'] = 3
    with pytest.raises(ValueError, match='identity'):
        github_evidence.monitor(store, run, cfg, {'run_id': 123, 'attempt': 2, 'commit': 'a' * 40})


def test_ci_signer_rejects_forged_success():
    identity = {'run_id': 1, 'manifest_sha256': 'a' * 64}
    checks = {name: 'PASSED' for name in ('acceptance', 'generated', 'regression', 'quality', 'security', 'definition_of_done', 'dev')}
    evidence = {'payload': {**identity, 'checks': checks}, 'records': [
        {**identity, 'check_id': name, 'result': 'PASSED', 'exit_code': 0} for name in checks]}
    result = delivery_evidence.sign_evaluation(evidence, identity, 's' * 32)
    expected = hmac.new(b's' * 32, json.dumps(result['payload'], sort_keys=True, separators=(',', ':')).encode(), hashlib.sha256).hexdigest()
    assert result['mac'] == expected
    evidence['records'][0]['exit_code'] = 1
    with pytest.raises(ValueError, match='inconsistent'):
        delivery_evidence.sign_evaluation(evidence, identity, 's' * 32)


def test_extended_guardrails_use_disposable_enforcement_boundaries(tmp_path):
    result = scenarios.evaluate_guardrails(Config.load(), tmp_path / 'probes', extended=True)
    assert len(result['observations']) == 24
    assert result['metrics']['aggregate']['runtime']['escaped'] == 0
    assert result['metrics']['aggregate']['runtime']['false_blocks'] == 0


def test_broker_accepts_actual_client_approval_binding(tmp_path):
    store, _ = store_run(tmp_path)
    cfg = Config.load(overrides={'enterprise': {'remote_dev': {'server': {
        'enabled': True, 'environments': {'dev-test': {'adapter': 'docker'}}}}}})
    broker = remote_dev.Broker(store, cfg)
    binding = {'environment': 'dev-test', 'tier': 'DEV', 'manifest_sha256': 'a' * 64,
               'spec_sha256': 'b' * 64, 'approval_sha256': 'c' * 64}
    deployment = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    assert broker.request('PUT', deployment, binding)['status'] == 'PENDING'


def test_kubernetes_adapter_owns_and_cleans_resources(tmp_path, monkeypatch):
    import shutil
    import subprocess
    from types import SimpleNamespace

    root = tmp_path / 'bundle'
    (root / 'integrated').mkdir(parents=True)
    (root / 'integrated' / 'app.py').write_text('VALUE = 1')
    resources, deleted = {}, []
    monkeypatch.setattr(shutil, 'which', lambda name: name)

    def command(argv, **kwargs):
        arguments = argv[argv.index('--request-timeout=10s') + 1:]
        if arguments[:2] == ['get', 'networkpolicies']:
            response = {'items': [{'spec': {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress']}}]}
        elif arguments[0] == 'create':
            response = json.loads(kwargs['input'])
            resources[response['kind'].lower()] = response
        elif arguments[0] == 'get':
            response = resources.get(arguments[1])
            if response and arguments[1] == 'job':
                response = {**response, 'status': {'succeeded': 1}}
        else:
            deleted.append(arguments[1])
            response = resources.pop(arguments[1])
        return SimpleNamespace(returncode=0, stdout=json.dumps(response) if response is not None else '')

    monkeypatch.setattr(subprocess, 'run', command)
    config = Config.load(overrides={'enterprise': {'container': {'commands': {'dev': ['/usr/bin/python3', '/opt/dev.py']}}}})
    result = remote_dev._kubernetes_dev(config, {'namespace': 'dev-test', 'context': 'isolated',
        'image': 'trusted/harness@sha256:' + 'a' * 64, 'kubeconfig': str(tmp_path / 'synthetic-config')},
        'b' * 64, root, {'manifest_sha256': 'c' * 64})
    assert result['result'] == 'PASSED'
    assert deleted == ['job', 'configmap']
    assert resources == {}


def test_broker_worker_connects_artifact_to_signed_receipt(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from agentic_sdlc import container_verifier
    from agentic_sdlc.development import _manifest

    monkeypatch.setenv('SYNTHETIC_RECEIPT_KEY', 's' * 32)
    root = tmp_path / 'bundle'
    (root / 'integrated').mkdir(parents=True)
    (root / 'integrated' / 'app.py').write_text('VALUE = 1')
    manifest = root / 'manifest.json'
    manifest.write_text(json.dumps({'baseline': {}, 'integrated': _manifest(root / 'integrated'), 'attempt': 1}))
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    harness = {'build': [['/usr/bin/true']], 'start': ['/usr/bin/server'],
               'health': {'url': 'http://127.0.0.1:8000'}, 'smoke': [{'url': 'http://127.0.0.1:8000'}]}
    commands = {'dev': ['/usr/bin/python3', '-I', '/opt/capstone/dev_harness.py', json.dumps(harness)]}
    environment = {'adapter': 'docker', 'configuration': {'enterprise': {'container': {'commands': commands}}}}
    server = {'enabled': True, 'receipt_key_env': 'SYNTHETIC_RECEIPT_KEY', 'artifacts': {digest: str(root)},
              'environments': {'dev-test': environment}}
    config = Config.load(overrides={'enterprise': {'remote_dev': {'server': server}}})
    calls = []

    def check(path, identity, expected):
        calls.append((path, identity, expected))
        return {'result': 'PASSED', 'exit_code': 0}

    monkeypatch.setattr(container_verifier, 'DockerVerifier', lambda cfg: SimpleNamespace(run_dev=check))
    store, _ = store_run(tmp_path)
    broker = remote_dev.Broker(store, config)
    binding = {'manifest_sha256': digest, 'spec_sha256': 'a' * 64, 'approval_sha256': 'b' * 64,
               'tier': 'DEV', 'environment': 'dev-test'}
    deployment = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    broker.request('PUT', deployment, binding)
    result = broker.work(deployment)
    assert result['status'] == 'PASSED'
    assert broker.work(deployment) == result
    assert len(calls) == 1
    mac = result.pop('mac')
    assert mac == hmac.new(b's' * 32, json.dumps(result, sort_keys=True).encode(), hashlib.sha256).hexdigest()


def test_recovery_distinguishes_active_lock_from_abandoned_stage(tmp_path):
    store, run = store_run(tmp_path)
    with closing(store._connect()) as connection, connection:
        connection.execute("INSERT INTO lifecycle_stages VALUES (?, 'validate', 1, ?, NULL, 'RUNNING', NULL, NULL)",
                           (run, "2026-01-01T00:00:00+00:00"))
    import sqlite3

    with closing(sqlite3.connect(lifecycle._lock_path(store, run))) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        assert lifecycle.recover(store)[0]["action"] == "WAIT_FOR_ACTIVE_WORKER"
        assert lifecycle.recovery_history(store, run) == []
    assert lifecycle.recover(store)[0]["action"] == "REQUIRE_OPERATOR_ACTION"
    assert lifecycle.recover(store) == []
    assert lifecycle.recovery_history(store, run)[0]["external_status"] == "UNCERTAIN"
    assert store.lifecycle_stages(run)[0]["state"] == "INTERRUPTED"
    assert store.lifecycle_stages(run)[0]["duration_seconds"] is None


def test_lifecycle_records_monotonic_duration_and_preserves_approval_wait(tmp_path):
    store, run = store_run(tmp_path)
    lifecycle.execute(store, run, "requirements", lambda: {"status": "AWAITING_APPROVAL"})
    assert lifecycle.recover(store) == []
    stage = store.lifecycle_stages(run)[0]
    assert stage["state"] == "WAITING_FOR_APPROVAL"
    assert stage["duration_seconds"] >= 0
    assert stage["worker_id"]


def test_parallel_checkpoint_reuses_only_intact_child_output(tmp_path):
    from types import SimpleNamespace

    from agentic_sdlc.development import _recoverable_generate

    store, run = store_run(tmp_path)
    calls = []

    def generate(work, workspace):
        calls.append(work)
        (workspace / "app.py").write_text("VALUE = 1", encoding="utf-8")
        return ["app.py"], []

    agent = SimpleNamespace(implement=generate)
    for attempt in range(3):
        workspace = tmp_path / str(attempt)
        workspace.mkdir()
        lifecycle.execute(store, run, "resume", lambda attempt=attempt, workspace=workspace: _recoverable_generate(
            agent, "implement", attempt, workspace, "same-inputs",
        ))
        assert (workspace / "app.py").read_text() == "VALUE = 1"
        if attempt == 1:
            (tmp_path / "0" / "app.py").write_text("tampered")
    assert calls == [0, 2]
    assert [row["artifact_integrity"] for row in lifecycle.recovery_history(store, run)] == ["VERIFIED", "INVALID"]


def test_stage_cancellation_propagates_to_child(tmp_path):
    store, run = store_run(tmp_path)

    def operation():
        result = lifecycle.cancel_stage(store, run, "resume", requester="operator", reason="fixture")
        assert result["status"] == "CANCELLING"
        lifecycle.child("implement", lambda: pytest.fail("Cancelled child executed"))

    with pytest.raises(lifecycle.StageCancelled):
        lifecycle.execute(store, run, "resume", operation)
    assert store.lifecycle_stages(run)[0]["state"] == "CANCELLED"
    with pytest.raises(lifecycle.StageCancelled):
        lifecycle.execute(store, run, "resume", operation)


def test_experiment_cancellation_propagates_to_registered_runs(tmp_path):
    directory = tmp_path / "trials"
    scenarios.materialize("bug-fix", directory, tmp_path / "trusted")
    store, _ = store_run(tmp_path)
    scheduled = experiments.schedule(store, directory)
    result = experiments.cancel_experiment(store, scheduled["experiment_id"])
    assert len(result["trials"]) == 3
    assert all(store.get(trial["run_id"])["cancelled_at"] for trial in result["trials"])


def test_intake_reuses_original_sources_and_rejects_changed_invocation(tmp_path, monkeypatch):
    from argparse import Namespace

    from agentic_sdlc import cli

    store, run = store_run(tmp_path)
    calls = []
    policy = Config.load(overrides={"jira": {"enabled": True, "base_url": "https://fixture.invalid"}})
    monkeypatch.setattr(cli.Config, "load", lambda *args, **kwargs: policy)

    class Jira:
        base = "https://fixture.invalid"

        def __init__(self, config):
            pass

        def get_issue(self, issue):
            calls.append(issue)
            return {"key": issue, "fields": {"summary": "Fixture", "description": "Fixture"}}

    monkeypatch.setattr(cli, "JiraClient", Jira)
    finish = SpecificationStore.finish_intake

    def fail(*args):
        raise KeyboardInterrupt

    args = Namespace(store=store.path, issue="FIX-1", intake_run_id=run, config=None,
                     page_id=None, criteria_file=None, allow_provider=False)
    monkeypatch.setattr(SpecificationStore, "finish_intake", fail)
    with pytest.raises(KeyboardInterrupt):
        cli._dev_spec(args)
    args.page_id = ["42"]
    with pytest.raises(ValueError, match="Intake inputs changed"):
        cli._dev_spec(args)
    args.page_id = None
    monkeypatch.setattr(SpecificationStore, "finish_intake", finish)
    assert cli._dev_spec(args)["run_id"] == run
    assert calls == ["FIX-1"]


def test_container_reconciliation_removes_only_owned_id(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from agentic_sdlc import container_verifier

    store, run = store_run(tmp_path)
    commands = []
    name = "capstone-verify-" + "b" * 32
    identity = "a" * 64

    def run_command(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=identity if len(commands) == 1 else "")

    monkeypatch.setattr(container_verifier.subprocess, "run", run_command)

    def interrupted():
        container_verifier._container_records(["docker"], {}, "daemon", register=name)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        lifecycle.execute(store, run, "testing", interrupted)
    lifecycle.execute(store, run, "testing", lambda: container_verifier._container_records(["docker"], {}, "daemon"))
    assert commands[1] == ["docker", "rm", "-f", identity]
    assert all(f"label=capstone.verifier={name}" in call for call in (commands[0], commands[2]))
    with closing(store._connect()) as conn:
        assert conn.execute("SELECT cleanup_state FROM lifecycle_containers").fetchone()[0] == "VERIFIED_ABSENT"


def test_cancelled_http_stream_checks_between_chunks(tmp_path, monkeypatch):
    from agentic_sdlc import httpclient

    store, run = store_run(tmp_path)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def read1(self, _size):
            store.cancel(run)
            return b'{"partial":'

    response = Response()
    monkeypatch.setattr(httpclient.urllib.request, "urlopen", lambda *_args, **_kwargs: response)
    with pytest.raises(lifecycle.StageCancelled):
        lifecycle.execute(store, run, "testing", lambda: httpclient.request_json("GET", "https://fixture.invalid"))
    assert response.closed
    assert store.lifecycle_stages(run)[0]["state"] == "CANCELLED"


def test_unresolved_container_blocks_cancellation_completion(tmp_path):
    store, run = store_run(tmp_path)

    def operation():
        with closing(store._connect()) as conn, conn:
            conn.execute("INSERT INTO lifecycle_containers VALUES (?, ?, 'testing', 1, 'fixture', 'PENDING')",
                         ("capstone-verify-" + "a" * 32, run))
        lifecycle.cancel_stage(store, run, "testing")
        lifecycle.checkpoint()

    with pytest.raises(lifecycle.StageCancelled):
        lifecycle.execute(store, run, "testing", operation)
    assert store.lifecycle_stages(run)[0]["state"] == "BLOCKED"
    with closing(store._connect()) as conn:
        assert conn.execute("SELECT completed_at FROM lifecycle_stage_cancellations").fetchone()[0] is None


def test_agent_timing_distinguishes_overlap_and_missing():
    assert lifecycle.timing_summary([])["agent_wall_seconds"] is None
    summary = lifecycle.timing_summary([
        {"stage": "run/1/implement", "started_at": "2026-01-01T01:00:00+00:00", "duration_seconds": 5},
        {"stage": "run/1/write_tests", "started_at": "2026-01-01T01:00:03+00:00", "duration_seconds": 6},
    ])
    assert summary == {"aggregate_agent_seconds": 11, "agent_wall_seconds": 9, "agent_overlap_seconds": 2}


def test_trusted_ci_rejects_tampered_raw_evidence(tmp_path, monkeypatch):
    from zipfile import ZipFile

    from agentic_sdlc.delivery_evidence import sign_evaluation, verify_evaluator_archive

    key = "s" * 32
    monkeypatch.setenv("SYNTHETIC_CI_KEY", key)
    config = Config.load(overrides={"enterprise": {"delivery_policy": {"remote_ci": {
        "evaluator_key_env": "SYNTHETIC_CI_KEY",
    }}}})
    identity = dict.fromkeys(("run_id", "attempt", "commit", "repository", "workflow", "manifest_sha256",
                             "spec_sha256", "evaluator_revision"), "fixture")
    checks = dict.fromkeys(("acceptance", "generated", "regression", "quality", "security", "definition_of_done", "dev"),
                           "PASSED")
    evaluation = {"payload": {**identity, "checks": checks}, "records": [
        {**identity, "check_id": name, "result": "PASSED", "exit_code": 0} for name in checks]}
    signed = sign_evaluation(evaluation, identity, key)
    archive = tmp_path / "ci.zip"
    for changed in (False, True):
        with ZipFile(archive, "w") as bundle:
            bundle.writestr("attestation.json", json.dumps(signed))
            bundle.writestr("raw-evidence.json", json.dumps({**evaluation, "tampered": changed} if changed else evaluation))
        policy = {"required": True, "checks": ["acceptance"]}
        if changed:
            with pytest.raises(ValueError, match="differs"):
                verify_evaluator_archive(config, policy, archive, identity)
        else:
            assert verify_evaluator_archive(config, policy, archive, identity) == signed["payload"]


def test_independent_ci_review_uses_only_reference_labels():
    from agentic_sdlc.delivery_evidence import evaluate_review

    binding = {"spec_sha256": "a" * 64, "manifest_sha256": "b" * 64}
    candidate = {**binding, "findings": [
        {"id": "confirmed", "origin": "ai"},
        {"id": "unknown", "origin": "ai", "adjudication": "incorrect"},
    ]}
    canonical = json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
    reference = {**binding, "findings_sha256": hashlib.sha256(canonical).hexdigest(),
                 "defects": ["D1", "D2"], "labels": {"confirmed": {"adjudication": "confirmed", "defect_id": "D1"}}}
    result = evaluate_review(candidate, reference, binding)["metrics"]["ai"]
    assert (result["tp"], result["fp"], result["fn"], result["unverified"]) == (1, 0, 1, 1)
    assert result["recall"] == 0.5
    with pytest.raises(ValueError, match="different review findings"):
        evaluate_review({**candidate, "findings": []}, reference, binding)


def test_admin_webhook_authenticates_body_tenant_and_deduplicates(tmp_path, monkeypatch):
    from agentic_sdlc.intake import accept_admin

    secret = "fixture-secret" * 4
    monkeypatch.setenv("TEST_ADMIN_HMAC", secret)
    config = Config.load(overrides={"enterprise": {"intake": {"projects": ["FIX"], "direct": {
        "mode": "jira_cloud_admin_hmac", "tenant": "fixture.atlassian.net", "secret_env": "TEST_ADMIN_HMAC",
    }}}})
    store, _ = store_run(tmp_path)
    event = {"webhookEvent": "jira:issue_created", "issue": {"key": "FIX-42",
             "self": "https://fixture.atlassian.net/rest/api/3/issue/42", "fields": {"project": {"key": "FIX"}}}}
    body = json.dumps(event).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    with pytest.raises(ValueError, match="signature"):
        accept_admin(store, config, body, "")
    accept_admin(store, config, body, signature)
    accept_admin(store, config, body, signature)
    assert len(store.intake_queue()) == 1
    changed = body.replace(b"fixture.atlassian.net", b"other.atlassian.net")
    with pytest.raises(ValueError, match="tenant"):
        accept_admin(store, config, changed, "sha256=" + hmac.new(secret.encode(), changed, hashlib.sha256).hexdigest())


def test_ci_dispatch_uses_returned_workflow_id(monkeypatch):
    from agentic_sdlc import github_evidence

    monkeypatch.setenv("TEST_DISPATCH_TOKEN", "fixture")
    config = Config.load(overrides={"github": {"allow_dispatch": True, "dispatch_refs": ["main"],
                                               "token_env": "TEST_DISPATCH_TOKEN"}})

    def request(method, url, *, headers, body):
        assert method == "POST"
        assert url.endswith("/trusted-evaluator.yml/dispatches")
        assert body["return_run_details"] is True
        return {"workflow_run_id": 719}

    monkeypatch.setattr(github_evidence, "request_json", request)
    assert github_evidence.dispatch(config, "owner/repo", "trusted-evaluator.yml", "main", {}, exact=True) == {
        "run_id": 719, "attempt": 1,
    }


def test_report_exports_same_missing_measurements(tmp_path):
    from agentic_sdlc.experiments import export_report

    report = {"trials": [{"id": "trial", "mode": "AGENT_DRIVEN", "measurement": None}],
              "comparisons": [{"trial": "trial", "comparison": None}]}
    output = tmp_path / "report"
    export_report(output, report)
    assert json.loads((output / "experiment.json").read_text(encoding="utf-8")) == report
    assert "trial,AGENT_DRIVEN,," in (output / "experiment.csv").read_text(encoding="utf-8")
    assert "Unavailable" in (output / "experiment.md").read_text(encoding="utf-8")


def test_workflow_start_reuses_approved_specification(tmp_path, monkeypatch, capsys):
    from agentic_sdlc import cli, orchestrator

    monkeypatch.setenv("SPEC_APPROVER_KEY", "approved-specification-fixture-key")
    cfg = Config.load(overrides={"enterprise": {"approvers": {"reviewer": "SPEC_APPROVER_KEY"}}})
    store = SpecificationStore(tmp_path / "workflow.db")
    draft = {"issue": "FIX-9", "title": "Fixture", "description": "Reused specification",
             "acceptance_criteria": [{"text": "The result is observable"}], "status": "AWAITING_APPROVAL"}
    draft["spec_sha256"] = specification_digest(draft)
    run_id = store.create(draft)
    store.decide(run_id, draft["spec_sha256"], "APPROVED", "reviewer", None, LocalApprovalAuthority(cfg))
    config_path = tmp_path / "policy.yaml"
    config_path.write_text("fixture", encoding="utf-8")
    captured = {}

    def continue_run(_store, selected_run, plan, **_kwargs):
        captured.update(run_id=selected_run, plan=plan)
        return {"run_id": selected_run, "stage": "development", "status": "WAITING_FOR_APPROVAL"}

    monkeypatch.setattr(orchestrator, "continue_enterprise", continue_run)
    args = [
        "dev", "workflow-start", "--run-id", run_id, "--store", str(store.path),
        "--config", str(config_path), "--repo", str(tmp_path / "source"),
        "--out", str(tmp_path / "output"), "--acceptance-dir", str(tmp_path / "acceptance"),
        "--actor", "verifier",
    ]
    assert cli.main(args) == 0
    capsys.readouterr()
    assert captured["run_id"] == run_id
    assert captured["plan"]["issue"] == "FIX-9"
    assert cli.main(args) == 2
    assert "use workflow-resume" in capsys.readouterr().err


def test_unified_workflow_pauses_at_approval_gates_and_orders_ci(tmp_path, monkeypatch):
    from agentic_sdlc import cli, orchestrator, verification

    store, run_id = store_run(tmp_path)
    cfg = Config.load(overrides={"enterprise": {"delivery_policy": {"remote_ci": {"required": True}}}})
    monkeypatch.setattr(Config, "load", lambda *_args, **_kwargs: cfg)
    with closing(store._connect()) as db, db:
        db.execute("INSERT INTO unified_runs(run_id, plan) VALUES (?, '{}')", (run_id,))
    plan = {"issue": "FIX-1", "mode": "agent-driven", "config": "fixture",
            "repo": str(tmp_path), "out": str(tmp_path / "out"),
            "acceptance": str(tmp_path), "verifier": "fixture"}
    draft = {"issue": "FIX-1", "status": "AWAITING_APPROVAL", "acceptance_criteria": [{"text": "fixture"}]}
    from agentic_sdlc.specifications import specification_digest

    draft["spec_sha256"] = specification_digest(draft)
    calls = []
    receipts = {}

    def intake_stage(_args):
        calls.append("requirements")
        store.finish_intake(run_id, draft)
        return {"status": "AWAITING_APPROVAL"}

    def generate(_args):
        if "generation" not in calls:
            calls.append("generation")
        return {
            "status": "READY_FOR_VERIFICATION", "manifest_sha256": "a" * 64,
            "artifact_dir": str(tmp_path / "out"), "code_files": ["app.py"],
            "test_files": ["tests/test_app.py"], "timings_ms": {},
        }

    def verify(args):
        stage = args.dev_command
        calls.append(stage)
        if stage == "testing":
            receipts["actual_verification"] = {"payload": {"status": "PASSED"}}
            return receipts["actual_verification"]["payload"]
        if stage == "review":
            receipts["review"] = {"status": "WAITING_REVIEW_APPROVAL"}
            return receipts["review"]
        assert stage == "validate"
        return {"status": "LOCAL_DEV_VALIDATED"}

    def ci_submit(*_args, include_review, authorized):
        assert authorized
        key = "ci_review_evidence" if include_review else "ci_evidence"
        calls.append(key)
        receipts[key] = {"status": "PASSED"}
        return receipts[key]

    monkeypatch.setattr(cli, "_dev_spec", intake_stage)
    monkeypatch.setattr(cli, "_dev_run", generate)
    monkeypatch.setattr(cli, "_dev_verification", verify)
    monkeypatch.setattr(cli, "_dev_report", lambda _args: {"status": "COMPLETED"})
    monkeypatch.setattr(verification, "submit_ci", ci_submit)
    monkeypatch.setattr(verification, "require_development_approval", lambda *_args: True)
    monkeypatch.setattr(verification, "require_quality_approval", lambda *_args: True)
    monkeypatch.setattr(store, "receipt", lambda _run, kind, **_kwargs: receipts.get(kind))
    monkeypatch.setattr(store, "approved_specification", lambda *_args: draft)

    first = orchestrator.continue_enterprise(store, run_id, plan, allow_ci=True)
    assert first["stage"] == "requirements"
    assert first["status"] == "WAITING_FOR_APPROVAL"
    assert calls == ["requirements"]
    with closing(store._connect()) as db, db:
        db.execute("UPDATE specifications SET status='APPROVED' WHERE run_id=?", (run_id,))
    before_approval = orchestrator.continue_enterprise(store, run_id, plan, allow_ci=True)
    assert before_approval["stage"] == "development"
    assert before_approval["status"] == "WAITING_FOR_APPROVAL"
    receipts["development_approval"] = {"payload": {"status": "APPROVED"}}
    quality_wait = orchestrator.continue_enterprise(store, run_id, plan, allow_ci=True)
    assert quality_wait["stage"] == "quality"
    assert quality_wait["status"] == "WAITING_FOR_APPROVAL"
    receipts["quality_approval"] = {"payload": {"status": "APPROVED"}}
    before_ci = orchestrator.continue_enterprise(store, run_id, plan)
    assert before_ci["stage"] == "ci-initial"
    assert before_ci["status"] == "PAUSED"
    before_review = orchestrator.continue_enterprise(store, run_id, plan, allow_ci=True)
    assert before_review["stage"] == "review"
    assert before_review["status"] == "WAITING_FOR_APPROVAL"
    assert calls == ["requirements", "generation", "testing", "ci_evidence", "review", "ci_review_evidence"]
    receipts["review_approval"] = {"status": "APPROVED"}
    result = orchestrator.continue_enterprise(store, run_id, plan)
    assert result["status"] == "LOCAL_DEV_VALIDATED"
    assert calls[-1] == "validate"
    assert calls.count("testing") == 1


def test_ci_timing_keeps_unavailable_rerun_queue_explicit():
    from agentic_sdlc.github_evidence import execution_timing

    run = {"created_at": "2026-01-01T10:00:00Z", "run_started_at": "2026-01-01T10:02:00Z"}
    jobs = [{"completed_at": "2026-01-01T10:05:00Z"}, {"completed_at": "2026-01-01T10:06:00Z"}]
    assert execution_timing(run, jobs, 1)["queue_seconds"] == 120
    assert execution_timing(run, jobs, 1)["execution_seconds"] == 240
    assert execution_timing(run, jobs, 2)["queue_seconds"] is None
    assert execution_timing(run, [{"completed_at": None}], 1)["execution_seconds"] is None


def test_intake_drain_respects_existing_worker_lock(tmp_path):
    import sqlite3
    from argparse import Namespace

    from agentic_sdlc.cli import _drain_intake

    store, _ = store_run(tmp_path)
    lock_path = store.path.with_name(f".{store.path.name}.intake-worker.lock")
    with closing(sqlite3.connect(lock_path)) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        assert _drain_intake(store, Namespace())["status"] == "ALREADY_RUNNING"


def test_ci_dispatch_recovery_matches_exact_correlation_and_rejects_ambiguity(monkeypatch):
    from agentic_sdlc import github_evidence

    monkeypatch.setenv("TEST_RECONCILE_TOKEN", "fixture")
    cfg = Config.load(overrides={"github": {"token_env": "TEST_RECONCILE_TOKEN"}})
    commit, correlation = "a" * 40, "b" * 64
    run = {"id": 8, "head_sha": commit, "display_title": correlation, "event": "workflow_dispatch",
           "head_repository": {"full_name": "owner/repo"}, "path": ".github/workflows/evaluator.yml@main"}
    runs = [{**run, "id": 9, "display_title": "unrelated"}, run]

    def request(method, *_args, **_kwargs):
        assert method == "GET"
        return {"workflow_runs": runs, "total_count": len(runs)}

    monkeypatch.setattr(github_evidence, "request_json", request)
    assert github_evidence.reconcile_dispatch(cfg, "owner/repo", "evaluator.yml", commit, correlation) == {
        "run_id": 8, "attempt": 1,
    }
    runs.append({**run, "id": 10})
    assert github_evidence.reconcile_dispatch(cfg, "owner/repo", "evaluator.yml", commit, correlation) is None


def test_abandoned_provider_reservation_remains_charged(tmp_path):
    store, run = store_run(tmp_path)
    cfg = Config.load()
    with closing(store._connect()) as conn, conn:
        conn.execute("INSERT INTO lifecycle_stages VALUES (?, 'run/1/implement', 1, ?, NULL, 'RUNNING', NULL, NULL)",
                     (run, "2026-01-01T00:00:00+00:00"))
    reserve((store, run, "run/1/implement", 1), cfg, "code", 12, 7, (None, None))
    lifecycle.recover(store)
    request = store.provider_requests(run)[0]
    assert request["state"] == "INTERRUPTED_UNKNOWN"
    assert request["charged_input"] == 12
    assert request["charged_output"] == 7


def test_blocked_http_header_observation_honors_cancel(tmp_path, monkeypatch):
    from threading import Event

    from agentic_sdlc import httpclient

    store, run = store_run(tmp_path)
    entered, release = Event(), Event()

    def blocked(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        raise TimeoutError("fixture released")

    monkeypatch.setattr(httpclient.urllib.request, "urlopen", blocked)

    def request():
        with ThreadPoolExecutor(1) as pool:
            def cancel():
                assert entered.wait(5)
                store.cancel(run)

            pending = pool.submit(cancel)
            try:
                return httpclient.request_json("GET", "https://fixture.invalid")
            finally:
                pending.result()
                release.set()

    with pytest.raises(lifecycle.StageCancelled):
        lifecycle.execute(store, run, "testing", request)
