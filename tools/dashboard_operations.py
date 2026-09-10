"""Bounded background operations for the local dashboard.

Long-running source refreshes and commute recalculations must not occupy the
dashboard's publication lock.  This registry supplies single-flight execution,
cooperative cancellation, bounded history, and a small JSON-safe status model
without introducing another service or dependency.
"""

from __future__ import annotations

import secrets
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


class OperationCancelled(RuntimeError):
    """Raised by a task at a safe cancellation checkpoint."""


class OperationCapacityError(RuntimeError):
    """Raised when the bounded registry has no room for another operation."""


ProgressCallback = Callable[[int, str, dict[str, Any] | None], None]
OperationTask = Callable[[ProgressCallback], dict[str, Any]]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class _Operation:
    operation_id: str
    kind: str
    key: str
    status: str = "queued"
    progress: int = 0
    message: str = "Queued"
    detail: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str = ""
    created_at: str = field(default_factory=_timestamp)
    started_at: str = ""
    finished_at: str = ""
    cancel_requested: bool = False
    future: Future[None] | None = None


class OperationRegistry:
    """Run a bounded number of local operations with per-key single flight."""

    def __init__(self, *, max_workers: int = 2, max_history: int = 32) -> None:
        if max_workers < 1 or max_history < 1:
            raise ValueError("operation limits must be positive")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="jobscout-dashboard",
        )
        self._max_history = max_history
        self._lock = threading.RLock()
        self._operations: dict[str, _Operation] = {}

    def start(self, *, kind: str, key: str, task: OperationTask) -> dict[str, Any]:
        with self._lock:
            for operation in self._operations.values():
                if operation.key == key and operation.status in {"queued", "running"}:
                    return self._public(operation)
            self._prune_locked()
            if len(self._operations) >= self._max_history:
                raise OperationCapacityError(
                    "Too many dashboard operations are already queued or running"
                )
            operation = _Operation(
                operation_id=secrets.token_hex(10),
                kind=str(kind),
                key=str(key),
            )
            self._operations[operation.operation_id] = operation
            operation.future = self._executor.submit(self._run, operation, task)
            return self._public(operation)

    def _run(self, operation: _Operation, task: OperationTask) -> None:
        with self._lock:
            if operation.cancel_requested:
                self._finish_cancelled_locked(operation)
                return
            operation.status = "running"
            operation.started_at = _timestamp()
            operation.message = "Starting"

        def progress(
            percent: int,
            message: str,
            detail: dict[str, Any] | None = None,
        ) -> None:
            with self._lock:
                if operation.cancel_requested:
                    raise OperationCancelled("Operation cancelled")
                operation.progress = max(
                    operation.progress,
                    min(99, max(0, int(percent))),
                )
                operation.message = " ".join(str(message or "Working").split())[:240]
                operation.detail = dict(detail or {})

        try:
            result = task(progress)
        except OperationCancelled:
            with self._lock:
                self._finish_cancelled_locked(operation)
        except Exception as exc:
            with self._lock:
                operation.status = "failed"
                operation.error = " ".join(str(exc).split())[:500] or type(exc).__name__
                operation.message = "Operation failed"
                operation.finished_at = _timestamp()
        else:
            with self._lock:
                operation.status = "succeeded"
                operation.progress = 100
                operation.message = "Complete"
                operation.result = dict(result)
                operation.finished_at = _timestamp()

    @staticmethod
    def _finish_cancelled_locked(operation: _Operation) -> None:
        operation.status = "cancelled"
        operation.message = "Cancelled"
        operation.finished_at = _timestamp()

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            operation = self._operations.get(str(operation_id))
            return self._public(operation) if operation is not None else None

    def cancel(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            operation = self._operations.get(str(operation_id))
            if operation is None:
                return None
            if operation.status not in {"queued", "running"}:
                return self._public(operation)
            operation.cancel_requested = True
            if operation.future is not None and operation.future.cancel():
                self._finish_cancelled_locked(operation)
            else:
                operation.message = "Cancelling after the current request"
            return self._public(operation)

    def _prune_locked(self) -> None:
        finished = sorted(
            (
                operation
                for operation in self._operations.values()
                if operation.status in {"succeeded", "failed", "cancelled"}
            ),
            key=lambda item: item.finished_at or item.created_at,
        )
        while len(self._operations) >= self._max_history and finished:
            expired = finished.pop(0)
            self._operations.pop(expired.operation_id, None)

    @staticmethod
    def _public(operation: _Operation) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operation_id": operation.operation_id,
            "kind": operation.kind,
            "status": operation.status,
            "progress": operation.progress,
            "message": operation.message,
            "detail": dict(operation.detail),
            "created_at": operation.created_at,
            "started_at": operation.started_at,
            "finished_at": operation.finished_at,
            "cancel_requested": operation.cancel_requested,
        }
        if operation.status == "succeeded" and operation.result is not None:
            payload["result"] = operation.result
        if operation.status == "failed":
            payload["error"] = operation.error
        return payload

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
