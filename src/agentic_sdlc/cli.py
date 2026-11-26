"""Command line: `py -m agentic_sdlc run ...` and `py -m agentic_sdlc benchmark ...`."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from .approval import LocalApprovalAuthority
from .config import Config
from .confluence import ConfluenceClient, linked_page_ids
from .evaluation import compare, score
from .events import summarize
from .jira import JiraClient, fetch_requirements, issue_to_requirement
from .metrics import benchmark, guardrail_breakdown, guardrail_effectiveness, independently_reviewed
from .orchestrator import END, Orchestrator
from .specifications import SpecificationStore, specification_digest


def _run(args: argparse.Namespace) -> int:
    overrides: dict = {}
    if args.llm:
        overrides.setdefault("llm", {})["provider"] = args.llm
    if args.interactive:
        overrides.setdefault("hitl", {})["mode"] = "interactive"
    if args.vcs:
        overrides.setdefault("vcs", {})["mode"] = args.vcs
    cfg = Config.load(args.config, overrides)
    repo = Path(args.repo).resolve() if args.repo else None
    state = Orchestrator(cfg).run([Path(p) for p in args.requirements], repo,
                                  Path(args.out) if args.out else None)
    final = state.history[-1]
    print(f"run {state.run_id}: finished at '{final}'")
    if state.concern:
        print(f"concern: {state.concern}")
    for w in state.work.values():
        score = f"{w.score:.2f}" if w.score is not None else "-"
        print(f"  {w.requirement.id:<12} {w.status.value:<14} score={score} "
              f"fix_iter={w.fix_iterations} review_iter={w.review_iterations}")
    for key, path in state.outputs.items():
        print(f"  {key}: {path}")
    return 0 if final == END else 2


def _benchmark(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    runs = Path(args.runs or cfg.get("project.output_dir"))
    result = benchmark(runs, Path(args.dataset) if args.dataset else None)
    print(f"items: {result['items']}")
    for reviewer, m in result["reviewers"].items():
        print(f"  {reviewer:<6} accuracy={m['accuracy']:.3f} precision={m['precision']:.3f} "
              f"recall={m['recall']:.3f} f1={m['f1']:.3f}")
    if args.json:
        print(json.dumps(result, indent=2))
    return 0


def _read_object(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("Expected a JSON object")
    return data


def _write_score(directory: Path, result: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "score.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with (directory / "score.csv").open("w", encoding="utf-8", newline="") as stream:
        columns = ("stage", "id", "maximum", "earned", "status")
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in result["criteria"]:
            # Prevent spreadsheet editors from interpreting rubric labels as formulas.
            writer.writerow({
                column: "'" + value if isinstance(value, str) and value.lstrip().startswith(
                    ("=", "+", "-", "@"),
                ) else value
                for column, value in row.items() if column in columns
            })
    lines = [
        "# SDLC score", "",
        f"Score: {result['earned_points']}/{result['configured_maximum']}",
        f"Evaluated maximum: {result['evaluated_maximum']}",
        f"Status: {result['status']}",
        f"Delivery eligibility: {result['delivery_eligibility']}",
    ]
    (directory / "score.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dev_registry(args):
    from .delivery_evidence import registry

    return {"criteria": registry(Config.load(args.config))}


def _dev_score(args):
    cfg = Config.load(args.config)
    rubric = _effective_rubric(cfg) if args.store else cfg.get("evaluation.rubric")
    if args.store:
        if not args.run_id or not args.config or not args.report_dir or args.evidence_dir:
            raise ValueError("Lifecycle scoring needs --store, --run-id, --config and --report-dir")
        assessment = _lifecycle_assessment(SpecificationStore(args.store), args.run_id, cfg, args.report_dir)
        root = args.report_dir
    else:
        if not args.evidence_dir or args.run_id:
            raise ValueError("An independent assessment requires --evidence-dir")
        assessment = _read_object(args.assessment)
        root = args.evidence_dir
    result = _score_snapshot(rubric, assessment, root)
    if args.report_dir:
        _write_score(args.report_dir, result)
    return result


def _score_snapshot(rubric, assessment, root):
    result = score(rubric, assessment, root)
    binding = assessment.get("binding")
    if binding:
        stamp = datetime.now(UTC).isoformat()
        for row in result["criteria"]:
            row.update(**binding, criterion_id=row["id"], earned_points=row["earned"], maximum_points=row["maximum"],
                       evaluation_status=row["status"], evidence_references=row["evidence"],
                       deduction_reason=row["reason"] if row["status"] != "PASSED" else None, evaluated_at=stamp)
    return result


def _effective_rubric(cfg):
    rubric = cfg.get("evaluation.rubric")
    if cfg.get("enterprise.delivery_policy.remote_ci.required", False):
        return {**rubric, "mandatory_gates": list(dict.fromkeys([*rubric["mandatory_gates"], "remote_ci"]))}
    return rubric


def _lifecycle_assessment(store, run_id, cfg, directory, *, include_final=True):
    from .verification import scoring_inputs

    facts, checks, gates = scoring_inputs(
        store, run_id, LocalApprovalAuthority(cfg), include_final=include_final, config=cfg,
    )
    filename = "lifecycle-evidence.json"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    serialized = json.dumps(facts, indent=2, sort_keys=True).encode()
    if path.exists():
        if path.read_bytes() != serialized:
            raise ValueError("Use a new report directory for a different lifecycle evidence snapshot")
    else:
        with path.open("xb") as stream:
            stream.write(serialized)
    ref = [{"path": filename, "sha256": hashlib.sha256(serialized).hexdigest()}]
    review = (facts.get("ci_review_evidence") or facts.get("ci_evidence", {})).get("evaluator", {}).get("review_evaluation") or {}
    review = review or facts.get("local_review_evidence", {})
    review_metrics = review.get("metrics", {}).get("ai", {})
    automatic = {
        name: {"ratio": review_metrics[metric], "method": "signed independent defect adjudication",
               "reason": "Recorded independent review evaluation", "evidence": ref}
        for name, metric in (("defect_detection", "recall"), ("finding_accuracy", "precision"))
        if review_metrics.get(metric) is not None
    }
    return {
        "evaluator_version": "lifecycle-receipts-v1",
        "binding": {"run_id": run_id, "attempt_id": facts["attempt_id"], "artifact_digest": facts["manifest_sha256"]},
        "criteria": {
            **automatic,
            **{name: {"ratio": row["passed"] / row["measured"], "method": row["method"],
                       "reason": row["reason"], "evidence": ref}
               for name, row in facts.get("criterion_evidence", {}).get("criteria", {}).items()},
            **{name: {
                "ratio": int(checks[check]), "method": "independent command exit status",
                "reason": "Recorded independent check result", "evidence": ref,
            }
            for name, check in (("regression", "regression"), ("definition_of_done", "definition_of_done"),
                                ("traceability", "ci_traceability"))
            if check in checks},
        },
        "gates": {name: {"passed": passed, "evidence": ref} for name, passed in gates.items()},
    }


def _capture_lifecycle_score(store, run_id, cfg, authority, phase):
    kind = f"{phase}_score"
    previous = store.receipt(run_id, kind, optional=True)
    if previous:
        return previous
    _, evidence = store.verified_development(run_id, authority)
    directory = Path(evidence["artifact_dir"]) / "scores" / phase
    assessment = _lifecycle_assessment(store, run_id, cfg, directory, include_final=phase == "final")
    result = _score_snapshot(_effective_rubric(cfg), assessment, directory)
    _write_score(directory, result)
    return store.record_receipt(run_id, kind, result, manifest=evidence["manifest_sha256"])


def _dev_experiment(args):
    from . import experiments

    if args.dev_command == "experiment-cancel":
        return experiments.cancel_experiment(SpecificationStore(args.store), args.experiment_id)
    if args.dev_command == "experiment-schedule":
        return experiments.schedule(SpecificationStore(args.store), args.directory)
    if args.dev_command == "experiment-trial":
        return experiments.execute_trial(SpecificationStore(args.store), args.directory, args.trial, args.operation,
                                         config_path=args.config, output=args.out, acceptance=args.acceptance_dir,
                                         actor=args.actor, allow_provider=args.allow_provider)
    if args.dev_command == "experiment-guardrails":
        from .scenarios import evaluate_guardrails

        return evaluate_guardrails(Config.load(args.config), args.directory, extended=True)
    if args.dev_command == "experiment-seed":
        from .scenarios import materialize

        return materialize(args.scenario, args.directory, args.evaluator, repetitions=args.repetitions)
    if args.dev_command == "experiment-prepare":
        return experiments.prepare(_read_object(args.plan), args.repo, args.directory)
    if args.dev_command == "experiment-baseline":
        return experiments.submit_baseline(args.directory, args.trial, _read_object(args.measurements),
                                           args.deliverable, LocalApprovalAuthority(Config.load(args.config)))
    if args.dev_command == "experiment-record":
        return experiments.record(args.directory, args.trial, _read_object(args.measurements))
    result = experiments.summarize(args.directory, Config.load(args.config).get("evaluation.index_weights"))
    if args.report_dir:
        experiments.export_report(args.report_dir, result)
    return result


def _dev_compare(args):
    cfg = Config.load(args.config)
    actual = _read_object(args.actual)
    if args.store:
        record = SpecificationStore(args.store).get(args.run_id)
        if actual.get("task_id") != record["draft"]["issue"] or actual.get("spec_sha256") != record["digest"]:
            raise ValueError("Actual comparison measurements must match the stored issue and specification digest")
    return compare(_read_object(args.baseline), actual, cfg.get("evaluation.index_weights"))


def _dev_backlog(args):
    cfg = Config.load(args.config, enterprise_intake=True)
    if not cfg.get("jira.issues_file") and not (cfg.get("jira.enabled") and cfg.get("jira.jql")):
        raise ValueError("Set JIRA_BASE_URL and JIRA_JQL in .env, or configure a Jira fixture")
    if args.summary and not cfg.get("jira.issues_file"):
        jira = JiraClient(cfg)
        issues = jira.search(cfg.get("jira.jql"), cfg.get("jira.max_results"))
        summaries = []
        for issue in issues:
            req = issue_to_requirement(issue)
            fields = issue["fields"]
            confluence_base = cfg.get("confluence.base_url")
            page_ids = linked_page_ids(confluence_base, jira.documentation_links(issue)) if confluence_base else None
            summaries.append({
                "key": req.id, "summary": req.title, "type": (fields.get("issuetype") or {}).get("name"),
                "priority": (fields.get("priority") or {}).get("name"),
                "criteria_count": len(req.acceptance_criteria),
                "readiness": "READY_FOR_REQUIREMENT_REVIEW" if req.acceptance_criteria else "MISSING_ACCEPTANCE_CRITERIA",
                "linked_confluence_pages": len(page_ids) if page_ids is not None else None,
            })
        return {"issues": summaries}
    return {"requirements": [item.to_dict() for item in fetch_requirements(cfg)]}


def _dev_spec(args):
    resume_id = getattr(args, "intake_run_id", None)
    if not args.store:
        if resume_id:
            raise ValueError("Intake resume requires --store")
        return _generate_spec(args)
    from .lifecycle import execute

    store = SpecificationStore(args.store)
    run_id = resume_id or store.begin_intake(args.issue, delivery_id=getattr(args, "delivery_id", None))

    def generate():
        existing = store.get(run_id)["draft"]
        if existing["issue"] != args.issue:
            raise ValueError("Run does not belong to the selected issue")
        if not existing.get("intake_pending"):
            return {"run_id": run_id, "draft": existing}
        return _generate_spec(args, run_id)

    return execute(store, run_id, "requirements", generate)


def _generate_spec(args, intake_run=None):
    from contextlib import closing

    from .lifecycle import checkpoint_digest, child, completed_output
    started_at, started = datetime.now(UTC), perf_counter()
    cfg = Config.load(args.config, enterprise_intake=True, local_model=args.allow_provider)
    criteria_document = _read_object(args.criteria_file) if args.criteria_file else None
    fixture = cfg.get("jira.issues_file")
    fixture_digest = hashlib.sha256(Path(fixture).read_bytes()).hexdigest() if fixture and intake_run else None
    source_identity = checkpoint_digest({"issue": args.issue, "pages": args.page_id or [],
                                         "criteria": criteria_document, "fixture": fixture_digest,
                                         "provider": args.allow_provider, "configuration": cfg.data})
    if intake_run:
        store = SpecificationStore(args.store)
        with closing(store._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT OR IGNORE INTO intake_invocations VALUES (?, ?)", (intake_run, source_identity))
            bound = connection.execute("SELECT input_digest FROM intake_invocations WHERE run_id=?",
                                       (intake_run,)).fetchone()[0]
            if bound != source_identity:
                raise ValueError("Intake inputs changed. Create a new intake to retrieve a new source snapshot.")

    def source(name, retrieve):
        return child(name, lambda: completed_output("intake-" + name, source_identity, retrieve, lambda _: True))

    if args.allow_provider:
        from .development import agent_model_config, validate_provider

        cfg_for_requirement = agent_model_config(cfg, "requirement")
        validate_provider(cfg_for_requirement, True)
        if cfg_for_requirement.get("llm.provider") == "stub":
            raise ValueError("Requirement analysis requires an approved configured model")
    issue = None
    if cfg.get("jira.issues_file"):
        requirement = next(
            (item for item in fetch_requirements(cfg) if item.id == args.issue), None,
        )
        if requirement is None:
            raise ValueError("Issue not found in the configured Jira fixture")
    elif cfg.get("jira.enabled"):
        jira = JiraClient(cfg)
        issue = source("jira", lambda: jira.get_issue(args.issue))
        requirement = issue_to_requirement(issue)
    else:
        raise ValueError("Set jira.issues_file or enable and configure Jira")
    pages = []
    if args.page_id:
        client = ConfluenceClient(cfg)
        for page_id in dict.fromkeys(args.page_id):
            pages.append(source("confluence-" + page_id, lambda page_id=page_id: client.read_page(page_id)))
    elif issue is not None and cfg.get("confluence.base_url"):
        client = ConfluenceClient(cfg)
        pages = source("confluence", lambda: client.issue_pages(args.issue, jira.documentation_links(issue), title=requirement.title))
    if args.criteria_file:
        if requirement.acceptance_criteria:
            raise ValueError("Operator criteria are only supported when Jira has no explicit criteria")
        document = criteria_document
        criteria = document.get("acceptance_criteria")
        if (document.get("issue") != requirement.id or not isinstance(criteria, list) or not 1 <= len(criteria) <= 20
                or any(not isinstance(text, str) or not text.strip() or len(text) > 1000 for text in criteria)
                or len(set(criteria)) != len(criteria)):
            raise ValueError("Provide the issue key and one to twenty distinct operator acceptance criteria")
        requirement.acceptance_criteria = criteria
    draft = {
        "schema_version": 1, "issue": requirement.id, "source": requirement.source,
        "title": requirement.title, "description": requirement.description,
        "acceptance_criteria": [
            {"id": f"{requirement.id}-AC-{index}", "text": criterion}
            for index, criterion in enumerate(requirement.acceptance_criteria, 1)
        ],
        "confluence": [{k: page[k] for k in ("id", "version", "url", "sha256", "truncated")} for page in pages],
        "status": "AWAITING_APPROVAL" if requirement.acceptance_criteria else "BLOCKED",
    }
    if args.criteria_file:
        draft["criteria_source"] = {"origin": "operator", "sha256": hashlib.sha256(
            json.dumps(document, sort_keys=True).encode(),
        ).hexdigest()}
    if args.allow_provider:
        from .agents.requirements import RequirementAgent
        from .llm import make_llm

        def analyze():
            model = make_llm(cfg_for_requirement)
            return {"analysis": RequirementAgent(model).analyze(draft, pages), "provider_usage": model.usage()}

        identity = checkpoint_digest({"draft": draft, "pages": pages, "jira": issue, "policy": cfg_for_requirement.data})
        analysis = child("analysis", lambda: completed_output("requirement-analysis", identity, analyze, lambda _: True))
        draft.update(analysis)
        if draft["analysis"]["missing_mandatory"]:
            draft["status"] = "BLOCKED"
    if issue is not None:
        draft["jira_revision"] = issue.get("fields", {}).get("updated")
        draft["jira_sha256"] = hashlib.sha256(json.dumps(issue, sort_keys=True).encode()).hexdigest()
        draft["jira_url"] = f"{jira.base}/browse/{requirement.id}"
        criterion_source = draft["criteria_source"] if args.criteria_file else {"url": draft["jira_url"]}
        draft["traceability"] = {criterion["id"]: criterion_source for criterion in draft["acceptance_criteria"]}
    if issue is not None or args.allow_provider:
        draft["timing"] = {"started_at": started_at.isoformat(), "ended_at": datetime.now(UTC).isoformat(),
                           "duration_ms": round((perf_counter() - started) * 1000, 3)}
    draft["spec_sha256"] = specification_digest(draft)
    if args.store:
        store = SpecificationStore(args.store)
        run_id = store.finish_intake(intake_run, draft) if intake_run else store.create(
            draft, delivery_id=getattr(args, "delivery_id", None))
        return {"run_id": run_id, "draft": store.get(run_id)["draft"]}
    if args.output:
        _save_new_json(args.output, draft)
    return draft


def _save_new_json(path: Path, data: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)


def _dev_context(args):
    cfg = Config.load(args.config, enterprise_intake=True)
    client = ConfluenceClient(cfg)
    if args.output and (not args.page_id or not args.page_id.isascii() or not args.page_id.isdigit()):
        raise ValueError("--output requires a numeric Confluence page ID")
    if args.spaces:
        rows, truncated = client.spaces()
        return {"spaces": [{"id": row["id"], "key": row["key"]} for row in rows], "truncated": truncated}
    if args.space:
        spaces, _ = client.spaces(args.space)
        match = next((space for space in spaces if space["key"] == args.space), None)
        if match is None:
            raise ValueError("No accessible Confluence space matches the key")
        rows, truncated = client.pages(match["id"])
    elif args.search:
        rows, truncated = client.search(args.search)
    elif args.page_id and not args.page_id.isdigit():
        jira = JiraClient(cfg)
        issue = jira.get_issue(args.page_id)
        pages = client.issue_pages(args.page_id, jira.documentation_links(issue), title=issue_to_requirement(issue).title)
        return {"pages": [{k: v for k, v in page.items() if k != "untrusted_storage"} for page in pages]}
    else:
        page = client.read_page(args.page_id)
        content = page.pop("untrusted_storage")
        if args.output:
            _save_new_json(args.output, {**page, "untrusted_storage": content})
        return page
    return {"pages": [{"id": str(row["id"]), "title": row["title"]} for row in rows], "truncated": truncated}


def _dev_external_write(args):
    if not args.authorize_write:
        raise ValueError("External writes need explicit --authorize-write")
    config = Config.load(args.config, enterprise_intake=True)
    if args.dev_command == "jira-transition":
        return JiraClient(config).transition(args.issue, args.stage)
    payload = _read_object(args.document)
    return ConfluenceClient(config).publish(args.page_id, args.version, payload["title"], payload["storage"])



def _dev_broker(args):
    from .remote_dev import Broker, serve_broker
    store, config = SpecificationStore(args.store), Config.load(args.config)
    if args.dev_command == "dev-broker-serve":
        return serve_broker(store, config)
    broker = Broker(store, config)
    return broker.work(args.deployment) if args.deployment else broker.work_next()


def _dev_webhook(args):
    from .intake import webhook

    if args.operation == "list":
        return webhook(Config.load(args.config), args.operation)
    if not args.authorize_write or args.journal is None:
        raise ValueError("Webhook writes require explicit authorization and a new reconciliation journal")
    config = Config.load(args.config)
    if not config.get("enterprise.intake.webhook.allow_write", False):
        raise ValueError("Webhook writes are disabled by policy")
    _save_new_json(args.journal, {"status": "INTENT_RECONCILE_BEFORE_RETRY", "operation": args.operation,
                                  "managed_id": args.hook_id})
    result = webhook(config, args.operation, hook_id=args.hook_id, authorized=True)
    _save_new_json(args.journal.with_suffix(args.journal.suffix + ".result"), result)
    return result


def _dev_intake(args):
    store = SpecificationStore(args.store)
    if args.dev_command in {"intake-serve", "intake-renew"}:
        from .intake import renewal_tick, serve
        config = Config.load(args.config)
        if args.dev_command == "intake-renew":
            return renewal_tick(store, config)

        def drain():
            return _dev_intake(argparse.Namespace(dev_command="intake-drain", store=args.store,
                                                   config=args.config, limit=1,
                                                   allow_provider=config.get("enterprise.intake.hosted.allow_provider", False)))

        return serve(store, config, drain=drain)
    if args.dev_command == "intake-queue":
        return {"queued": store.intake_queue(), "dead_letters": store.dead_letters()}
    if args.dev_command == "intake-poll":
        from .intake import poll

        return {"deliveries": poll(store, Config.load(args.config, enterprise_intake=True))}
    if args.dev_command == "intake-drain":
        if not 1 <= args.limit <= 20:
            raise ValueError("Intake drain limit must be between 1 and 20")
        results, failures = [], []
        max_attempts = Config.load(args.config).get("enterprise.intake.retry_limit", 3)
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("Intake retry limit must be between 1 and 10")
        for event in store.intake_queue()[:args.limit]:
            draft_args = argparse.Namespace(config=args.config, issue=event["issue"], store=args.store,
                                            page_id=None, criteria_file=None, allow_provider=args.allow_provider,
                                            delivery_id=event["delivery_id"])
            try:
                result = _dev_spec(draft_args)
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                failure = type(exc).__name__
                store.fail_intake(event["delivery_id"], failure, max_attempts)
                failures.append({"delivery_id": event["delivery_id"], "failure": failure})
                continue
            results.append({"run_id": result["run_id"], "issue": event["issue"],
                            "status": result["draft"]["status"]})
        return {"drafted": results, "failed": failures}
    if args.dev_command == "intake-draft":
        event = next((row for row in store.intake_queue() if row["delivery_id"] == args.delivery_id), None)
        if event is None:
            raise ValueError("No queued Jira delivery matches that ID")
        args.issue, args.page_id, args.criteria_file = event["issue"], None, None
        return _dev_spec(args)
    from .intake import accept

    with args.payload.open("rb") as stream:
        return accept(store, Config.load(args.config), stream.read(256_001), delivery_id=args.delivery_id,
                      timestamp=args.timestamp, signature=args.signature)


def _dev_ci_evidence(args):
    from .github_evidence import collect, dispatch, download_artifact

    config = Config.load(args.config)
    if args.dev_command == "ci-artifact":
        return download_artifact(config, _read_object(args.evidence), args.artifact_id, args.output)
    if args.dev_command == "ci-dispatch":
        if not args.authorize_dispatch:
            raise ValueError("Workflow dispatch needs explicit --authorize-dispatch")
        return dispatch(config, args.repository, args.workflow, args.ref, _read_object(args.inputs) if args.inputs else {})
    record = collect(config, args.repository, args.run_id, args.attempt, args.commit, args.workflow)
    _save_new_json(args.output, record)
    return record


def _dev_metrics(args):
    labels = _read_object(args.labels) if args.labels else {}
    result = {}
    if args.store:
        store = SpecificationStore(args.store)
        attempts = store.get(args.run_id)["development_attempts"]
        observations = [row for attempt in attempts for row in (attempt["evidence"] or {}).get("guardrail_events", [])]
        independent = labels.get("guardrails", [])
        guardrail_effectiveness(independent)
        truth = {(row["phase"], row["id"]): row["violation"] for row in independent}
        observed = [{**row, "violation": truth.get((row["phase"], row["id"]))} for row in observations]
        result["runtime_observations"] = guardrail_breakdown(observed)
        result["human_effort"] = store.human_effort(args.run_id)
    if "guardrails" in labels:
        result["guardrails"] = guardrail_effectiveness(labels["guardrails"])
    if "defects" in labels and "findings" in labels:
        result["reviewers"] = independently_reviewed(labels["defects"], labels["findings"])
    if not result:
        raise ValueError("Provide guardrails or both defects and findings in the labels file")
    return result


def _dev_activity(args):
    store = SpecificationStore(args.store)
    authority = LocalApprovalAuthority(Config.load(args.config))
    kind = args.kind or args.dev_command.split("-")[0]
    end = args.dev_command.endswith("-end")
    authority.seal("approvers", "activity-identity", args.actor, {"run_id": args.run_id})
    return store.human_activity(args.run_id, args.actor, kind, end=end,
                                interrupt=getattr(args, "interrupt", False), note=getattr(args, "note", None))


def _dev_status(args):
    if args.events:
        if args.run_id:
            raise ValueError("--run-id applies to a stored specification, not an event file")
        return summarize(args.events)
    if not args.run_id:
        raise ValueError("--run-id is required with --store")
    from contextlib import closing

    store = SpecificationStore(args.store)
    result = store.get(args.run_id)
    with closing(store._connect()) as conn:
        workflow = conn.execute("SELECT progress FROM unified_runs WHERE run_id=?", (args.run_id,)).fetchone()
    if workflow:
        result["workflow"] = json.loads(workflow["progress"]) if workflow["progress"] else None
    return result


def _dev_decision(args):
    if args.review_sha256:
        from .verification import approve_review, reject_review

        if not args.config:
            raise ValueError("Final review decisions need a trusted --config")
        decision_args = (SpecificationStore(args.store), args.run_id, LocalApprovalAuthority(Config.load(args.config)),
                         args.actor, args.review_sha256)
        if args.dev_command == "reject":
            return reject_review(*decision_args, args.reason)
        return approve_review(*decision_args)
    status = "APPROVED" if args.dev_command == "approve" else "REJECTED"
    authority = LocalApprovalAuthority(Config.load(args.config)) if args.config else None
    return SpecificationStore(args.store).decide(
        args.run_id, args.spec_sha256, status, args.actor, args.reason, authority,
    )


def _dev_cancel(args):
    store = SpecificationStore(args.store)
    if args.lifecycle_stage:
        from .lifecycle import cancel_stage
        return cancel_stage(store, args.run_id, args.lifecycle_stage, requester=args.actor, reason=args.reason)
    return store.cancel(args.run_id)


def _dev_workflow(args):
    from contextlib import closing

    from . import experiments
    from .orchestrator import continue_enterprise

    store = SpecificationStore(args.store)
    if args.dev_command == "workflow-start":
        if bool(args.experiment) != bool(args.trial):
            raise ValueError("Specify both --experiment and --trial")
        trial = None
        if args.experiment:
            experiment = experiments.schedule(store, args.experiment)
            trial = next((row for row in experiment["trials"] if row["id"] == args.trial), None)
            if trial is None or trial["mode"].lower().replace("_", "-") != args.mode:
                raise ValueError("Trial does not match the selected workflow mode")
            run_id = trial["run_id"]
            if store.get(run_id)["draft"]["issue"] != args.issue:
                raise ValueError("Workflow issue does not match the scheduled trial")
        else:
            run_id = store.begin_intake(args.issue)
        plan = {
            "issue": args.issue, "config": str(Path(args.config).resolve()),
            "repo": str((args.experiment / args.trial if trial else args.repo).resolve()),
            "out": str(args.out.resolve()), "acceptance": str(args.acceptance_dir.resolve()),
            "verifier": args.actor, "mode": args.mode,
            "experiment": str(args.experiment.resolve()) if args.experiment else None, "trial": trial,
            "provider_required": args.allow_provider,
        }
        with closing(store._connect()) as conn, conn:
            conn.execute("INSERT INTO unified_runs(run_id, plan) VALUES (?, ?)", (run_id, json.dumps(plan)))
    else:
        run_id = args.run_id
        with closing(store._connect()) as conn:
            row = conn.execute("SELECT plan FROM unified_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("Run has no unified workflow plan")
        plan = json.loads(row["plan"])
    return continue_enterprise(store, run_id, plan, allow_provider=args.allow_provider,
                               allow_ci=args.authorize_ci, allow_dev=args.authorize_remote_dev)


def _dev_run(args):
    cfg = Config.load(args.config, local_model=args.allow_provider)
    from .development import agent_model_config, model_identity, validate_provider, verify_artifact
    from .experiments import enforce_policy

    enforce_policy(SpecificationStore(args.store), args.run_id, cfg)
    validate_provider(cfg, args.allow_provider)
    if args.store.resolve().is_relative_to(args.repo.resolve()):
        raise ValueError("Keep the approval database outside the source workspace")
    if args.store.resolve().is_relative_to(args.out.resolve()):
        raise ValueError("Keep the approval database outside the generated output directory")
    authority = LocalApprovalAuthority(cfg)
    orchestrator = Orchestrator(cfg)
    store = SpecificationStore(args.store)
    model_inputs = {}
    ci_policy, remote_policy = cfg.get("enterprise.delivery_policy.remote_ci", {}), cfg.get("enterprise.remote_dev", {})
    if ci_policy.get("required") or remote_policy.get("required"):
        from .verification import _digest

        model_inputs["delivery_policy_sha256"] = _digest([ci_policy, remote_policy])
    local_review = cfg.get("enterprise.delivery_policy.local_review", {})
    if local_review.get("required") or local_review.get("reference_dir"):
        from .verification import _digest

        model_inputs["local_review_policy_sha256"] = _digest(local_review)
    budgets = cfg.get("enterprise.budgets", {})
    if any(budgets.values()):
        from .verification import _digest

        model_inputs["budget_policy_sha256"] = _digest(budgets)
    if cfg.get("enterprise.agent_models"):
        model_inputs["model_profiles"] = {
            role: model_identity(agent_model_config(cfg, role)) for role in ("code", "testcase", "review")
        }
    if args.dev_command in {"code", "testcase"}:
        from .development import stage

        draft = store.approved_specification(args.run_id, authority)
        return stage(orchestrator, draft, args.repo, args.out, args.run_id, 0,
                     allow_provider=args.allow_provider, independent=args.dev_command)
    correction = None
    if args.dev_command == "correct":
        from .verification import remediation_context

        if cfg.get("llm.provider") == "stub":
            raise ValueError("Correction requires an explicitly configured model")
        correction = remediation_context(store, args.run_id, authority)

    def remediate(draft, attempt, context):
        from .development import stage

        verify_artifact({"artifact_dir": context["source"], "manifest_sha256": context["manifest_sha256"]})
        return stage(orchestrator, draft, Path(context["source"]) / "integrated", args.out / "corrections" / str(attempt),
                     args.run_id, attempt, allow_provider=args.allow_provider, remediation=context)

    result = store.develop(
        args.run_id, authority,
        lambda draft, attempt: orchestrator.stage_development(
            draft, args.repo, args.out, args.run_id, attempt, allow_provider=args.allow_provider,
        ),
        cfg.get("enterprise.development_attempts", 2),
        correction=correction, correction_limit=cfg.get("enterprise.correction_cycles", 2),
        remediation_generate=remediate,
        inputs={"source": str(args.repo.resolve()), "output": str(args.out.resolve()),
                "provider": cfg.get("llm.provider"), "model": cfg.get("llm.model"),
                "endpoint": cfg.get("llm.base_url") if args.allow_provider else None, **model_inputs},
    )
    if result["status"] == "READY_FOR_VERIFICATION":
        verify_artifact(result)
    return result


def _export_lifecycle(directory, report):
    directory.mkdir(parents=True, exist_ok=True)
    _save_new_json(directory / "report.json", report)
    timeline = report["development_attempts"]
    with (directory / "timeline.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["attempt", "status", "started_at", "finished_at", "duration_ms"])
        for attempt in timeline:
            evidence = attempt["evidence"] or {}
            writer.writerow([attempt["attempt"], attempt["status"], evidence.get("started_at"),
                             evidence.get("finished_at"), evidence.get("duration_ms")])
    scored = report["recorded_receipts"]
    lines = ["# Enterprise lifecycle report", "", f"Run `{report['run_id']}`", "",
             f"Requirement status **{report['requirements_status']}**", ""]
    for phase in ("first_pass", "final"):
        result = scored.get(f"{phase}_score")
        if result:
            lines.append(f"- {phase.replace('_', ' ').capitalize()} score {result['earned_points']}/"
                         f"{result['configured_maximum']} ({result['delivery_eligibility']})")
        else:
            lines.append(f"- {phase.replace('_', ' ').capitalize()} score unavailable")
    with (directory / "report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def _dev_recover(args):
    from .lifecycle import recover

    return {"recovery": recover(SpecificationStore(args.store))}


def _dev_report(args):
    from .lifecycle import recovery_history, timing_summary

    store = SpecificationStore(args.store)
    record = store.get(args.run_id)
    receipts = {}
    for kind in ("verification", "actual_verification", "first_pass_score", "review", "review_approval", "review_rejection", "review_adjudication",
                 "validation", "validation_v2", "final_score", "local_review_evidence",
                 "ci_evidence", "ci_review_evidence", "remote_dev_validation"):
        saved = store.receipt(args.run_id, kind, optional=True)
        if saved:
            receipts[kind] = saved.get("payload", saved)
    stages = store.lifecycle_stages(args.run_id)
    report = {
        "run_id": args.run_id, "issue": record["draft"]["issue"],
        "spec_sha256": record["digest"], "requirements_status": record["status"],
        "approval_wait_seconds": record["approval_wait_seconds"],
        "cancelled_at": record["cancelled_at"],
        "human_effort": store.human_effort(args.run_id),
        "attempt_receipt_history": store.historical_receipts(args.run_id),
        "development_attempts": record["development_attempts"],
        "lifecycle_stages": stages,
        "lifecycle_timing": timing_summary(stages),
        "recovery": recovery_history(store, args.run_id),
        "provider_requests": store.provider_requests(args.run_id),
        "recorded_receipts": receipts,
    }
    if args.output:
        _save_new_json(args.output, report)
    if args.report_dir:
        _export_lifecycle(args.report_dir, report)
    return report


def _dev_verification(args):
    from . import verification

    store = SpecificationStore(args.store)
    cfg = Config.load(args.config, local_model=getattr(args, "allow_provider", False))
    from .experiments import enforce_policy

    enforce_policy(store, args.run_id, cfg)
    authority = LocalApprovalAuthority(cfg)
    command_args = (store, args.run_id, authority)
    if args.dev_command == "ci-submit":
        return verification.submit_ci(*command_args, cfg, args.actor,
                                      include_review=args.include_review, authorized=args.authorize_submission)
    if args.dev_command == "ci-bundle":
        return verification.export_ci_bundle(*command_args, args.output, include_review=args.include_review)
    if args.dev_command == "ci-monitor":
        from .github_evidence import monitor
        selection = _read_object(args.evidence)
        fetched = monitor(store, args.run_id, cfg, selection)
        document = {**selection, **{key: fetched[key] for key in ("run_id", "attempt", "commit", "repository", "workflow")},
                    "evaluator_revision": cfg.get("enterprise.delivery_policy.remote_ci.evaluator_revision"),
                    "verified_archives": {row["name"]: row["digest"] for row in fetched["run_artifact_metadata"]}}
        return verification.attest_delivery(*command_args, cfg, args.actor, "ci_evidence", document)
    if args.dev_command in {"ci-attest", "criterion-attest"}:
        kind = "ci_evidence" if args.dev_command == "ci-attest" else "criterion_evidence"
        return verification.attest_delivery(*command_args, cfg, args.actor, kind, _read_object(args.evidence), raw_directory=args.evidence.parent)
    if args.dev_command == "review-adjudicate":
        return verification.adjudicate_review(*command_args, args.actor, _read_object(args.labels))
    if args.dev_command == "verify":
        return verification.record_verification(*command_args, args.actor, _read_object(args.result))
    if args.dev_command == "testing":
        result = verification.execute_verification(*command_args, args.actor, cfg, args.acceptance_dir)
        _capture_lifecycle_score(store, args.run_id, cfg, authority, "first_pass")
        return result
    if args.dev_command == "review":
        return verification.review(*command_args, cfg, allow_provider=args.allow_provider)
    result = verification.validate(*command_args, config=cfg, authorize_remote=getattr(args, "authorize_remote_dev", False))
    if result["environment"] == "LOCAL_ACTUAL":
        _capture_lifecycle_score(store, args.run_id, cfg, authority, "final")
    return result


def _dev(args):
    try:
        issue = getattr(args, "stored_issue", None)
        if issue:
            if args.run_id or not args.store:
                raise ValueError("Use ISSUE with --store, or select the run directly with --run-id")
            args.run_id = SpecificationStore(args.store).resolve_issue(issue)
        if hasattr(args, "stored_issue") and not args.run_id and not (
            getattr(args, "events", None) or getattr(args, "assessment", None)
            or args.dev_command in {"metrics", "compare"} and not args.store
        ):
            raise ValueError("Supply a stored issue or --run-id")
        if getattr(args, "run_id", None) and hasattr(args, "stored_issue") and not args.store:
            raise ValueError("Run selection requires --store")
        stage = args.stage if args.dev_command in {"approve", "reject"} else None
        if (stage == "requirements" and not args.spec_sha256) or (stage == "review" and not args.review_sha256):
            raise ValueError("Approval stage requires the matching specification or review digest")
        registered = {"approve", "reject", "run", "resume", "correct", "code", "testcase", "verify", "testing",
                      "review", "review-adjudicate", "validate", "score", "report", "ci-attest", "criterion-attest", "ci-monitor", "ci-submit"}
        if getattr(args, "run_id", None) and args.dev_command in registered:
            from .lifecycle import execute

            result = execute(SpecificationStore(args.store), args.run_id, args.dev_command, lambda: args.handler(args))
        else:
            result = args.handler(args)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as exc:
        # Integration clients remove response bodies from HTTP errors.
        print(f"dev {args.dev_command}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    failed = result.get("status") in {"FAILED", "BLOCKED", "CANCELLED"}
    return 2 if failed and args.handler in {_dev_run, _dev_verification} else 0


def _dev_parser(sub):
    dev = sub.add_parser("dev", help="read enterprise context and analyze independent evidence")
    commands = dev.add_subparsers(dest="dev_command", required=True)
    backlog = commands.add_parser("backlog", help="read the configured Jira backlog")
    backlog.add_argument("--config")
    backlog.add_argument("--summary", action="store_true", help="show live issue readiness and linked page counts")
    backlog.set_defaults(func=_dev, handler=_dev_backlog)

    spec = commands.add_parser("spec", help="draft a hashed requirement from a Jira issue")
    recovery = commands.add_parser("recover", help="assess interrupted lifecycle executions")
    recovery.add_argument("--store", type=Path, required=True)
    recovery.set_defaults(func=_dev, handler=_dev_recover)

    spec.add_argument("issue")
    spec.add_argument("--intake-run-id", help="resume an existing intake for this issue")
    spec.add_argument("--config")
    spec.add_argument("--page-id", action="append", help="record authorized Confluence page provenance")
    spec.add_argument("--criteria-file", type=Path, help="operator criteria JSON when Jira supplies none")
    spec.add_argument("--allow-provider", action="store_true", help="analyze source context with the approved configured model")
    storage = spec.add_mutually_exclusive_group()
    storage.add_argument("--output", type=Path, help="save the draft to a new file")
    storage.add_argument("--store", type=Path, help="persist the draft in a local SQLite database")
    spec.set_defaults(func=_dev, handler=_dev_spec)

    context = commands.add_parser("context", help="read a Confluence page by ID")
    context_source = context.add_mutually_exclusive_group(required=True)
    context_source.add_argument("page_id", nargs="?", help="numeric page ID or Jira issue key")
    context_source.add_argument("--spaces", action="store_true")
    context_source.add_argument("--space", help="Confluence space key")
    context_source.add_argument("--search", help="Confluence page keywords")
    context.add_argument("--config")
    context.add_argument("--output", type=Path, help="save bounded untrusted content to a new file")
    context.set_defaults(func=_dev, handler=_dev_context)

    scorer = commands.add_parser("score", help="score an independent assessment")
    score_input = scorer.add_mutually_exclusive_group(required=True)
    score_input.add_argument("--assessment", type=Path)
    score_input.add_argument("--store", type=Path)
    scorer.add_argument("--run-id")
    scorer.add_argument("--evidence-dir", type=Path)
    scorer.add_argument("--config")
    scorer.add_argument("--report-dir", type=Path, help="write JSON, CSV and Markdown")
    scorer.set_defaults(func=_dev, handler=_dev_score)

    criterion_registry = commands.add_parser("criterion-registry", help="show independent evidence requirements by criterion")
    criterion_registry.add_argument("--config")
    criterion_registry.set_defaults(func=_dev, handler=_dev_registry)

    status = commands.add_parser("status", help="summarize a recorded run's stage attempts and timings")
    source = status.add_mutually_exclusive_group(required=True)
    source.add_argument("--events", type=Path)
    source.add_argument("--store", type=Path)
    status.add_argument("--run-id")
    status.set_defaults(func=_dev, handler=_dev_status)

    for name in ("approve", "reject"):
        decision = commands.add_parser(name, help=f"{name} a stored requirement specification")
        decision.add_argument("--store", type=Path, required=True)
        decision.add_argument("--run-id")
        digest = decision.add_mutually_exclusive_group(required=True)
        digest.add_argument("--spec-sha256")
        digest.add_argument("--review-sha256")
        decision.add_argument("--stage", choices=("requirements", "review"))
        decision.add_argument("--actor", required=True)
        decision.add_argument("--config", help="trusted local approval identity configuration")
        decision.add_argument("--reason", required=(name == "reject"))
        decision.set_defaults(func=_dev, handler=_dev_decision)

    for name in ("workflow-start", "workflow-resume"):
        workflow = commands.add_parser(name, help="start or resume the complete enterprise SDLC")
        workflow.add_argument("--store", type=Path, required=True)
        workflow.add_argument("--allow-provider", action="store_true")
        workflow.add_argument("--authorize-ci", action="store_true")
        workflow.add_argument("--authorize-remote-dev", action="store_true")
        if name == "workflow-start":
            workflow.add_argument("issue")
            workflow.add_argument("--mode", choices=("agent-driven", "agent-assisted", "human-baseline"),
                                  default="agent-driven")
            workflow.add_argument("--config", required=True)
            workflow.add_argument("--repo", type=Path, required=True)
            workflow.add_argument("--out", type=Path, required=True)
            workflow.add_argument("--acceptance-dir", type=Path, required=True)
            workflow.add_argument("--actor", required=True, help="configured independent verifier")
            workflow.add_argument("--experiment", type=Path)
            workflow.add_argument("--trial")
        else:
            workflow.add_argument("--run-id", required=True)
        workflow.set_defaults(func=_dev, handler=_dev_workflow)

    for name in ("run", "resume", "correct", "code", "testcase"):
        development = commands.add_parser(name, help="stage offline development for an approved specification")
        development.add_argument("--store", type=Path, required=True)
        development.add_argument("--run-id")
        development.add_argument("--repo", type=Path, required=True, help="trusted source directory to copy")
        development.add_argument("--out", type=Path, required=True, help="separate artifact directory")
        development.add_argument("--allow-provider", action="store_true", help="allow approved configured model requests")
        development.add_argument("--config", required=True, help="trusted local approval identity configuration")
        development.set_defaults(func=_dev, handler=_dev_run)

    for name in ("verify", "testing", "review", "review-adjudicate", "validate", "ci-attest", "criterion-attest", "ci-monitor"):
        command = commands.add_parser(name, help=f"{name} exact offline development artifacts")
        command.add_argument("--store", type=Path, required=True)
        command.add_argument("--run-id")
        command.add_argument("--config", required=True, help="trusted local identity configuration")
        if name == "verify":
            command.add_argument("--result", type=Path, required=True, help="independent offline check results")
            command.add_argument("--actor", required=True, help="configured local verifier identity")
        if name == "testing":
            command.add_argument("--acceptance-dir", type=Path, required=True, help="independent trusted test files")
            command.add_argument("--actor", required=True, help="configured local verifier identity")
        if name in {"ci-attest", "criterion-attest", "ci-monitor"}:
            command.add_argument("--evidence", type=Path, required=True)
            command.add_argument("--actor", required=True)
        if name == "review-adjudicate":
            command.add_argument("--labels", type=Path, required=True)
            command.add_argument("--actor", required=True)
        if name == "validate":
            command.add_argument("--authorize-remote-dev", action="store_true")
        if name == "review":
            command.add_argument("--allow-provider", action="store_true", help="allow approved configured review model")
        command.set_defaults(func=_dev, handler=_dev_verification)

    cancellation = commands.add_parser("cancel", help="cancel pending offline development")
    cancellation.add_argument("--store", type=Path, required=True)
    cancellation.add_argument("--run-id")
    cancellation.add_argument("--lifecycle-stage")
    cancellation.add_argument("--actor")
    cancellation.add_argument("--reason")
    cancellation.set_defaults(func=_dev, handler=_dev_cancel)

    report = commands.add_parser("report", help="summarize stored offline execution evidence")
    report.add_argument("--store", type=Path, required=True)
    report.add_argument("--run-id")
    report.add_argument("--output", type=Path, help="save the report to a new JSON file")
    report.add_argument("--report-dir", type=Path, help="export JSON, CSV timeline and Markdown")
    report.set_defaults(func=_dev, handler=_dev_report)

    activities = []
    for name in ("review-start", "review-end", "rework-start", "rework-end"):
        activity = commands.add_parser(name, help="record active human effort on a stored run")
        activity.add_argument("--store", type=Path, required=True)
        activity.add_argument("--run-id")
        activity.add_argument("--actor", required=True)
        activity.add_argument("--config", required=True)
        activity.add_argument("--kind", choices=("requirements", "review", "re-review", "manual-code",
                                                 "manual-test", "rework", "validation"))
        if name.endswith("-end"):
            activity.add_argument("--interrupt", action="store_true")
            activity.add_argument("--note")
        activity.set_defaults(func=_dev, handler=_dev_activity)
        activities.append(activity)

    for command_name in ("approve", "reject", "run", "resume", "correct", "code", "testcase", "verify", "testing", "review", "review-adjudicate", "ci-attest", "criterion-attest", "ci-monitor",
                         "validate", "cancel", "report", "status", "score"):
        commands.choices[command_name].add_argument("stored_issue", nargs="?", metavar="ISSUE")
    for activity in activities:
        activity.add_argument("stored_issue", nargs="?", metavar="ISSUE")

    for name in ("intake-receive", "intake-queue", "intake-draft", "intake-poll", "intake-drain", "intake-serve", "intake-renew"):
        intake = commands.add_parser(name, help="process an authenticated Jira relay delivery")
        intake.add_argument("--store", type=Path, required=True)
        intake.add_argument("--config")
        if name in {"intake-receive", "intake-draft"}:
            intake.add_argument("--delivery-id", required=True)
        if name == "intake-receive":
            intake.add_argument("--payload", type=Path, required=True)
            intake.add_argument("--timestamp", required=True)
            intake.add_argument("--signature", required=True)
        if name in {"intake-draft", "intake-drain"}:
            intake.add_argument("--allow-provider", action="store_true")
        if name == "intake-drain":
            intake.add_argument("--limit", type=int, default=1)
        intake.set_defaults(func=_dev, handler=_dev_intake)

    experiment_cancel = commands.add_parser("experiment-cancel", help="cancel registered experiment trials")
    experiment_cancel.add_argument("--store", type=Path, required=True)
    experiment_cancel.add_argument("--experiment-id", required=True)
    experiment_cancel.set_defaults(func=_dev, handler=_dev_experiment)

    baseline = commands.add_parser("experiment-baseline", help="submit an independently attested human baseline")
    baseline.add_argument("--directory", type=Path, required=True)
    baseline.add_argument("--trial", required=True)
    baseline.add_argument("--measurements", type=Path, required=True)
    baseline.add_argument("--deliverable", type=Path, required=True)
    baseline.add_argument("--config", required=True)
    baseline.set_defaults(func=_dev, handler=_dev_experiment)

    submission = commands.add_parser("ci-submit", help="publish and monitor an approved CI evaluation")
    submission.add_argument("--store", type=Path, required=True)
    submission.add_argument("--run-id", required=True)
    submission.add_argument("--config", required=True)
    submission.add_argument("--actor", required=True)
    submission.add_argument("--include-review", action="store_true")
    submission.add_argument("--authorize-submission", action="store_true")
    submission.set_defaults(func=_dev, handler=_dev_verification)

    bundle = commands.add_parser("ci-bundle", help="export an exact approved artifact for evaluation")
    bundle.add_argument("--store", type=Path, required=True)
    bundle.add_argument("--run-id", required=True)
    bundle.add_argument("--config", required=True)
    bundle.add_argument("--output", type=Path, required=True)
    bundle.add_argument("--include-review", action="store_true")
    bundle.set_defaults(func=_dev, handler=_dev_verification)

    for name in ("experiment-prepare", "experiment-record", "experiment-report", "experiment-seed", "experiment-guardrails", "experiment-schedule", "experiment-trial"):
        experiment = commands.add_parser(name, help="manage a local reproducible experiment")
        experiment.add_argument("--directory", type=Path, required=True)
        if name in {"experiment-schedule", "experiment-trial"}:
            experiment.add_argument("--store", type=Path, required=True)
            if name == "experiment-trial":
                experiment.add_argument("--trial", required=True)
                experiment.add_argument("--operation", required=True, choices=("run", "resume", "correct", "testing", "review", "validate", "report", "status", "cancel"))
                experiment.add_argument("--config", required=True)
                experiment.add_argument("--out", type=Path, required=True)
                experiment.add_argument("--acceptance-dir", type=Path)
                experiment.add_argument("--actor")
                experiment.add_argument("--allow-provider", action="store_true")
        elif name == "experiment-seed":
            experiment.add_argument("--scenario", required=True)
            experiment.add_argument("--evaluator", type=Path, required=True)
            experiment.add_argument("--repetitions", type=int, default=1)
        elif name == "experiment-prepare":
            experiment.add_argument("--plan", type=Path, required=True)
            experiment.add_argument("--repo", type=Path, required=True)
        elif name == "experiment-record":
            experiment.add_argument("--trial", required=True)
            experiment.add_argument("--measurements", type=Path, required=True)
        else:
            experiment.add_argument("--config")
        if name == "experiment-report":
            experiment.add_argument("--report-dir", type=Path)
        experiment.set_defaults(func=_dev, handler=_dev_experiment)

    for name in ("dev-broker-serve", "dev-broker-work"):
        command = commands.add_parser(name, help="operate the explicitly configured DEV broker")
        command.add_argument("--store", type=Path, required=True)
        command.add_argument("--config", required=True)
        if name == "dev-broker-work":
            command.add_argument("--deployment")
        command.set_defaults(func=_dev, handler=_dev_broker)

    for name in ("jira-transition", "confluence-publish"):
        write = commands.add_parser(name, help="perform an explicitly authorized, policy-scoped write")
        write.add_argument("--config", required=True)
        write.add_argument("--authorize-write", action="store_true")
        if name == "jira-transition":
            write.add_argument("issue")
            write.add_argument("--stage", required=True)
        else:
            write.add_argument("page_id")
            write.add_argument("--version", type=int, required=True)
            write.add_argument("--document", type=Path, required=True)
        write.set_defaults(func=_dev, handler=_dev_external_write)

    webhook = commands.add_parser("jira-webhook", help="manage a configured Jira OAuth webhook")
    webhook.add_argument("operation", choices=("list", "register", "renew", "remove"))
    webhook.add_argument("--config", required=True)
    webhook.add_argument("--hook-id", type=int)
    webhook.add_argument("--journal", type=Path)
    webhook.add_argument("--authorize-write", action="store_true")
    webhook.set_defaults(func=_dev, handler=_dev_webhook)

    ci = commands.add_parser("ci-evidence", help="save read-only GitHub Actions run evidence")
    ci.add_argument("--repository", required=True)
    ci.add_argument("--run-id", type=int, required=True)
    ci.add_argument("--attempt", type=int, required=True)
    ci.add_argument("--commit", required=True)
    ci.add_argument("--workflow", required=True)
    ci.add_argument("--output", type=Path, required=True)
    ci.add_argument("--config")
    ci.set_defaults(func=_dev, handler=_dev_ci_evidence)

    artifact = commands.add_parser("ci-artifact", help="retrieve an archive verified against GitHub metadata")
    artifact.add_argument("--evidence", type=Path, required=True)
    artifact.add_argument("--artifact-id", type=int, required=True)
    artifact.add_argument("--output", type=Path, required=True)
    artifact.add_argument("--config")
    artifact.set_defaults(func=_dev, handler=_dev_ci_evidence)

    dispatch = commands.add_parser("ci-dispatch", help="request an explicitly authorized workflow dispatch")
    dispatch.add_argument("--repository", required=True)
    dispatch.add_argument("--workflow", required=True)
    dispatch.add_argument("--ref", required=True)
    dispatch.add_argument("--inputs", type=Path)
    dispatch.add_argument("--config", required=True)
    dispatch.add_argument("--authorize-dispatch", action="store_true")
    dispatch.set_defaults(func=_dev, handler=_dev_ci_evidence)

    metrics = commands.add_parser("metrics", help="measure independently labelled guardrail and review outcomes")
    metrics.add_argument("--labels", type=Path)
    metrics.add_argument("--store", type=Path)
    metrics.add_argument("--run-id")
    metrics.add_argument("stored_issue", nargs="?", metavar="ISSUE")
    metrics.set_defaults(func=_dev, handler=_dev_metrics)

    comparison = commands.add_parser("compare", help="compare equivalent measured runs")
    comparison.add_argument("--baseline", type=Path, required=True)
    comparison.add_argument("--actual", type=Path, required=True)
    comparison.add_argument("--config")
    comparison.add_argument("--store", type=Path)
    comparison.add_argument("--run-id")
    comparison.add_argument("stored_issue", nargs="?", metavar="ISSUE")
    comparison.set_defaults(func=_dev, handler=_dev_compare)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2 and argv[0] == "dev" and re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", argv[1]):
        argv.insert(1, "status")
    parser = argparse.ArgumentParser(prog="agentic_sdlc", description="Agentic SDLC framework")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the full SDLC flow")
    run.add_argument("--requirements", nargs="+", default=[],
                     help="requirement documents (.md, .txt, .html, .json, .docx)")
    run.add_argument("--repo", help="path of the target repository")
    run.add_argument("--config", help="YAML file overriding the packaged sdlc.yaml")
    run.add_argument("--out", help="output directory for run artifacts")
    run.add_argument("--llm", choices=["stub", "openai"])
    run.add_argument("--vcs", choices=["dry-run", "git", "gerrit"])
    run.add_argument("--interactive", action="store_true", help="prompt for HITL gates")
    run.set_defaults(func=_run)

    bench = sub.add_parser("benchmark", help="reviewer accuracy / precision / recall / F1")
    bench.add_argument("--runs", help="directory containing run outputs (review-*.json)")
    bench.add_argument("--dataset", help="labelled dataset JSON instead of run outputs")
    bench.add_argument("--config")
    bench.add_argument("--json", action="store_true")
    bench.set_defaults(func=_benchmark)

    _dev_parser(sub)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
