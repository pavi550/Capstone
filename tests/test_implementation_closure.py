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
