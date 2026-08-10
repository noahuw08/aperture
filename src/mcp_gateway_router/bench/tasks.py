"""The Q1 task battery: what to ask, and how to know whether it worked.

Tasks are **data, not code**, so the battery can be filled from real sessions later
without touching the harness. Every task must be:

- **read-only** — arms run repeatedly, and a task with side effects is not repeatable
- **deterministically checkable** — an exact value, never a judge. A judge is another
  model to calibrate, and this project has no spare calibration references
- **labelled with its required tools** — so `satisfied` stays comparable to the offline
  frontier, and so the oracle arm can be constructed

⚠️ **Tasks must be drawn from observed sessions, not invented.** Inventing them moves
the house failure mode out of the personas and into the task mix, where it is harder to
see. The leakage check below is the guard that makes that an assertion rather than a hope.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_WORD = re.compile(r"[a-z0-9]+")


def _terms(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


@dataclass(frozen=True)
class Task:
    task_id: str
    prompt: str
    #: Exact expected answer. Compared after normalisation, never judged.
    expect: str
    #: Ground truth for `satisfied` and for building the oracle arm.
    required_tools: tuple[tuple[str, str], ...] = ()
    #: How to compare. `contains` is the loosest that is still deterministic.
    match: str = "contains"
    notes: str = ""

    def check(self, answer: str | None) -> bool:
        if answer is None:
            return False
        got, want = _normalise(answer), _normalise(self.expect)
        if self.match == "exact":
            return got == want
        if self.match == "contains":
            return want in got
        if self.match == "number":
            # Digits only, so 1,441,792 and 1441792 agree.
            return _digits(self.expect) in _digits(answer)
        raise ValueError(f"unknown match mode {self.match!r}")


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _digits(text: str) -> str:
    return re.sub(r"[^0-9]", "", text)


@dataclass
class LeakageReport:
    task_id: str
    overlap: float
    leaks: bool = field(default=False)


def leakage(task: Task, tool_descriptions: dict[tuple[str, str], str]) -> float:
    """Fraction of the required tools' description vocabulary present in the prompt.

    A prompt that reuses the tool's own words is describing its own answer, which
    flatters every retrieval arm. Measured the same way the ToolRet analysis was:
    queries scored **7.4%**, the leaky `instruction` field **20.3%**.
    """
    if not task.required_tools:
        return 0.0
    vocabulary: set[str] = set()
    for key in task.required_tools:
        vocabulary |= _terms(tool_descriptions.get(key, ""))
    if not vocabulary:
        return 0.0
    return len(vocabulary & _terms(task.prompt)) / len(vocabulary)


def check_leakage(
    tasks: list[Task],
    tool_descriptions: dict[tuple[str, str], str],
    threshold: float = 0.074,
) -> list[LeakageReport]:
    """Flag tasks whose prompts reuse their tools' vocabulary more than a real query would.

    The default threshold is the measured ToolRet query-level baseline. Anything above it
    should be rewritten before the battery is used, not explained away afterwards.
    """
    reports = []
    for task in tasks:
        overlap = leakage(task, tool_descriptions)
        reports.append(LeakageReport(task.task_id, overlap, overlap > threshold))
    return reports


def load_tasks(path: Path) -> list[Task]:
    payload = json.loads(Path(path).read_text())
    return [
        Task(
            task_id=raw["task_id"],
            prompt=raw["prompt"],
            expect=raw["expect"],
            required_tools=tuple(tuple(k) for k in raw.get("required_tools", [])),
            match=raw.get("match", "contains"),
            notes=raw.get("notes", ""),
        )
        for raw in payload["tasks"]
    ]
