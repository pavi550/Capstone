"""Command line: `py -m agentic_sdlc run ...` and `py -m agentic_sdlc benchmark ...`."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from .approval import LocalApprovalAuthority
from .config import Config
from .confluence import ConfluenceClient
from .evaluation import compare, score
from .events import summarize
from .jira import JiraClient, fetch_requirements
from .metrics import benchmark, guardrail_effectiveness, independently_reviewed
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


def _dev_score(args):
    cfg = Config.load(args.config)
    rubric = cfg.get("evaluation.rubric")
    if args.store:
        if not args.run_id or not args.config or not args.report_dir or args.evidence_dir:
            raise ValueError("Lifecycle scoring needs --store, --run-id, --config and --report-dir")
        assessment = _lifecycle_assessment(args, cfg)
        root = args.report_dir
    else:
        if not args.evidence_dir or args.run_id:
            raise ValueError("An independent assessment requires --evidence-dir")
        assessment = _read_object(args.assessment)
        root = args.evidence_dir
    result = score(rubric, assessment, root)
    if args.report_dir:
        _write_score(args.report_dir, result)
    return result


def _lifecycle_assessment(args, cfg):
    from .verification import scoring_inputs

    facts, checks, gates = scoring_inputs(
        SpecificationStore(args.store), args.run_id, LocalApprovalAuthority(cfg),
    )
    filename = "lifecycle-evidence.json"
    args.report_dir.mkdir(parents=True, exist_ok=True)
    path = args.report_dir / filename
    serialized = json.dumps(facts, indent=2, sort_keys=True).encode()
    if path.exists():
        if path.read_bytes() != serialized:
            raise ValueError("Use a new report directory for a different lifecycle evidence snapshot")
    else:
        with path.open("xb") as stream:
            stream.write(serialized)
    ref = [{"path": filename, "sha256": hashlib.sha256(serialized).hexdigest()}]
    return {
        "evaluator_version": "lifecycle-receipts-v1",
        "criteria": {
            name: {
                "ratio": int(checks[check]), "method": "independent command exit status",
                "reason": "Recorded independent check result", "evidence": ref,
            }
            for name, check in (("regression", "regression"), ("definition_of_done", "definition_of_done"))
            if check in checks
        },
        "gates": {name: {"passed": passed, "evidence": ref} for name, passed in gates.items()},
    }


def _dev_compare(args):
    cfg = Config.load(args.config)
    return compare(
        _read_object(args.baseline), _read_object(args.actual), cfg.get("evaluation.index_weights"),
    )


def _dev_backlog(args):
    cfg = Config.load(args.config)
    if not cfg.get("jira.issues_file") and not (cfg.get("jira.enabled") and cfg.get("jira.jql")):
        raise ValueError("Set jira.issues_file or enable Jira and configure jira.jql")
    return {"requirements": [item.to_dict() for item in fetch_requirements(cfg)]}


def _dev_spec(args):
    cfg = Config.load(args.config)
    if cfg.get("jira.issues_file"):
        requirement = next(
            (item for item in fetch_requirements(cfg) if item.id == args.issue), None,
        )
        if requirement is None:
            raise ValueError("Issue not found in the configured Jira fixture")
    elif cfg.get("jira.enabled"):
        requirement = JiraClient(cfg).get_requirement(args.issue)
    else:
        raise ValueError("Set jira.issues_file or enable and configure Jira")
    provenance = []
    if args.page_id:
        client = ConfluenceClient(cfg)
        for page_id in dict.fromkeys(args.page_id):
            page = client.read_page(page_id)
            provenance.append({k: page[k] for k in ("id", "version", "url", "sha256", "truncated")})
    draft = {
        "schema_version": 1, "issue": requirement.id, "source": requirement.source,
        "title": requirement.title, "description": requirement.description,
        "acceptance_criteria": [
            {"id": f"{requirement.id}-AC-{index}", "text": criterion}
            for index, criterion in enumerate(requirement.acceptance_criteria, 1)
        ],
        "confluence": provenance,
        "status": "AWAITING_APPROVAL" if requirement.acceptance_criteria else "BLOCKED",
    }
    draft["spec_sha256"] = specification_digest(draft)
    if args.store:
        return {"run_id": SpecificationStore(args.store).create(draft), "draft": draft}
    if args.output:
        _save_new_json(args.output, draft)
    return draft


def _save_new_json(path: Path, data: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)


def _dev_context(args):
    page = ConfluenceClient(Config.load(args.config)).read_page(args.page_id)
    content = page.pop("untrusted_storage")
    if args.output:
        _save_new_json(args.output, {**page, "untrusted_storage": content})
    return page


def _dev_metrics(args):
    labels = _read_object(args.labels)
    result = {}
    if "guardrails" in labels:
        result["guardrails"] = guardrail_effectiveness(labels["guardrails"])
    if "defects" in labels and "findings" in labels:
        result["reviewers"] = independently_reviewed(labels["defects"], labels["findings"])
    if not result:
        raise ValueError("Provide guardrails or both defects and findings in the labels file")
    return result


def _dev_status(args):
    if args.events:
        if args.run_id:
            raise ValueError("--run-id applies to a stored specification, not an event file")
        return summarize(args.events)
    if not args.run_id:
        raise ValueError("--run-id is required with --store")
    return SpecificationStore(args.store).get(args.run_id)


def _dev_decision(args):
    if args.review_sha256:
        from .verification import approve_review

        if args.dev_command != "approve" or not args.config:
            raise ValueError("Final review approval needs approve and a trusted --config")
        return approve_review(
            SpecificationStore(args.store), args.run_id, LocalApprovalAuthority(Config.load(args.config)),
            args.actor, args.review_sha256,
        )
    status = "APPROVED" if args.dev_command == "approve" else "REJECTED"
    authority = LocalApprovalAuthority(Config.load(args.config)) if args.config else None
    return SpecificationStore(args.store).decide(
        args.run_id, args.spec_sha256, status, args.actor, args.reason, authority,
    )


def _dev_cancel(args):
    return SpecificationStore(args.store).cancel(args.run_id)


def _dev_run(args):
    cfg = Config.load(args.config)
    from .development import validate_provider, verify_artifact

    validate_provider(cfg, args.allow_provider)
    if args.store.resolve().is_relative_to(args.repo.resolve()):
        raise ValueError("Keep the approval database outside the source workspace")
    if args.store.resolve().is_relative_to(args.out.resolve()):
        raise ValueError("Keep the approval database outside the generated output directory")
    authority = LocalApprovalAuthority(cfg)
    orchestrator = Orchestrator(cfg)
    result = SpecificationStore(args.store).develop(
        args.run_id, authority,
        lambda draft, attempt: orchestrator.stage_development(
            draft, args.repo, args.out, args.run_id, attempt, allow_provider=args.allow_provider,
        ),
        cfg.get("enterprise.development_attempts", 2),
        inputs={"source": str(args.repo.resolve()), "output": str(args.out.resolve()),
                "provider": cfg.get("llm.provider"), "model": cfg.get("llm.model"),
                "endpoint": cfg.get("llm.base_url") if args.allow_provider else None},
    )
    if result["status"] == "READY_FOR_VERIFICATION":
        verify_artifact(result)
    return result


def _dev_report(args):
    store = SpecificationStore(args.store)
    record = store.get(args.run_id)
    receipts = {}
    for kind in ("verification", "actual_verification", "review", "review_approval", "validation"):
        saved = store.receipt(args.run_id, kind, optional=True)
        if saved:
            receipts[kind] = saved.get("payload", saved)
    report = {
        "run_id": args.run_id, "issue": record["draft"]["issue"],
        "spec_sha256": record["digest"], "requirements_status": record["status"],
        "approval_wait_seconds": record["approval_wait_seconds"],
        "cancelled_at": record["cancelled_at"],
        "development_attempts": record["development_attempts"],
        "recorded_receipts": receipts,
    }
    if args.output:
        _save_new_json(args.output, report)
    return report


def _dev_verification(args):
    from . import verification

    store = SpecificationStore(args.store)
    cfg = Config.load(args.config)
    authority = LocalApprovalAuthority(cfg)
    command_args = (store, args.run_id, authority)
    if args.dev_command == "verify":
        return verification.record_verification(*command_args, args.actor, _read_object(args.result))
    if args.dev_command == "testing":
        return verification.execute_verification(*command_args, args.actor, cfg, args.acceptance_dir)
    if args.dev_command == "review":
        return verification.review(*command_args, cfg, allow_provider=args.allow_provider)
    return verification.validate(*command_args)


def _dev(args):
    try:
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
    backlog.add_argument("--config", required=True)
    backlog.set_defaults(func=_dev, handler=_dev_backlog)

    spec = commands.add_parser("spec", help="draft a hashed requirement from a Jira issue")
    spec.add_argument("issue")
    spec.add_argument("--config", required=True)
    spec.add_argument("--page-id", action="append", help="record authorized Confluence page provenance")
    storage = spec.add_mutually_exclusive_group()
    storage.add_argument("--output", type=Path, help="save the draft to a new file")
    storage.add_argument("--store", type=Path, help="persist the draft in a local SQLite database")
    spec.set_defaults(func=_dev, handler=_dev_spec)

    context = commands.add_parser("context", help="read a Confluence page by ID")
    context.add_argument("page_id")
    context.add_argument("--config", required=True)
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

    status = commands.add_parser("status", help="summarize a recorded run's stage attempts and timings")
    source = status.add_mutually_exclusive_group(required=True)
    source.add_argument("--events", type=Path)
    source.add_argument("--store", type=Path)
    status.add_argument("--run-id")
    status.set_defaults(func=_dev, handler=_dev_status)

    for name in ("approve", "reject"):
        decision = commands.add_parser(name, help=f"{name} a stored requirement specification")
        decision.add_argument("--store", type=Path, required=True)
        decision.add_argument("--run-id", required=True)
        digest = decision.add_mutually_exclusive_group(required=True)
        digest.add_argument("--spec-sha256")
        digest.add_argument("--review-sha256")
        decision.add_argument("--actor", required=True)
        decision.add_argument("--config", help="trusted local approval identity configuration")
        decision.add_argument("--reason", required=(name == "reject"))
        decision.set_defaults(func=_dev, handler=_dev_decision)

    for name in ("run", "resume"):
        development = commands.add_parser(name, help="stage offline development for an approved specification")
        development.add_argument("--store", type=Path, required=True)
        development.add_argument("--run-id", required=True)
        development.add_argument("--repo", type=Path, required=True, help="trusted source directory to copy")
        development.add_argument("--out", type=Path, required=True, help="separate artifact directory")
        development.add_argument("--allow-provider", action="store_true", help="allow loopback LLM requests")
        development.add_argument("--config", required=True, help="trusted local approval identity configuration")
        development.set_defaults(func=_dev, handler=_dev_run)

    for name in ("verify", "testing", "review", "validate"):
        command = commands.add_parser(name, help=f"{name} exact offline development artifacts")
        command.add_argument("--store", type=Path, required=True)
        command.add_argument("--run-id", required=True)
        command.add_argument("--config", required=True, help="trusted local identity configuration")
        if name == "verify":
            command.add_argument("--result", type=Path, required=True, help="independent offline check results")
            command.add_argument("--actor", required=True, help="configured local verifier identity")
        if name == "testing":
            command.add_argument("--acceptance-dir", type=Path, required=True, help="independent trusted test files")
            command.add_argument("--actor", required=True, help="configured local verifier identity")
        if name == "review":
            command.add_argument("--allow-provider", action="store_true", help="allow loopback review model")
        command.set_defaults(func=_dev, handler=_dev_verification)

    cancellation = commands.add_parser("cancel", help="cancel pending offline development")
    cancellation.add_argument("--store", type=Path, required=True)
    cancellation.add_argument("--run-id", required=True)
    cancellation.set_defaults(func=_dev, handler=_dev_cancel)

    report = commands.add_parser("report", help="summarize stored offline execution evidence")
    report.add_argument("--store", type=Path, required=True)
    report.add_argument("--run-id", required=True)
    report.add_argument("--output", type=Path, help="save the report to a new JSON file")
    report.set_defaults(func=_dev, handler=_dev_report)

    metrics = commands.add_parser("metrics", help="measure independently labelled guardrail and review outcomes")
    metrics.add_argument("--labels", type=Path, required=True)
    metrics.set_defaults(func=_dev, handler=_dev_metrics)

    comparison = commands.add_parser("compare", help="compare equivalent measured runs")
    comparison.add_argument("--baseline", type=Path, required=True)
    comparison.add_argument("--actual", type=Path, required=True)
    comparison.add_argument("--config")
    comparison.set_defaults(func=_dev, handler=_dev_compare)


def main(argv: list[str] | None = None) -> int:
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
