"""Run the extraction pipeline on the test calls with the real model and score it.

    python -m eval.run_eval                 # uses LLM_MODEL from the environment
    python -m eval.run_eval --model claude-opus-5-5 --repeat 3 --min-score 0.9

Run this before changing the prompt or the model. --repeat runs each case several
times, because model output can vary between runs. Exit code 1 means the score is
below --min-score (use it in CI). Reports are written to eval/reports/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from app.evidence import needs_check
from app.extraction import PROMPT_VERSION, CallCtx, ClientCtx, PriorRecord, run_extraction
from app.llm import LLM, AnthropicLLM, LLMError
from app.transcripts import parse_transcript

from .grader import grade, load_cases


def run_case(llm: LLM, case: dict) -> dict:
    lines = parse_transcript(case["call"]["transcript"])
    call = CallCtx(case["call"]["title"], date.fromisoformat(case["call"]["date"]), lines)
    client = ClientCtx(case["client"]["name"], case["client"].get("people", ""), case["client"].get("sow", ""))
    prior = [PriorRecord(p["id"], p["type"], p["topic"], p["value"], p["text"], p["date"]) for p in case.get("prior", [])]
    n = iter(range(1, 10_000))
    t0 = time.time()
    try:
        res = run_extraction(llm, client, call, prior, id_gen=lambda: f"N{next(n)}")
    except LLMError as e:
        return {"id": case["id"], "name": case["name"], "error": f"{e.code}: {e}", "checks": [], "records": []}
    recs = [dict(r, due_date=r["due_date"].isoformat() if r["due_date"] else None) for r in res.records]
    return {"id": case["id"], "name": case["name"], "seconds": round(time.time() - t0, 1),
            "tokens": res.usage.input_tokens + res.usage.output_tokens, "checks": grade(case, recs), "records": recs,
            "verified": sum(1 for r in recs if r["verified"]), "flagged": sum(1 for r in recs if needs_check(r))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--only", default=None, help="comma-separated case ids")
    args = ap.parse_args(argv)

    llm = AnthropicLLM(model=args.model)
    cases = load_cases()
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    results = []
    for rep in range(args.repeat):
        for c in cases:
            r = run_case(llm, c)
            r["repeat"] = rep + 1
            results.append(r)
            passed = sum(ch["ok"] for ch in r["checks"])
            print(f"[{rep + 1}] {c['name']}: " + (r["error"] if r.get("error") else f"{passed}/{len(r['checks'])}"), flush=True)
    checks = [ch for r in results for ch in r["checks"]]
    errors = sum(1 for r in results if r.get("error"))
    total = len(checks) + errors  # an errored case counts as failing
    score = sum(ch["ok"] for ch in checks) / total if total else 0.0

    out_dir = Path(__file__).with_name("reports")
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    meta = {"model": llm.model, "prompt_version": PROMPT_VERSION, "at": stamp, "score": round(score, 3), "repeat": args.repeat}
    (out_dir / f"eval-{stamp}.json").write_text(json.dumps({"meta": meta, "results": results}, indent=2, default=str), encoding="utf-8")
    md = [f"# Extraction eval {stamp}", "", f"Model `{llm.model}`, prompt `{PROMPT_VERSION}`, repeats {args.repeat}.", "",
          f"**Score: {sum(ch['ok'] for ch in checks)} of {total} checks ({score:.0%})**", ""]
    for r in results:
        md.append(f"## {r['name']} (run {r['repeat']})")
        if r.get("error"):
            md += [f"Error: {r['error']}", ""]
            continue
        md += [f"- {'PASS' if ch['ok'] else 'FAIL'}: {ch['label']}. {ch['detail']}" for ch in r["checks"]]
        md += ["", f"{len(r['records'])} records, {r['verified']} quotes verified, {r['flagged']} flagged, {r['tokens']} tokens, {r['seconds']}s", ""]
    (out_dir / f"eval-{stamp}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nScore {score:.0%} ({sum(ch['ok'] for ch in checks)}/{total}). Report: eval/reports/eval-{stamp}.md")
    return 1 if score < args.min_score else 0


if __name__ == "__main__":
    sys.exit(main())
