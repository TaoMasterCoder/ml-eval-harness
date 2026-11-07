#!/usr/bin/env python3
"""Multi-model evaluation harness skeleton."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Model adapter (stub — replace with real API / delegate_task integration)
# ---------------------------------------------------------------------------

def _mock_response(model: str, task_id: str, prompt: str) -> str:
    """Deterministic mock answers for models named fake-*."""
    sleep_match = re.search(r"__SLEEP__(\d+(?:\.\d+)?)", prompt)
    if sleep_match:
        time.sleep(float(sleep_match.group(1)))

    table: dict[str, dict[str, str]] = {
        "t-exact": {"fake-a": "42", "fake-b": "41"},
        "t-contains": {
            "fake-a": "The operation completed with success.",
            "fake-b": "failed",
        },
        "t-numeric": {"fake-a": "99.6", "fake-b": "50"},
        "t-timeout": {"fake-a": "done", "fake-b": "done"},
    }
    by_model = table.get(task_id, {})
    if model in by_model:
        return by_model[model]
    return f"mock:{model}:{task_id}"


def call_model(model: str, prompt: str, **kwargs: Any) -> str:
    """Invoke a model with prompt; returns raw text output."""
    if model.startswith("fake-"):
        return _mock_response(model, kwargs.get("task_id", ""), prompt)
    raise NotImplementedError(
        f"No adapter for model {model!r}; implement call_model or use fake-* mocks."
    )


# ---------------------------------------------------------------------------
# Checkers
# ---------------------------------------------------------------------------

_CUSTOM_HANDLERS: dict[str, Callable[[Any, str], bool]] = {}


def register_custom_checker(name: str, fn: Callable[[Any, str], bool]) -> None:
    _CUSTOM_HANDLERS[name] = fn


def _parse_number(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def check_result(expected: Any, actual: str, check_type: str) -> bool:
    if check_type == "exact":
        return actual.strip() == str(expected).strip()
    if check_type == "contains":
        return str(expected) in actual
    if check_type == "numeric_tolerance":
        if isinstance(expected, dict):
            target = float(expected["value"])
            tol = float(expected.get("tolerance", 0))
        elif isinstance(expected, (list, tuple)) and len(expected) >= 2:
            target, tol = float(expected[0]), float(expected[1])
        else:
            raise ValueError(
                "numeric_tolerance expects {value, tolerance} or [value, tolerance]"
            )
        got = _parse_number(actual)
        if got is None:
            return False
        return abs(got - target) <= tol
    if check_type == "custom":
        if isinstance(expected, str) and expected in _CUSTOM_HANDLERS:
            return _CUSTOM_HANDLERS[expected](expected, actual)
        if "custom" in _CUSTOM_HANDLERS:
            return _CUSTOM_HANDLERS["custom"](expected, actual)
        return False
    raise ValueError(f"Unknown check_type: {check_type!r}")


# ---------------------------------------------------------------------------
# Task I/O
# ---------------------------------------------------------------------------

def load_tasks(path: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {e}") from e
            for key in ("id", "prompt", "expected", "check_type"):
                if key not in obj:
                    raise ValueError(f"{path}:{lineno}: missing field {key!r}")
            tasks.append(obj)
    return tasks


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

@dataclass
class TaskOutcome:
    task_id: str
    model: str
    passed: bool
    actual: str
    status: str  # OK | TIMEOUT | ERROR
    latency_s: float
    error: str | None = None


@dataclass
class ModelStats:
    model: str
    passed: int = 0
    total: int = 0
    total_latency_s: float = 0.0
    outcomes: list[TaskOutcome] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


async def _call_one(
    model: str,
    task: dict[str, Any],
    timeout_s: float,
) -> TaskOutcome:
    task_id = task["id"]
    t0 = time.perf_counter()
    try:
        actual = await asyncio.wait_for(
            asyncio.to_thread(
                call_model,
                model,
                task["prompt"],
                task_id=task_id,
            ),
            timeout=timeout_s,
        )
        latency = time.perf_counter() - t0
        passed = check_result(task["expected"], actual, task["check_type"])
        return TaskOutcome(
            task_id=task_id,
            model=model,
            passed=passed,
            actual=actual,
            status="OK",
            latency_s=latency,
        )
    except asyncio.TimeoutError:
        latency = time.perf_counter() - t0
        return TaskOutcome(
            task_id=task_id,
            model=model,
            passed=False,
            actual="",
            status="TIMEOUT",
            latency_s=latency,
        )
    except Exception as e:  # noqa: BLE001 — harness records failures
        latency = time.perf_counter() - t0
        return TaskOutcome(
            task_id=task_id,
            model=model,
            passed=False,
            actual="",
            status="ERROR",
            latency_s=latency,
            error=str(e),
        )


async def run_harness(
    tasks: list[dict[str, Any]],
    models: list[str],
    task_timeout_s: float,
) -> dict[str, ModelStats]:
    stats = {m: ModelStats(model=m) for m in models}
    for task in tasks:
        outcomes = await asyncio.gather(
            *[_call_one(m, task, task_timeout_s) for m in models]
        )
        for o in outcomes:
            s = stats[o.model]
            s.total += 1
            s.total_latency_s += o.latency_s
            if o.passed:
                s.passed += 1
            s.outcomes.append(o)
    return stats


def write_results(
    output_dir: Path,
    run_id: str,
    stats: dict[str, ModelStats],
    meta: dict[str, Any],
) -> Path:
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "run_id": run_id,
        "meta": meta,
        "models": {
            m: {
                "score": s.passed,
                "total": s.total,
                "pass_rate": round(s.pass_rate, 4),
                "total_latency_s": round(s.total_latency_s, 3),
            }
            for m, s in stats.items()
        },
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for m, s in stats.items():
        model_dir = run_dir / m
        model_dir.mkdir(parents=True, exist_ok=True)
        detail_path = model_dir / "detail.jsonl"
        with detail_path.open("w", encoding="utf-8") as f:
            for o in s.outcomes:
                row = {
                    "task_id": o.task_id,
                    "passed": o.passed,
                    "status": o.status,
                    "actual": o.actual,
                    "latency_s": round(o.latency_s, 3),
                }
                if o.error:
                    row["error"] = o.error
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    return run_dir


def print_score_matrix(stats: dict[str, ModelStats]) -> None:
    parts = [
        f"{m}:{s.passed}/{s.total}({s.pass_rate:.0%})"
        for m, s in sorted(stats.items())
    ]
    print("SCORES | " + " | ".join(parts))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-model eval harness")
    p.add_argument("--tasks", type=Path, required=True, help="Path to tasks.jsonl")
    p.add_argument(
        "--models",
        required=True,
        help="Comma-separated model names",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("results"),
        help="Output base directory (default: results/)",
    )
    p.add_argument(
        "--task-timeout",
        type=float,
        default=300.0,
        help="Per-task model call timeout in seconds (default: 300)",
    )
    p.add_argument(
        "--run-id",
        default="",
        help="Run id subdirectory name (default: UTC timestamp)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    models = [x.strip() for x in args.models.split(",") if x.strip()]
    if not models:
        print("No models specified", file=sys.stderr)
        return 2

    tasks = load_tasks(args.tasks)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not run_id:
        run_id = uuid.uuid4().hex[:12]

    t0 = time.perf_counter()
    stats = asyncio.run(run_harness(tasks, models, args.task_timeout))
    wall_s = time.perf_counter() - t0

    meta = {
        "tasks_file": str(args.tasks.resolve()),
        "models": models,
        "task_count": len(tasks),
        "task_timeout_s": args.task_timeout,
        "wall_clock_s": round(wall_s, 3),
    }
    run_dir = write_results(args.output, run_id, stats, meta)
    print_score_matrix(stats)
    print(f"Results written to {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
