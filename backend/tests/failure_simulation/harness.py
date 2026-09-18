"""Run the failure-simulation suite and emit a PASS/FAIL report.

Usage::

    python -m tests.failure_simulation.harness

Writes ``tests/failure_simulation/REPORT.md`` with one row per scenario
(scenario name, status, notes) and an overall pass/fail summary. Each
scenario is mapped to a single top-level test function in this
package; the harness picks them up by walking the package.

This script never modifies production code. It only collects, runs,
and reports.
"""

from __future__ import annotations

import importlib
import inspect
import io
import json
import pkgutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPORT_PATH = HERE / "REPORT.md"
JUNIT_PATH = HERE / "results.xml"

# Friendly scenario names keyed by (module, test_function).
SCENARIOS: list[tuple[str, str, str]] = [
    (
        "tests.failure_simulation.test_invalid_json",
        "test_invalid_json_triggers_repair_or_fallback",
        "LLM returns invalid JSON -> repair or fallback",
    ),
    (
        "tests.failure_simulation.test_quota_429",
        "test_primary_429_retries_then_succeeds",
        "Primary model gives 429 -> next model used",
    ),
    (
        "tests.failure_simulation.test_all_unavailable",
        "test_all_models_unavailable_returns_safe_no_op",
        "All models unavailable -> controlled error",
    ),
    (
        "tests.failure_simulation.test_all_unavailable",
        "test_no_provider_chain_at_all_is_safe_no_op",
        "All models unavailable -> no provider chain",
    ),
    (
        "tests.failure_simulation.test_invalid_schedule",
        "test_optimizer_returns_invalid_schedule_is_rejected_by_verifier",
        "Optimizer creates invalid schedule -> validator catches it",
    ),
    (
        "tests.failure_simulation.test_wrong_directive_type",
        "test_unknown_directive_type_is_replaced_with_no_op",
        "Wrong directive type returned -> guardrail rejection",
    ),
    (
        "tests.failure_simulation.test_wrong_directive_type",
        "test_numeric_out_of_bounds_directive_is_replaced_with_no_op",
        "Numeric out-of-bounds directive -> guardrail rejection",
    ),
]


@dataclass
class Result:
    name: str
    description: str
    status: str  # PASS or FAIL
    duration_seconds: float
    detail: str


def _run_pytest() -> None:
    """Invoke pytest on the failure_simulation package, emitting JUnit XML."""
    # Use ``--rootdir`` pointing at the backend folder so the ``tests``
    # namespace package is importable; ``--junit-xml`` for downstream
    # parsing; ``-p no:cacheprovider`` to avoid stale .pytest_cache issues.
    cmd = [
        sys.executable, "-m", "pytest",
        str(HERE),
        "-v", "--tb=short", "--no-header",
        "-p", "no:cacheprovider",
        f"--junit-xml={JUNIT_PATH}",
        "--rootdir", str(HERE.parent.parent),
    ]
    # Allow the failing-suite path so failures don't return nonzero; we
    # parse JUnit XML and exit 0 ourselves.
    subprocess.run(cmd, cwd=str(HERE.parent.parent), check=False)


def _parse_results() -> list[Result]:
    if not JUNIT_PATH.exists():
        raise SystemExit(
            f"JUnit XML was not produced at {JUNIT_PATH}; "
            "pytest did not run."
        )
    tree = ET.parse(JUNIT_PATH)
    root = tree.getroot()
    cases_by_full_name: dict[str, ET.Element] = {}
    for suite in root.findall("testsuite"):
        for case in suite.findall("testcase"):
            full = f"{case.attrib.get('classname','')}.{case.attrib.get('name','')}"
            cases_by_full_name[full] = case

    results: list[Result] = []
    for module_name, fn_name, description in SCENARIOS:
        # pytest's classname is the dotted module path; the test name is fn.
        full_name = f"{module_name}.{fn_name}"
        case = cases_by_full_name.get(full_name)
        if case is None:
            results.append(Result(
                name=full_name, description=description,
                status="FAIL", duration_seconds=0.0,
                detail="Test was not collected by pytest.",
            ))
            continue
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        duration = float(case.attrib.get("time", "0") or 0.0)
        if failure is not None:
            message = (failure.attrib.get("message") or "").strip()
            results.append(Result(
                name=full_name, description=description,
                status="FAIL", duration_seconds=duration,
                detail=message or "see pytest output",
            ))
        elif error is not None:
            message = (error.attrib.get("message") or "").strip()
            results.append(Result(
                name=full_name, description=description,
                status="FAIL", duration_seconds=duration,
                detail=message or "see pytest output",
            ))
        elif skipped is not None:
            message = (skipped.attrib.get("message") or "").strip()
            results.append(Result(
                name=full_name, description=description,
                status="FAIL", duration_seconds=duration,
                detail=f"skipped: {message}",
            ))
        else:
            results.append(Result(
                name=full_name, description=description,
                status="PASS", duration_seconds=duration, detail="",
            ))
    return results


def _render_markdown(results: list[Result]) -> str:
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    failed = total - passed
    overall = "PASS" if failed == 0 else "FAIL"

    lines: list[str] = []
    lines.append("# Failure Simulation Report")
    lines.append("")
    lines.append(
        "End-to-end failure scenarios for the GridWise ``POST /optimize-energy`` "
        "pipeline. Each scenario exercises the real FastAPI route with a "
        "scripted fake LLM and asserts the documented fail-safe contract."
    )
    lines.append("")
    lines.append(f"- **Overall:** **{overall}**")
    lines.append(f"- **Total scenarios:** {total}")
    lines.append(f"- **Passed:** {passed}")
    lines.append(f"- **Failed:** {failed}")
    lines.append("")
    lines.append("| # | Scenario | Description | Status | Duration (s) | Notes |")
    lines.append("|---|----------|-------------|--------|--------------|-------|")
    for i, r in enumerate(results, 1):
        detail = r.detail.replace("|", "\\|").replace("\n", " ")
        if len(detail) > 200:
            detail = detail[:197] + "..."
        lines.append(
            f"| {i} | `{r.name.split('.')[-1]}` "
            f"| {r.description} | **{r.status}** "
            f"| {r.duration_seconds:.2f} | {detail} |"
        )
    lines.append("")
    if failed:
        lines.append("## Failures")
        lines.append("")
        for r in results:
            if r.status != "PASS":
                lines.append(f"### `{r.name}`")
                lines.append("")
                lines.append(f"- **Description:** {r.description}")
                lines.append(f"- **Detail:** {r.detail}")
                lines.append("")
    lines.append("## How to reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append("cd backend")
    lines.append("python -m tests.failure_simulation.harness")
    lines.append("```")
    lines.append("")
    lines.append(
        "Each scenario is also runnable directly with ``pytest``:"
    )
    lines.append("")
    lines.append("```bash")
    lines.append("cd backend")
    lines.append("python -m pytest tests/failure_simulation -v")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    print(f"Running {len(SCENARIOS)} failure-simulation scenarios...")
    _run_pytest()
    results = _parse_results()
    markdown = _render_markdown(results)
    REPORT_PATH.write_text(markdown, encoding="utf-8")
    failed = sum(1 for r in results if r.status != "PASS")
    print(markdown)
    print(f"\nReport written to {REPORT_PATH}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
