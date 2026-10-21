"""Command line: `py -m agentic_sdlc run ...` and `py -m agentic_sdlc benchmark ...`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Config
from .metrics import benchmark
from .orchestrator import END, Orchestrator


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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
