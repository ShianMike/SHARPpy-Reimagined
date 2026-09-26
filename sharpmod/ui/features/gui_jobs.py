"""A small shared presentation contract for existing background workers.

This widget does not schedule work or own threads. Feature owners keep their
existing workers, request tokens, partial results, and retry boundaries. The
snapshot only describes that work and rejects superseded/terminal updates.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.ui.features.gui_common import action_label, make_status_label, set_status_label
from sharpmod.ui.styles.theme import OBJ_GHOST, SPACE


_UNSET = object()
_ACTIVE = frozenset(("running", "cancelling"))
_TERMINAL = frozenset(("completed", "partial", "cancelled", "failed"))


def _compact_text(value, limit=112):
    """Keep long paths/identities readable without losing their endpoints."""
    text = str(value)
    if len(text) <= limit:
        return text
    head = max(24, limit // 3)
    tail = max(32, limit - head - 1)
    return f"{text[:head]}…{text[-tail:]}"


def _count(value, name):
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a nonnegative integer") from exc
    if isinstance(value, bool) or number != value or number < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return number


@dataclass(frozen=True)
class JobCounts:
    """Terminal work outcomes; no count is inferred from a progress fraction."""

    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    unavailable: int = 0
    requested: int | None = None
    unknown: int = field(default=0, kw_only=True)

    def __post_init__(self):
        for name in ("completed", "failed", "cancelled", "unavailable", "unknown"):
            object.__setattr__(self, name, _count(getattr(self, name), name))
        if self.requested is not None:
            object.__setattr__(self, "requested", _count(self.requested, "requested"))
            if self.resolved > self.requested:
                raise ValueError("Outcome counts exceed requested work")

    @property
    def resolved(self):
        return self.completed + self.failed + self.cancelled + self.unavailable + self.unknown

    @property
    def unattempted(self):
        return None if self.requested is None else self.requested - self.resolved

    @property
    def summary(self):
        text = (
            f"Completed {self.completed}; failed {self.failed}; "
            f"cancelled {self.cancelled}; unavailable {self.unavailable}"
        )
        if self.unknown:
            text += f"; outcome unknown {self.unknown}"
        if self.requested is not None:
            text += f"; requested {self.requested}"
            if self.unattempted:
                text += f"; not attempted {self.unattempted}"
        return text + "."


@dataclass(frozen=True)
class JobSnapshot:
    token: object
    operation: str
    affected_input: str
    stage: str
    state: str = "running"
    done: int = 0
    total: int | None = None
    unit: str = "items"
    retained: str = ""
    counts: JobCounts = field(default_factory=JobCounts)
    message: str = ""
    cancellable: bool = False
    retryable: bool = False


class JobStatus(QWidget):
    """Input, stage, honest progress, retained identity, and terminal outcome."""

    cancelRequested = Signal()
    retryRequested = Signal()
    snapshotChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("backgroundJobStatus")
        self.setAccessibleName("Background operation status")
        self.snapshot: JobSnapshot | None = None
        self._compact_terminal = False
        self._details_expanded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        layout.setSpacing(SPACE["xs"])

        header = QHBoxLayout()
        header.setSpacing(SPACE["sm"])
        self.input_label = self._label("backgroundJobInput")
        self.input_label.setObjectName("backgroundJobInput")
        header.addWidget(self.input_label, 1)
        actions = QHBoxLayout()
        actions.setSpacing(SPACE["xs"])
        self.status = make_status_label(parent=self)
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status.setObjectName("backgroundJobMessage")
        self.progress = QProgressBar(self)
        self.progress.setObjectName("backgroundJobProgress")
        self.progress.setAccessibleName("Known work progress")
        self.progress.setTextVisible(False)
        self.progress_label = self._label("backgroundJobProgressText")
        self.progress_label.setMinimumWidth(0)
        self.count_label = self._label("backgroundJobCounts")
        self.retained_label = self._label("backgroundJobRetained")
        self.cancel_button = QPushButton(action_label("cancel"), self)
        self.cancel_button.setObjectName(OBJ_GHOST)
        self.cancel_button.setAccessibleName("Cancel this background operation")
        self.cancel_button.setToolTip("Request cooperative cancellation; keep completed results")
        self.retry_button = QPushButton("Retry missing / failed", self)
        self.retry_button.setObjectName(OBJ_GHOST)
        self.retry_button.setAccessibleName("Retry this operation's missing or failed work")
        self.retry_button.setToolTip("Retry only the operation's saved missing or failed inputs")
        self.details_button = QToolButton(self)
        self.details_button.setObjectName("backgroundJobDetails")
        self.details_button.setText("Details")
        self.details_button.setCheckable(True)
        self.details_button.setAccessibleName("Show completed job details")
        self.details_button.hide()
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.retry_button)
        actions.addWidget(self.details_button)
        actions.addStretch(1)
        layout.addLayout(header)
        layout.addWidget(self.status)
        layout.addLayout(actions)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(SPACE["sm"])
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)
        layout.addWidget(self.count_label)
        layout.addWidget(self.retained_label)
        self.cancel_button.clicked.connect(self._cancel_clicked)
        self.retry_button.clicked.connect(self.retryRequested)
        self.details_button.toggled.connect(self._set_details_expanded)
        self.hide()

    def set_compact_terminal(self, enabled=True):
        """Keep successful work to one summary line until details are requested."""
        self._compact_terminal = bool(enabled)
        if self.snapshot is not None:
            self._render()

    def _set_details_expanded(self, expanded):
        self._details_expanded = bool(expanded)
        if self.snapshot is not None:
            self._render()

    def _label(self, name, layout=None):
        label = QLabel(self)
        label.setObjectName(name)
        label.setAccessibleName(name)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        if layout is not None:
            layout.addWidget(label)
        return label

    def begin(
        self, token, operation, affected_input, stage, *, total=None, unit="items",
        retained="", cancellable=False,
    ):
        if total is not None:
            total = _count(total, "total")
        self.snapshot = JobSnapshot(
            token, str(operation), str(affected_input), str(stage), total=total,
            unit=str(unit), retained=str(retained), cancellable=bool(cancellable),
        )
        self._details_expanded = False
        self.details_button.setChecked(False)
        self.show()
        self._render()
        return token

    def accepts(self, token):
        snapshot = self.snapshot
        return bool(snapshot and token == snapshot.token and snapshot.state in _ACTIVE)

    def update(self, token, *, stage=None, done=_UNSET, total=_UNSET, retained=None):
        if not self.accepts(token):
            return False
        changes = {}
        if stage is not None:
            changes["stage"] = str(stage)
        if done is not _UNSET:
            changes["done"] = _count(done, "processed")
        if total is not _UNSET:
            changes["total"] = None if total is None else _count(total, "total")
        if retained is not None:
            changes["retained"] = str(retained)
        snapshot = replace(self.snapshot, **changes)
        if snapshot.total is not None and snapshot.done > snapshot.total:
            raise ValueError("Processed count exceeds known total")
        self.snapshot = snapshot
        self._render()
        return True

    def request_cancel(self, token=None):
        snapshot = self.snapshot
        if snapshot is None:
            return False
        token = snapshot.token if token is None else token
        if not self.accepts(token) or snapshot.state == "cancelling":
            return False
        self.snapshot = replace(snapshot, state="cancelling")
        self._render()
        return True

    def _cancel_clicked(self):
        if self.request_cancel():
            self.cancelRequested.emit()

    def finish(self, token, *, counts, outcome=None, message="", retryable=False):
        if not self.accepts(token):
            return False
        if not isinstance(counts, JobCounts):
            raise TypeError("counts must be JobCounts")
        if outcome is None:
            outcome = (
                "cancelled" if counts.cancelled
                else "partial" if counts.failed and counts.completed
                else "failed" if counts.failed
                else "partial" if counts.unavailable or counts.unknown or counts.unattempted
                else "completed"
            )
        if outcome not in _TERMINAL:
            raise ValueError(f"Unknown terminal job state: {outcome}")
        self.snapshot = replace(
            self.snapshot, state=outcome, counts=counts, message=str(message),
            retryable=bool(retryable), cancellable=False,
        )
        self._render()
        return True

    def invalidate(self, message="This request was superseded by a newer input."):
        if self.snapshot is None:
            return
        self.snapshot = replace(
            self.snapshot, state="superseded", message=str(message),
            cancellable=False, retryable=False,
        )
        self._render()

    def _render(self):
        snapshot = self.snapshot
        if snapshot is None:
            return
        active = snapshot.state in _ACTIVE
        full_input = f"{snapshot.operation} · {snapshot.affected_input}"
        self.input_label.setText(_compact_text(full_input))
        self.input_label.setToolTip(full_input)
        self.input_label.setAccessibleDescription(full_input)
        if snapshot.state == "cancelling":
            message = "Cancel requested; waiting for the current bounded operation."
        elif active:
            message = f"{snapshot.stage}…"
        else:
            message = snapshot.message or snapshot.state.capitalize() + "."
        level = (
            "error" if snapshot.state == "failed"
            else "warn" if snapshot.state in {"partial", "cancelled", "cancelling", "superseded"}
            else "info"
        )
        set_status_label(self.status, message, level=level)
        self.progress.setVisible(active and snapshot.total != 0)
        self.progress_label.setVisible(active)
        if snapshot.total is None:
            self.progress.setRange(0, 0)
            progress_text = f"{snapshot.done} processed; total unknown."
        else:
            self.progress.setRange(0, max(1, snapshot.total))
            self.progress.setValue(snapshot.done)
            progress_text = f"{snapshot.done} / {snapshot.total} {snapshot.unit}."
        self.progress_label.setText(progress_text)
        self.progress.setAccessibleDescription(progress_text)
        self.count_label.setText(snapshot.counts.summary)
        self.count_label.setVisible(snapshot.state in _TERMINAL)
        full_retained = f"Retained result: {snapshot.retained}" if snapshot.retained else ""
        self.retained_label.setText(_compact_text(full_retained))
        self.retained_label.setToolTip(full_retained)
        self.retained_label.setAccessibleDescription(full_retained)
        self.retained_label.setVisible(bool(full_retained))
        self.cancel_button.setVisible(active and snapshot.cancellable)
        self.cancel_button.setEnabled(snapshot.state == "running" and snapshot.cancellable)
        self.retry_button.setVisible(not active and snapshot.retryable)
        self.retry_button.setEnabled(not active and snapshot.retryable)
        compact = self._compact_terminal and snapshot.state == "completed"
        show_details = not compact or self._details_expanded
        self.details_button.setVisible(compact)
        self.details_button.setText(
            "Hide details" if self._details_expanded else "Details"
        )
        self.input_label.setVisible(show_details)
        self.count_label.setVisible(show_details and snapshot.state in _TERMINAL)
        self.retained_label.setVisible(show_details and bool(full_retained))
        description = " · ".join(
            part for part in (
                full_input, snapshot.state, message,
                progress_text if active else self.count_label.text(), full_retained,
            ) if part
        )
        self.setAccessibleDescription(description)
        self.setToolTip(description)
        self.snapshotChanged.emit(snapshot)


__all__ = ["JobCounts", "JobSnapshot", "JobStatus"]
