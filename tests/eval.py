"""Run all 30 sample claims and print a pass rate.

    python -m tests.eval            real local models (Ollama must be running)
    python -m tests.eval --fake     fake models (checks the code logic only)
    python -m tests.eval --only CLM-EVAL-010 CLM-EVAL-011

The report is written to reports/eval_<time>.md
"""
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = ["outcome", "reasons", "payout", "citations", "rag_agrees", "photo_check", "no_failures"]


def check(state, expected, policy) -> dict:
    return {
        "outcome": state.decision.outcome == expected["outcome"],
        "reasons": set(expected["reasons"]) <= set(state.decision.reasons),
        "payout": (state.payout.net if state.payout else None) == expected["net_payout"],
        "citations": state.policy is not None and set(state.policy.cited) <= policy.all_ids(),
        "rag_agrees": state.rag is not None and state.rag.agrees,
        "photo_check": state.damage is not None and state.damage.matches_story == expected["matches_story"],
        "no_failures": not state.failures,
    }


def main(args: list[str]) -> float:
    fake = "--fake" in args
    only = args[args.index("--only") + 1:] if "--only" in args else []
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.environ["AUDIT_DIR"] = str(ROOT / "reports" / f"audit_{stamp}")   # keep this run's audit files

    from app import data
    from app.pipeline import process_claim
    from app.schemas import Submission
    if fake:
        from tests.fakes import fakes_on
        fakes_on()

    rows = []
    for c in data.sample_claims():
        if only and c["claim_id"] not in only:
            continue
        start = time.time()
        state = process_claim(Submission(**c["submission"]), c["claim_id"])
        result = check(state, c["expected"], data.get_policy(c["submission"]["policy_id"]))
        rows.append((c["claim_id"], c["expected"]["outcome"], state.decision.outcome,
                     state.decision.reasons, result, round(time.time() - start, 1), state.failures))
        print(f"{'PASS' if all(result.values()) else 'FAIL'}  {c['claim_id']}  "
              f"expected={c['expected']['outcome']:<12} got={state.decision.outcome:<12} {rows[-1][5]}s")

    passed = sum(all(r[4].values()) for r in rows)
    lines = [f"# Eval ({'fake' if fake else 'live'} models) — {stamp}", "",
             f"**Pass rate: {passed / len(rows):.0%}** ({passed}/{len(rows)} claims pass every check)", "",
             "| Check | Pass rate |", "|---|---|"]
    lines += [f"| {k} | {sum(r[4][k] for r in rows) / len(rows):.0%} |" for k in CHECKS]
    lines += ["", "| Claim | Expected | Got | Reasons | Failed checks | Time |", "|---|---|---|---|---|---|"]
    for cid, exp, got, reasons, result, secs, failures in rows:
        failed = ", ".join(k for k, ok in result.items() if not ok) or "—"
        lines.append(f"| {cid} | {exp} | {got} | {', '.join(reasons)} | {failed} | {secs}s |")
    report = ROOT / "reports" / f"eval_{'fake' if fake else 'live'}_{stamp}.md"
    report.parent.mkdir(exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nPass rate: {passed / len(rows):.0%}  ->  {report}")
    return passed / len(rows)


if __name__ == "__main__":
    main(sys.argv[1:])
