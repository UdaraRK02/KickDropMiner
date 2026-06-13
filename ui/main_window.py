import asyncio
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QTextEdit, QSplitter,
    QGroupBox, QProgressBar, QFrame, QMenu,
    QInputDialog, QMessageBox, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QAction

from core.drop_manager import DropManager, Drop
from core.miner import Miner
from core import kick_api, auth_manager
from ui.browser_login import BrowserLoginDialog

# ── dark theme stylesheet ────────────────────────────────────────────────────
DARK_STYLE = """
QMainWindow, QWidget, QDialog {
    background-color: #1a1a2e;
    color: #e0e0e0;
    font-family: 'Segoe UI', sans-serif;
    font-size: 10pt;
}
QMenuBar {
    background-color: #16213e;
    color: #e0e0e0;
    border-bottom: 1px solid #0f3460;
}
QMenuBar::item:selected { background-color: #0f3460; }
QMenu {
    background-color: #16213e;
    border: 1px solid #0f3460;
}
QMenu::item:selected { background-color: #0f3460; }

/* ── toolbar strip ── */
#toolbar {
    background-color: #16213e;
    border-bottom: 1px solid #0f3460;
    min-height: 44px;
    max-height: 44px;
}

/* ── buttons ── */
QPushButton {
    background-color: #0f3460;
    border: 1px solid #1a5276;
    border-radius: 5px;
    padding: 6px 14px;
    color: #e0e0e0;
    min-width: 80px;
}
QPushButton:hover  { background-color: #1a5276; border-color: #2980b9; }
QPushButton:pressed { background-color: #0d2137; }
QPushButton:disabled { color: #555; border-color: #333; background-color: #141414; }

QPushButton#btn_start  { background-color: #1e5c2e; border-color: #27ae60; }
QPushButton#btn_start:hover  { background-color: #27ae60; }
QPushButton#btn_stop   { background-color: #7b241c; border-color: #e74c3c; }
QPushButton#btn_stop:hover   { background-color: #e74c3c; }
QPushButton#btn_refresh { background-color: #154360; border-color: #2980b9; }
QPushButton#btn_refresh:hover { background-color: #2980b9; }
QPushButton#primary_btn { background-color: #1e5c2e; border-color: #27ae60; }
QPushButton#primary_btn:hover { background-color: #27ae60; }

/* ── table ── */
QTableWidget {
    background-color: #16213e;
    gridline-color: #0d2240;
    border: none;
    selection-background-color: #0f3460;
    alternate-background-color: #192542;
}
QTableWidget::item { padding: 3px 6px; }
QHeaderView::section {
    background-color: #0f3460;
    color: #a0c4e8;
    padding: 5px 6px;
    border: none;
    border-right: 1px solid #1a1a2e;
    font-weight: bold;
    font-size: 9pt;
}
QHeaderView { background-color: #0f3460; }

/* ── group box ── */
QGroupBox {
    border: 1px solid #0f3460;
    border-radius: 6px;
    margin-top: 10px;
    padding: 8px 10px;
    font-weight: bold;
    color: #a0c4e8;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}

/* ── progress bar ── */
QProgressBar {
    background-color: #0d2240;
    border: 1px solid #0f3460;
    border-radius: 4px;
    text-align: center;
    color: #e0e0e0;
    font-size: 8pt;
    height: 16px;
}
QProgressBar::chunk {
    background-color: #53fc18;
    border-radius: 3px;
}

/* ── log ── */
QTextEdit {
    background-color: #0d0d1a;
    border: none;
    border-top: 1px solid #0f3460;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 9pt;
    color: #c0c0c0;
}

/* ── splitter handle ── */
QSplitter::handle { background-color: #0f3460; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }

/* ── status label colors ── */
QLabel#lbl_status_dot { font-size: 11pt; }

/* ── input fields (login dialog) ── */
QLineEdit {
    background-color: #16213e;
    border: 1px solid #0f3460;
    border-radius: 4px;
    padding: 5px 8px;
    color: #e0e0e0;
}
QLineEdit:focus { border-color: #53fc18; }

/* ── scrollbars ── */
QScrollBar:vertical {
    background: #16213e; width: 8px; margin: 0;
}
QScrollBar::handle:vertical {
    background: #0f3460; border-radius: 4px; min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: #16213e; height: 8px; margin: 0;
}
QScrollBar::handle:horizontal {
    background: #0f3460; border-radius: 4px; min-width: 20px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""

# ── column indices ───────────────────────────────────────────────────────────
COL_PRI      = 0
COL_CAMPAIGN = 1
COL_REWARD   = 2
COL_TYPE     = 3
COL_REQUIRED = 4
COL_WATCHED  = 5
COL_PROGRESS = 6
COL_STATUS   = 7
COL_ENABLED  = 8
COLUMNS = ['#', 'Campaign', 'Reward', 'Type', 'Required', 'Watched', 'Progress', 'Status', 'On']


# ============================================================================
# Background threads
# ============================================================================

class MinerThread(QThread):
    """Runs the async Miner in a dedicated event loop."""
    log_sig    = pyqtSignal(str)
    status_sig = pyqtSignal(str)

    def __init__(self, dm: DropManager, parent=None):
        super().__init__(parent)
        self.dm = dm
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._miner: Miner | None = None

    # --- public -----------------------------------------------------------------

    @property
    def current_drop(self) -> Drop | None:
        return self._miner.current_drop if self._miner else None

    @property
    def current_username(self) -> str | None:
        return self._miner.current_username if self._miner else None

    def request_stop(self):
        if self._loop and self._stop_event and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop_event.set)

    # --- QThread ----------------------------------------------------------------

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()
        self._miner = Miner(
            self.dm,
            self._stop_event,
            log_cb=self.log_sig.emit,
            status_cb=self.status_sig.emit,
        )
        try:
            self._loop.run_until_complete(self._miner.run())
        except Exception as exc:
            self.log_sig.emit(f'[!] Miner crashed: {exc}')
        finally:
            self._loop.close()


class RefreshThread(QThread):
    """Fetches drops + progress from the Kick API (sync, blocking)."""
    done = pyqtSignal(int, str)   # drop_count, error_msg

    def __init__(self, dm: DropManager, parent=None):
        super().__init__(parent)
        self.dm = dm

    def run(self):
        try:
            data = kick_api.get_all_campaigns()
            self.dm.load_from_api(data)

            session_token = auth_manager.get_session_token()
            if session_token:
                progress = kick_api.get_drops_progress(session_token)
                if progress:
                    self.dm.sync_progress(progress)

            self.done.emit(len(self.dm.drops), '')
        except Exception as exc:
            self.done.emit(0, str(exc))


# ============================================================================
# Main window
# ============================================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Kick Drop Miner')
        self.resize(1100, 700)
        self.setMinimumSize(800, 500)

        self.dm = DropManager()
        self._miner_thread: MinerThread | None = None
        self._refresh_thread: RefreshThread | None = None

        self._build_menu()
        self._build_ui()
        self.setStyleSheet(DARK_STYLE)

        # Periodic UI refresh (status panel + table highlighting)
        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._refresh_status_panel)
        self._ui_timer.start(2000)

        # Load saved state
        if self.dm.load():
            self._rebuild_table()
            self._log('[*] Loaded saved state — press Refresh to sync with Kick API')
        else:
            self._log('[*] No saved state — press Refresh to load drops')

    # ── menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self):
        mb = self.menuBar()

        file_menu = mb.addMenu('File')
        act_login  = QAction('Login…', self)
        act_logout = QAction('Logout', self)
        act_quit   = QAction('Quit', self)
        act_login.triggered.connect(self._show_login)
        act_logout.triggered.connect(self._logout)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_login)
        file_menu.addAction(act_logout)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        miner_menu = mb.addMenu('Miner')
        act_start   = QAction('Start', self)
        act_stop    = QAction('Stop', self)
        act_refresh = QAction('Refresh drops', self)
        act_start.triggered.connect(self._start_miner)
        act_stop.triggered.connect(self._stop_miner)
        act_refresh.triggered.connect(self._refresh_drops)
        miner_menu.addAction(act_start)
        miner_menu.addAction(act_stop)
        miner_menu.addSeparator()
        miner_menu.addAction(act_refresh)

    # ── UI layout ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_toolbar())

        # Main splitter: drops table | status panel
        h_split = QSplitter(Qt.Orientation.Horizontal)
        h_split.addWidget(self._build_drops_panel())
        h_split.addWidget(self._build_status_panel())
        h_split.setSizes([680, 380])
        h_split.setHandleWidth(3)

        # Vertical splitter: top content | log
        v_split = QSplitter(Qt.Orientation.Vertical)
        v_split.addWidget(h_split)
        v_split.addWidget(self._build_log_panel())
        v_split.setSizes([480, 180])
        v_split.setHandleWidth(3)

        layout.addWidget(v_split)

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName('toolbar')
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 4, 10, 4)
        row.setSpacing(6)

        self.btn_start   = QPushButton('▶  Start')
        self.btn_stop    = QPushButton('■  Stop')
        self.btn_refresh = QPushButton('↺  Refresh')
        self.btn_start.setObjectName('btn_start')
        self.btn_stop.setObjectName('btn_stop')
        self.btn_refresh.setObjectName('btn_refresh')

        self.btn_start.clicked.connect(self._start_miner)
        self.btn_stop.clicked.connect(self._stop_miner)
        self.btn_refresh.clicked.connect(self._refresh_drops)

        self.lbl_dot = QLabel('●')
        self.lbl_dot.setObjectName('lbl_status_dot')
        self.lbl_dot.setStyleSheet('color: #555;')
        self.lbl_status = QLabel('Idle')
        self.lbl_status.setStyleSheet('color: #888;')

        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_refresh)
        row.addSpacing(12)
        row.addWidget(self.lbl_dot)
        row.addWidget(self.lbl_status)
        row.addStretch()

        # Auth indicator
        self.lbl_auth = QLabel()
        self._update_auth_label()
        row.addWidget(self.lbl_auth)

        return bar

    def _build_drops_panel(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(6, 6, 2, 0)
        vbox.setSpacing(4)

        # Mini toolbar above table
        mini = QHBoxLayout()
        mini.setSpacing(4)
        lbl = QLabel('Available Drops')
        lbl.setStyleSheet('color: #a0c4e8; font-weight: bold;')
        self.btn_up     = QPushButton('↑')
        self.btn_down   = QPushButton('↓')
        self.btn_toggle = QPushButton('Enable / Disable')
        self.btn_up.setFixedWidth(36)
        self.btn_down.setFixedWidth(36)
        self.btn_up.setToolTip('Move selected drop up in priority')
        self.btn_down.setToolTip('Move selected drop down in priority')
        self.btn_toggle.setToolTip('Toggle whether this drop is mined')
        self.btn_up.clicked.connect(self._move_up)
        self.btn_down.clicked.connect(self._move_down)
        self.btn_toggle.clicked.connect(self._toggle_drop)

        mini.addWidget(lbl)
        mini.addStretch()
        mini.addWidget(self.btn_up)
        mini.addWidget(self.btn_down)
        mini.addWidget(self.btn_toggle)
        vbox.addLayout(mini)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setShowGrid(True)
        self.table.setSortingEnabled(False)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(COL_PRI,      QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_CAMPAIGN, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(COL_REWARD,   QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(COL_TYPE,     QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_REQUIRED, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_WATCHED,  QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_PROGRESS, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_STATUS,   QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_ENABLED,  QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_PRI,      34)
        self.table.setColumnWidth(COL_TYPE,     72)
        self.table.setColumnWidth(COL_REQUIRED, 72)
        self.table.setColumnWidth(COL_WATCHED,  64)
        self.table.setColumnWidth(COL_PROGRESS, 120)
        self.table.setColumnWidth(COL_STATUS,   72)
        self.table.setColumnWidth(COL_ENABLED,  30)
        self.table.setRowHeight(0, 26)

        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_context_menu)

        vbox.addWidget(self.table)
        return w

    def _build_status_panel(self) -> QGroupBox:
        box = QGroupBox('Mining Status')
        vbox = QVBoxLayout(box)
        vbox.setSpacing(8)

        self.lbl_watching = QLabel('Not watching')
        self.lbl_watching.setWordWrap(True)
        self.lbl_watching.setStyleSheet('color: #e0e0e0;')

        self.lbl_drop = QLabel('')
        self.lbl_drop.setWordWrap(True)
        self.lbl_drop.setStyleSheet('color: #a0c4e8;')

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat('%p%')
        self.progress_bar.setFixedHeight(18)

        self.lbl_remaining = QLabel('')
        self.lbl_remaining.setStyleSheet('color: #888; font-size: 9pt;')

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet('color: #0f3460;')

        self.lbl_queue_hdr = QLabel('Queue')
        self.lbl_queue_hdr.setStyleSheet('color: #a0c4e8; font-weight: bold;')
        self.lbl_queue = QLabel('–')
        self.lbl_queue.setWordWrap(True)
        self.lbl_queue.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.lbl_queue.setStyleSheet('color: #ccc; font-size: 9pt;')
        self.lbl_queue.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        vbox.addWidget(self.lbl_watching)
        vbox.addWidget(self.lbl_drop)
        vbox.addWidget(self.progress_bar)
        vbox.addWidget(self.lbl_remaining)
        vbox.addWidget(sep)
        vbox.addWidget(self.lbl_queue_hdr)
        vbox.addWidget(self.lbl_queue)
        vbox.addStretch()

        return box

    def _build_log_panel(self) -> QTextEdit:
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setPlaceholderText('Activity log will appear here…')
        return self.log_edit

    # ── auth ──────────────────────────────────────────────────────────────────

    def _update_auth_label(self):
        if auth_manager.is_logged_in():
            self.lbl_auth.setText('🟢 Logged in')
            self.lbl_auth.setStyleSheet('color: #53fc18; font-size: 9pt;')
        else:
            self.lbl_auth.setText('🔴 Not logged in')
            self.lbl_auth.setStyleSheet('color: #e74c3c; font-size: 9pt;')

    def _show_login(self):
        dlg = BrowserLoginDialog(self)
        dlg.setStyleSheet(DARK_STYLE)
        dlg.login_success.connect(self._on_login_success)
        dlg.exec()

    def _on_login_success(self, _cookies: dict):
        self._log('[*] Login successful — session saved')
        self._update_auth_label()

    def _logout(self):
        auth_manager.clear_session()
        self._update_auth_label()
        self._log('[*] Logged out')

    # ── miner controls ────────────────────────────────────────────────────────

    def _start_miner(self):
        if self._miner_thread and self._miner_thread.isRunning():
            return
        if not auth_manager.is_logged_in():
            QMessageBox.warning(
                self, 'Not logged in',
                'Please sign in first via File → Login.'
            )
            return

        self._miner_thread = MinerThread(self.dm, self)
        self._miner_thread.log_sig.connect(self._log)
        self._miner_thread.status_sig.connect(self._on_miner_status)
        self._miner_thread.finished.connect(self._on_miner_finished)
        self._miner_thread.start()

        self._set_running(True)
        self._log('[*] Mining started')

    def _stop_miner(self):
        if self._miner_thread and self._miner_thread.isRunning():
            self._miner_thread.request_stop()
            self._log('[*] Stop requested…')

    def _on_miner_status(self, msg: str):
        self.lbl_status.setText(msg)
        self._refresh_status_panel()

    def _on_miner_finished(self):
        self._set_running(False)
        self._log('[*] Miner thread finished')

    def _set_running(self, running: bool):
        color = '#53fc18' if running else '#555'
        label = 'Running' if running else 'Idle'
        self.lbl_dot.setStyleSheet(f'color: {color};')
        self.lbl_status.setText(label)
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    # ── drops refresh ─────────────────────────────────────────────────────────

    def _refresh_drops(self):
        if self._refresh_thread and self._refresh_thread.isRunning():
            return
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText('↺  Loading…')
        self._log('[*] Fetching drops from Kick API…')

        self._refresh_thread = RefreshThread(self.dm, self)
        self._refresh_thread.done.connect(self._on_refresh_done)
        self._refresh_thread.start()

    def _on_refresh_done(self, count: int, error: str):
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText('↺  Refresh')
        if error:
            self._log(f'[!] Refresh failed: {error}')
        else:
            self._log(f'[+] {count} drop(s) loaded')
            self._rebuild_table()

    # ── table management ──────────────────────────────────────────────────────

    def _rebuild_table(self):
        self.table.setRowCount(0)
        for i, drop in enumerate(self.dm.drops):
            self.table.insertRow(i)
            self.table.setRowHeight(i, 26)

            active = (
                self._miner_thread is not None
                and self._miner_thread.current_drop is not None
                and self._miner_thread.current_drop.id == drop.id
            )

            # # (priority)
            pri_item = QTableWidgetItem(str(i + 1))
            pri_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if active:
                pri_item.setForeground(QColor('#53fc18'))
                pri_item.setText(f'► {i+1}')
            self.table.setItem(i, COL_PRI, pri_item)

            # Campaign
            self.table.setItem(i, COL_CAMPAIGN, QTableWidgetItem(drop.campaign_name))

            # Reward
            self.table.setItem(i, COL_REWARD, QTableWidgetItem(drop.reward_name))

            # Type
            type_item = QTableWidgetItem('Specific' if drop.drop_type == 1 else 'General')
            type_item.setForeground(
                QColor('#a0c4e8') if drop.drop_type == 1 else QColor('#888')
            )
            self.table.setItem(i, COL_TYPE, type_item)

            # Required
            req_item = QTableWidgetItem(f'{drop.required_minutes:.0f} m')
            req_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(i, COL_REQUIRED, req_item)

            # Watched
            w_item = QTableWidgetItem(f'{drop.watched_minutes:.1f} m')
            w_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(i, COL_WATCHED, w_item)

            # Progress bar
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(drop.progress_pct))
            bar.setTextVisible(True)
            bar.setFormat(f'{drop.progress_pct:.0f}%')
            bar.setFixedHeight(16)
            if drop.claimed:
                bar.setStyleSheet('QProgressBar::chunk { background-color: #27ae60; }')
            elif active:
                bar.setStyleSheet('QProgressBar::chunk { background-color: #53fc18; }')
            self.table.setCellWidget(i, COL_PROGRESS, bar)

            # Status
            status_colors = {'Claimed': '#27ae60', 'Ready': '#f39c12', 'Pending': '#888'}
            s_item = QTableWidgetItem(drop.status)
            s_item.setForeground(QColor(status_colors.get(drop.status, '#888')))
            s_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, COL_STATUS, s_item)

            # Enabled
            en_item = QTableWidgetItem('✓' if drop.enabled else '✗')
            en_item.setForeground(QColor('#27ae60') if drop.enabled else QColor('#555'))
            en_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, COL_ENABLED, en_item)

            # Dim entire row if disabled or claimed
            if not drop.enabled or drop.claimed:
                for col in range(len(COLUMNS)):
                    item = self.table.item(i, col)
                    if item:
                        item.setForeground(QColor('#444' if not drop.enabled else '#2d6a4f'))

    def _selected_drop(self) -> Drop | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.dm.drops):
            return self.dm.drops[row]
        return None

    def _restore_cursor(self, drop_id: int):
        idx = self.dm._index_of(drop_id)
        if idx >= 0:
            self.table.selectRow(idx)

    def _move_up(self):
        drop = self._selected_drop()
        if drop:
            self.dm.move_up(drop.id)
            self._rebuild_table()
            self._restore_cursor(drop.id)

    def _move_down(self):
        drop = self._selected_drop()
        if drop:
            self.dm.move_down(drop.id)
            self._rebuild_table()
            self._restore_cursor(drop.id)

    def _toggle_drop(self):
        drop = self._selected_drop()
        if drop:
            self.dm.toggle_enabled(drop.id)
            self._rebuild_table()
            self._restore_cursor(drop.id)

    def _set_priority_dialog(self):
        drop = self._selected_drop()
        if not drop:
            return
        current = self.dm._index_of(drop.id) + 1
        total = len(self.dm.drops)
        val, ok = QInputDialog.getInt(
            self, 'Set Priority',
            f'New position (1–{total}):',
            value=current, min=1, max=total,
        )
        if ok:
            self.dm.set_priority(drop.id, val)
            self._rebuild_table()
            self._restore_cursor(drop.id)

    def _table_context_menu(self, pos):
        drop = self._selected_drop()
        if not drop:
            return
        menu = QMenu(self)
        menu.setStyleSheet(DARK_STYLE)
        act_up     = menu.addAction('↑  Move Up')
        act_down   = menu.addAction('↓  Move Down')
        act_pri    = menu.addAction('…  Set Priority')
        menu.addSeparator()
        lbl = 'Disable' if drop.enabled else 'Enable'
        act_toggle = menu.addAction(f'   {lbl}')
        menu.addSeparator()
        act_info   = menu.addAction('ℹ  Show Streamers')

        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == act_up:
            self._move_up()
        elif chosen == act_down:
            self._move_down()
        elif chosen == act_pri:
            self._set_priority_dialog()
        elif chosen == act_toggle:
            self._toggle_drop()
        elif chosen == act_info:
            if drop.drop_type == 1 and drop.usernames:
                QMessageBox.information(
                    self, 'Streamers',
                    'Streamers for this drop:\n\n' + '\n'.join(drop.usernames),
                )
            else:
                QMessageBox.information(self, 'Streamers', 'General drop — any streamer in the category.')

    # ── status panel ──────────────────────────────────────────────────────────

    def _refresh_status_panel(self):
        thread = self._miner_thread
        running = thread is not None and thread.isRunning()

        if running and thread.current_drop:
            drop = thread.current_drop
            username = thread.current_username or 'searching…'
            self.lbl_watching.setText(f'Watching: {username}')
            self.lbl_drop.setText(f'{drop.campaign_name}\n{drop.reward_name}')
            self.progress_bar.setValue(int(drop.progress_pct))
            self.progress_bar.setFormat(f'{drop.progress_pct:.0f}%')
            self.lbl_remaining.setText(
                f'{drop.watched_minutes:.1f} / {drop.required_minutes:.0f} min  '
                f'({drop.remaining_minutes:.0f} min left)'
            )
        elif running:
            self.lbl_watching.setText('Searching for a stream…')
            self.lbl_drop.setText('')
            self.lbl_remaining.setText('')
        else:
            self.lbl_watching.setText('Not watching')
            self.lbl_drop.setText('')
            self.progress_bar.setValue(0)
            self.lbl_remaining.setText('')

        # Queue
        pending = [
            d for d in self.dm.drops
            if d.enabled and not d.claimed and d.remaining_minutes > 0
        ]
        if pending:
            lines = '\n'.join(
                f'{i+1}. {d.reward_name}  ({d.remaining_minutes:.0f} min)'
                for i, d in enumerate(pending[:8])
            )
            extra = f'\n…and {len(pending)-8} more' if len(pending) > 8 else ''
            self.lbl_queue.setText(lines + extra)
        else:
            self.lbl_queue.setText('–  no pending drops')

        # Refresh progress bars in table without rebuilding everything
        for row, drop in enumerate(self.dm.drops):
            bar: QProgressBar = self.table.cellWidget(row, COL_PROGRESS)
            if bar:
                bar.setValue(int(drop.progress_pct))
                bar.setFormat(f'{drop.progress_pct:.0f}%')
            w_item = self.table.item(row, COL_WATCHED)
            if w_item:
                w_item.setText(f'{drop.watched_minutes:.1f} m')

    # ── logging ───────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_edit.append(msg)
        sb = self.log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._stop_miner()
        if self._miner_thread:
            self._miner_thread.wait(3000)
        event.accept()
