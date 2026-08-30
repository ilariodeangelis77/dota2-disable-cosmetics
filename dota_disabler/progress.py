"""Weighted, monotonic progress reporting for long-running build work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .domain import ProgressUpdateCallback, WorkProgressCallback


# All progress allocation lives here. Pipeline code reports semantic phase names and
# completed work units; adding or reweighting a phase does not scatter percentage math.
BUILD_PHASE_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("validation", 4),
    ("schema_extract", 6),
    ("schema_parse", 3),
    ("planning", 7),
    ("source_extract", 22),
    ("model_analysis", 5),
    ("material_extract", 3),
    ("particle_validation", 4),
    ("deployment", 44),
    ("finalize", 2),
)

DEPLOYMENT_PHASE_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("staging", 14),
    ("model_patch", 12),
    ("language_extract", 3),
    ("language_stage", 2),
    ("pack", 7),
    ("verify", 6),
    ("install", 2),
)


@dataclass(frozen=True)
class _PhaseSpan:
    start: float
    end: float


class WeightedProgress:
    """Map named work-unit phases onto monotonic overall percentage updates."""

    def __init__(
        self,
        callback: Optional[ProgressUpdateCallback],
        phase_weights: Iterable[tuple[str, float]],
        *,
        minimum_delta: float = 0.1,
    ) -> None:
        weighted = tuple(phase_weights)
        if not weighted or any(weight <= 0 for _name, weight in weighted):
            raise ValueError("Progress phase weights must be positive.")
        names = [name for name, _weight in weighted]
        if len(names) != len(set(names)):
            raise ValueError("Progress phase names must be unique.")

        total = sum(weight for _name, weight in weighted)
        cursor = 0.0
        spans: dict[str, _PhaseSpan] = {}
        for name, weight in weighted:
            start = cursor * 100.0 / total
            cursor += weight
            spans[name] = _PhaseSpan(start, cursor * 100.0 / total)

        self._callback = callback
        self._spans = spans
        self._minimum_delta = max(0.0, minimum_delta)
        self._last_value = 0.0
        self._last_emitted = -self._minimum_delta

    def begin(self, phase: str, message: str) -> None:
        self._emit(self._span(phase).start, message, force=True)

    def complete(self, phase: str, message: str) -> None:
        self._emit(self._span(phase).end, message, force=True)

    def work(
        self,
        phase: str,
        completed: int,
        total: int,
        message: str,
    ) -> None:
        span = self._span(phase)
        ratio = 1.0 if total <= 0 else max(0.0, min(1.0, completed / total))
        self._emit(span.start + (span.end - span.start) * ratio, message)

    def work_callback(self, phase: str, label: str) -> WorkProgressCallback:
        def update(_operation: str, completed: int, total: int) -> None:
            self.work(
                phase,
                completed,
                total,
                f"{label} ({completed:,} of {total:,})",
            )

        return update

    def child_callback(self, phase: str) -> ProgressUpdateCallback:
        span = self._span(phase)

        def update(percent: float, message: str) -> None:
            ratio = max(0.0, min(100.0, percent)) / 100.0
            self._emit(span.start + (span.end - span.start) * ratio, message)

        return update

    def _span(self, phase: str) -> _PhaseSpan:
        try:
            return self._spans[phase]
        except KeyError as exc:
            raise ValueError(f"Unknown progress phase: {phase}") from exc

    def _emit(self, value: float, message: str, *, force: bool = False) -> None:
        value = max(self._last_value, min(100.0, max(0.0, value)))
        self._last_value = value
        if self._callback is None:
            return
        if not force and value < 100.0 and value - self._last_emitted < self._minimum_delta:
            return
        self._last_emitted = value
        self._callback(value, message)


__all__ = [
    "BUILD_PHASE_WEIGHTS",
    "DEPLOYMENT_PHASE_WEIGHTS",
    "WeightedProgress",
]
