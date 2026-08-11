"""日志面板 — 查看 + 过滤 + 导出（v3.0）"""
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QComboBox, QFileDialog,
)
from PySide6.QtCore import Qt

from src.ui.base_panel import BasePanel
from src.ui.common import mb_info


class LogPanel(BasePanel):
    """日志查看面板"""

    def __init__(self, event_bus, logger, config_manager=None):
        self._config_manager = config_manager
        self._all_lines: list[str] = []
        super().__init__(event_bus, logger)

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
        export_btn = QPushButton("导出")
        export_btn.clicked.connect(self._export_logs)
        tb.addWidget(export_btn)
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
        line = event.data.get("text", "")
        self._all_lines.append(line)
        if self._filter.currentText() == "ALL" or f"[{self._filter.currentText()}]" in line:
            self._text.append(line)
            self._text.moveCursor(self._text.textCursor().End)

    def _apply_filter(self):
        level = self._filter.currentText()
        self._text.clear()
        for line in self._all_lines[-500:]:
            if level == "ALL" or f"[{level}]" in line:
                self._text.append(line)

    def _export_logs(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "", "Text (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(self._all_lines))
        mb_info(self, "导出完成", f"已导出到 {path}")
