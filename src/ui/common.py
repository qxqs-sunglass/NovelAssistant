"""公共 UI 组件（v3.0 PySide6）

包含：
  - 消息框包装（mb_info / mb_warn / mb_error / mb_ask）
  - 统一模态对话框（dialog_toplevel）
  - 深度思考流式弹窗（ReasoningWindow）
  - 右键详情弹窗（DetailPopup）
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QMessageBox, QWidget,
)
from PySide6.QtCore import Qt


def mb_info(parent: QWidget, title: str, message: str):
    """信息对话框 — 绑定父窗口"""
    QMessageBox.information(parent, title, message)


def mb_warn(parent: QWidget, title: str, message: str):
    """警告对话框"""
    QMessageBox.warning(parent, title, message)


def mb_error(parent: QWidget, title: str, message: str):
    """错误对话框"""
    QMessageBox.critical(parent, title, message)


def mb_ask(parent: QWidget, title: str, message: str) -> bool:
    """确认对话框 — 返回 True/False"""
    return QMessageBox.question(
        parent, title, message,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    ) == QMessageBox.StandardButton.Yes


def dialog_toplevel(
    parent: QWidget, title: str, width: int = 520, height: int = 400,
) -> QDialog:
    """统一模态对话框 — 居中于父窗口"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(width, height)
    dlg.setMinimumSize(360, 240)
    dlg.setModal(True)
    # Center on parent
    if parent:
        parent_geo = parent.geometry()
        x = parent_geo.x() + (parent_geo.width() - width) // 2
        y = parent_geo.y() + (parent_geo.height() - height) // 3
        dlg.move(max(x, 0), max(y, 0))
    return dlg


class ReasoningWindow:
    """深度思考弹窗 — 流式显示 AI reasoning_content

    v3.0 PySide6 版本：QDialog + QTextEdit + 非模态（用户可滚动主窗口）
    """

    def __init__(self, parent: QWidget, title: str = "🧠 深度思考"):
        self._dlg = QDialog(parent)
        self._dlg.setWindowTitle(title)
        self._dlg.resize(560, 420)
        self._dlg.setMinimumSize(360, 240)
        self._dlg.setWindowFlags(
            self._dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        layout = QVBoxLayout(self._dlg)

        # Header
        self._header = QLabel("🧠 深度思考进行中...")
        self._header.setStyleSheet(
            "background:#fff7e6; color:#b8860b; padding:4px 8px; font-weight:bold;"
        )
        layout.addWidget(self._header)

        # Close button
        close_btn = QPushButton("✕ 关闭")
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet("padding:2px 8px;")
        close_btn.setMaximumWidth(80)
        hdr_layout = QHBoxLayout()
        hdr_layout.addWidget(self._header)
        hdr_layout.addStretch()
        hdr_layout.addWidget(close_btn)
        # ★ v3修复: header 已被 hdr_layout 接管（addWidget 自动移出原布局），
        # 原代码 layout.itemAt(0) 此时为 None 导致崩溃，已移除
        hdr_widget = QWidget()
        hdr_widget.setLayout(hdr_layout)
        hdr_widget.setStyleSheet("background:#fff7e6;")
        layout.insertWidget(0, hdr_widget)

        # Text area
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setStyleSheet(
            "background:#f8f9fa; color:#555; border:none; padding:8px;"
        )
        layout.addWidget(self._text)

        self._dlg.show()  # Non-modal so user can scroll main window
        self._finished = False

    def append(self, text: str):
        """追加一段思考内容（流式调用）"""
        if self._finished:
            return
        cursor = self._text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._text.setTextCursor(cursor)
        self._text.insertPlainText(text)

    def finish(self):
        """思考结束标记"""
        if self._finished:
            return
        self._finished = True
        self._header.setText("🧠 深度思考完成")
        self._header.setStyleSheet(
            "background:#fff7e6; color:#2e8b57; padding:4px 8px; font-weight:bold;"
        )

    def is_alive(self) -> bool:
        try:
            return self._dlg.isVisible()
        except Exception:
            return False

    def close(self):
        try:
            self._dlg.close()
        except Exception:
            pass


class DetailPopup:
    """右键详情弹窗"""

    @staticmethod
    def show(parent: QWidget, title: str, detail: str):
        dlg = dialog_toplevel(parent, title, 520, 400)
        layout = QVBoxLayout(dlg)
        label = QLabel(title)
        label.setStyleSheet(
            "background:#f0f4f8; padding:6px 8px; font-weight:bold;"
        )
        layout.addWidget(label)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(detail)
        layout.addWidget(text)
        dlg.exec()
