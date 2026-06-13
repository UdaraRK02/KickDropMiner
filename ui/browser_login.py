import json
import os
import sys
import subprocess
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QProgressBar,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from core import auth_manager

def _helper_cmd() -> list[str]:
    """
    Return the command that runs the browser login helper.
    When frozen by PyInstaller the helper is a compiled exe sitting next to
    the main executable.  During development it is run as a Python script.
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller build: helper exe is in the same folder as KickDropMiner.exe
        exe = os.path.join(os.path.dirname(sys.executable), '_browser_login_helper.exe')
        return [exe]
    # Development: run the script with the current Python interpreter
    script = os.path.join(os.path.dirname(os.path.dirname(__file__)), '_browser_login_helper.py')
    return [sys.executable, script]


class _BrowserProcess(QThread):
    """Runs the browser login helper in a subprocess and forwards the result."""
    done = pyqtSignal(dict, str)   # cookies, error_msg

    def run(self):
        try:
            proc = subprocess.run(
                _helper_cmd(),
                capture_output=True,
                text=True,
                timeout=300,   # user has 5 minutes
            )
            stdout = proc.stdout.strip()
            if proc.returncode == 0 and stdout:
                cookies = json.loads(stdout)
                if cookies.get('session_token'):
                    self.done.emit(cookies, '')
                    return
                self.done.emit({}, 'Login completed but no session token was returned')
            elif proc.returncode == 2:
                self.done.emit({}, 'pywebview is not installed — run: pip install pywebview')
            else:
                err = (proc.stderr or '').strip()
                self.done.emit({}, err or 'Login window closed without signing in')

        except subprocess.TimeoutExpired:
            self.done.emit({}, 'Login timed out (5 min)')
        except Exception as exc:
            self.done.emit({}, str(exc))


class BrowserLoginDialog(QDialog):
    login_success = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Sign in — Kick Drop Miner')
        self.setFixedSize(400, 220)
        self.setModal(True)
        self._worker: _BrowserProcess | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(12)

        title = QLabel('Sign in to Kick.com')
        title.setFont(title.font())
        title.setStyleSheet('color: #53fc18; font-size: 13pt; font-weight: bold;')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.info_lbl = QLabel(
            'A browser window will open so you can sign in.\n'
            'Google, Apple, and email login all work.'
        )
        self.info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_lbl.setWordWrap(True)
        self.info_lbl.setStyleSheet('color: #aaa; font-size: 9pt;')
        layout.addWidget(self.info_lbl)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)   # indeterminate spinner
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        self.open_btn = QPushButton('Open Login Browser')
        self.open_btn.setObjectName('primary_btn')
        layout.addWidget(self.open_btn)

        self.cancel_btn = QPushButton('Cancel')
        layout.addWidget(self.cancel_btn)

        self.open_btn.clicked.connect(self._start)
        self.cancel_btn.clicked.connect(self._cancel)

    # ------------------------------------------------------------------

    def _start(self):
        self.open_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.info_lbl.setText(
            'Browser window opened — sign in there.\n'
            'This dialog will close automatically once you\'re logged in.'
        )
        self._worker = _BrowserProcess(self)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, cookies: dict, error: str):
        self.progress.setVisible(False)
        if cookies:
            auth_manager.save_session(cookies)
            if auth_manager.get_session_token():
                self.login_success.emit(cookies)
                self.accept()
                return
            auth_manager.clear_session()
            error = 'Login completed but no usable session token was found'
        self.open_btn.setEnabled(True)
        self.info_lbl.setText(f'❌  {error}\n\nClick the button to try again.')
        self.info_lbl.setStyleSheet('color: #e74c3c; font-size: 9pt;')

    def _cancel(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(1000)
        self.reject()

    def closeEvent(self, event):
        self._cancel()
        event.accept()
