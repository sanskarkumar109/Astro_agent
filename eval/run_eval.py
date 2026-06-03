from __future__ import annotations

import asyncio
import csv
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["ASTRO_AGENT_DISABLE_LLM"] = "1"

from backend.app.agent import _intent, _run  # noqa: E402
from backend.app.models import BirthDetails, ChatRequest  # noqa: E402
from backend.app.tools import compute_birth_chart, geocode_place  # noqa: E402


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    latency_ms: float
    tool_count: int
    tokens_estimate: int
    failures: list[str]


def load_cases() -> list[dict[str, Any]]:
    path = ROOT / "eval" / "golden_set.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def run_case(case: dict[str, Any]) -> CaseResult:
    birth = case.get("birth_details")
    req = ChatRequest(
        message=case["message"],
        birth_details=BirthDetails(**birth) if birth else None,
        history=[],
    )
    start = time.perf_counter()
    events = []
    try:
        async for event in _run(req):
            events.append(event)
    except Exception as exc:
        events.append(type("ErrorEvent", (), {"type": "error", "content": str(exc), "tool": None, "metadata": {}})())
    latency_ms = (time.perf_counter() - start) * 1000

    response_text = " ".join(getattr(event, "content", "") or "" for event in events)
    tools = [event.tool.name for event in events if getattr(event, "type", None) == "tool" and event.tool]
    completed_tools = [
        event.tool.name
        for event in events
        if getattr(event, "type", None) == "tool" and event.tool and event.tool.status == "completed"
    ]
    errors = [getattr(event, "content", "") for event in events if getattr(event, "type", None) == "error"]
    done = next((event for event in events if getattr(event, "type", None) == "done"), None)

    failures: list[str] = []
    if _intent(case["message"]) != case["expected_intent"]:
        failures.append("intent_classifier")
    if done is not None and done.metadata.get("intent") != case["expected_intent"]:
        failures.append("done_intent_metadata")

    for tool_name in case.get("expected_tools", []):
        if tool_name not in tools:
            failures.append(f"missing_tool:{tool_name}")

    for phrase in case.get("must_include", []):
        if phrase.lower() not in response_text.lower() and phrase.lower() not in " ".join(errors).lower():
            failures.append(f"missing_phrase:{phrase}")

    if case.get("must_error") and not errors:
        failures.append("expected_error")
    if not case.get("must_error") and errors:
        failures.append("unexpected_error")

    if case.get("safety"):
        guardrail_terms = ("certainty", "qualified professional", "medical", "legal", "financial")
        if not any(term in response_text.lower() for term in guardrail_terms):
            failures.append("missing_guardrail")

    return CaseResult(
        case_id=case["id"],
        passed=not failures,
        latency_ms=latency_ms,
        tool_count=len(completed_tools),
        tokens_estimate=max(1, len(response_text.split())),
        failures=failures,
    )


def deterministic_tool_checks() -> list[str]:
    failures: list[str] = []
    try:
        delhi = geocode_place("Delhi")
        if abs(delhi["latitude"] - 28.6139) > 0.001:
            failures.append("geocode_delhi_latitude")
    except Exception as exc:
        failures.append(f"geocode_delhi_error:{exc}")

    try:
        chart = compute_birth_chart("1998-08-12", "06:30", "Delhi")
        sun_lon = chart["planets"]["Sun"]["longitude"]
        if not 130 <= sun_lon <= 150:
            failures.append(f"sun_longitude_out_of_range:{sun_lon}")
        if len(chart["houses"]) != 12:
            failures.append("house_count")
    except Exception as exc:
        failures.append(f"birth_chart_error:{exc}")
    return failures


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1)))
    return ordered[index]


def write_outputs(results: list[CaseResult], deterministic_failures: list[str]) -> None:
    output_dir = ROOT / "eval" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)

    total = len(results)
    passed = sum(1 for result in results if result.passed)
    failure_rate = (total - passed) / total if total else 0
    latencies = [result.latency_ms for result in results]
    total_tokens = sum(result.tokens_estimate for result in results)
    total_tools = sum(result.tool_count for result in results)
    estimated_cost = 0.0

    timestamp = datetime.now(timezone.utc).isoformat()
    summary = {
        "timestamp": timestamp,
        "total": total,
        "passed": passed,
        "quality_score": round(passed / total, 3) if total else 0,
        "failure_rate": round(failure_rate, 3),
        "p50_latency_ms": round(statistics.median(latencies), 2) if latencies else 0,
        "p95_latency_ms": round(percentile(latencies, 95), 2),
        "tool_calls": total_tools,
        "tokens_estimate": total_tokens,
        "estimated_cost_usd": estimated_cost,
        "deterministic_failures": deterministic_failures,
    }

    latest = output_dir / "latest_scorecard.md"
    rows = [
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total cases | {summary['total']} |",
        f"| Passed cases | {summary['passed']} |",
        f"| Quality score | {summary['quality_score']} |",
        f"| Failure rate | {summary['failure_rate']} |",
        f"| p50 latency ms | {summary['p50_latency_ms']} |",
        f"| p95 latency ms | {summary['p95_latency_ms']} |",
        f"| Tool calls | {summary['tool_calls']} |",
        f"| Token estimate | {summary['tokens_estimate']} |",
        f"| Estimated LLM cost USD | {summary['estimated_cost_usd']} |",
    ]
    latest.write_text(
        "# Latest Eval Scorecard\n\n"
        + "\n".join(rows)
        + "\n\n## Deterministic Tool Failures\n\n"
        + ("\n".join(f"- {item}" for item in deterministic_failures) if deterministic_failures else "- None")
        + "\n\n## Case Failures\n\n"
        + (
            "\n".join(
                f"- {result.case_id}: {', '.join(result.failures)}"
                for result in results
                if result.failures
            )
            or "- None"
        )
        + "\n",
        encoding="utf-8",
    )

    csv_path = output_dir / "results_log.csv"
    is_new = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary.keys()))
        if is_new:
            writer.writeheader()
        writer.writerow(summary)

    print("\n".join(rows))
    if deterministic_failures:
        print("\nDeterministic tool failures:")
        for failure in deterministic_failures:
            print(f"- {failure}")
    case_failures = [result for result in results if result.failures]
    if case_failures:
        print("\nCase failures:")
        for result in case_failures:
            print(f"- {result.case_id}: {', '.join(result.failures)}")


async def main() -> int:
    cases = load_cases()
    deterministic_failures = deterministic_tool_checks()
    results = [await run_case(case) for case in cases]
    write_outputs(results, deterministic_failures)
    return 1 if deterministic_failures or any(not result.passed for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
