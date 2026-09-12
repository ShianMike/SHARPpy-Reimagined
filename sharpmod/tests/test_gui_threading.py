"""Process-level proof for safe Qt worker retention."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_running_qthread_survives_parent_deletion_without_terminate():
    """Deleting the window must not destroy its still-running QThread child."""
    code = textwrap.dedent(
        """
        import os
        import time

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from qtpy.QtCore import QThread
        from qtpy.QtWidgets import QApplication, QWidget
        from sharpmod.gui_threading import retain_worker_until_finished

        class Worker(QThread):
            def run(self):
                time.sleep(0.15)

        app = QApplication([])
        parent = QWidget()
        worker = Worker(parent)
        worker.start()
        assert retain_worker_until_finished(worker)
        assert worker.parent() is None
        parent.deleteLater()
        app.processEvents()
        assert worker.wait(2000)
        app.processEvents()
        print("safe-retention-ok")
        """
    )
    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "safe-retention-ok" in result.stdout
    assert "QThread: Destroyed while thread" not in result.stderr
