"""日志面板 — 查看 + 过滤（v3.2，导出已移至「📤 导出」页）"""
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QComboBox,
)
from PySide6.QtCore import Qt, Signal

from src.ui.base_panel import BasePanel


class LogPanel(BasePanel):
    """日志查看面板"""

    # ★ 跨线程桥接：日志由 Logger 后台线程发布，通过信号切回主线程更新 UI
    log_received = Signal(str)

    def __init__(self, event_bus, logger, config_manager=None):
        self._config_manager = config_manager
        self._all_lines: list[str] = []
        super().__init__(event_bus, logger)
        self.log_received.connect(self._do_append_log)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        tb = QHBoxLayout()
        tb.addWidget(QLabel("级别:"))
        self._filter = QComboBox()
        self._filter.addItems(["ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self._filter.currentTextChanged.connect(self._apply_filter)
        tb.addWidget(self._filter)
        tb.addStretch()
        # ★ v3.2: 导出功能已统合到「📤 导出」导航页
        layout.addLayout(tb)

        # Log display
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setStyleSheet("background: #1e1e1e; color: #d4d4d4; font-family: monospace; font-size: 12px;")
        layout.addWidget(self._text, 1)

    def _subscribe_events(self):
        self._event_bus.subscribe("log:new", self._on_new_log)

    def on_show(self):
        self._load_history()

    def _load_history(self):
        """加载最近的日志文件（★ v3修复: Logger 按日期分目录，需递归收集）"""
        log_dir = self._logger.log_dir if self._logger else "workspace/logs"
        self._all_lines = []
        try:
            # Logger 写入格式: <log_dir>/<日期>/<模块>.log → 递归收集
            files = []
            for root, _dirs, fnames in os.walk(log_dir):
                for fn in fnames:
                    if fn.endswith(".log"):
                        files.append(os.path.join(root, fn))
            files.sort(reverse=True)
            for fp in files[:5]:
                with open(fp, encoding="utf-8") as f:
                    for line in f:
                        self._all_lines.append(line.strip())
        except Exception:
            pass
        self._apply_filter()

    def _on_new_log(self, event):
        # ★ 仅转发信号（Logger 线程），UI 操作在 _do_append_log（主线程）
        line = event.data.get("text", "")
        if line:
            self.log_received.emit(line)

    def _do_append_log(self, line: str):
        self._all_lines.append(line)
        if len(self._all_lines) > 5000:
            self._all_lines = self._all_lines[-5000:]
        if self._filter.currentText() == "ALL" or f"[{self._filter.currentText()}]" in line:
            self._text.append(line)
            self._text.moveCursor(self._text.textCursor().End)

    def _apply_filter(self):
        level = self._filter.currentText()
        self._text.clear()
        for line in self._all_lines[-500:]:
            if level == "ALL" or f"[{level}]" in line:
                self._text.append(line)

    # ★ v3.2: 日志导出功能已统合到「📤 导出」导航页
