"""伏笔面板 — CRUD + 隐藏/显示 + 序号列（v3.0）"""
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QTextEdit, QPushButton, QCheckBox, QLabel, QMenu, QHeaderView,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction

from src.ui.base_panel import BasePanel
from src.ui.common import mb_error, mb_ask, dialog_toplevel


class ForeshadowPanel(BasePanel):
    """伏笔管理面板 — 条目式 CRUD"""

    def __init__(self, event_bus, logger, project_service):
        self._project_service = project_service
        self._show_hidden = True
        super().__init__(event_bus, logger)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        tb = QHBoxLayout()
        tb.addWidget(QLabel("🔮 伏笔管理"))
        tb.addStretch()
        chk = QCheckBox("显示已隐藏")
        chk.setChecked(True)
        chk.toggled.connect(self._on_toggle_hidden)
        tb.addWidget(chk)
        layout.addLayout(tb)

        # Tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["#", "伏笔内容", "状态"])
        self._tree.setColumnWidth(0, 50)
        self._tree.setColumnWidth(1, 500)
        self._tree.setColumnWidth(2, 80)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.itemDoubleClicked.connect(self._on_edit)
        layout.addWidget(self._tree)

        # Input area
        input_area = QHBoxLayout()
        self._input = QTextEdit()
        self._input.setMaximumHeight(50)
        self._input.setPlaceholderText("输入新伏笔内容...")
        input_area.addWidget(self._input)
        add_btn = QPushButton("+ 添加")
        add_btn.clicked.connect(self._add_foreshadow)
        input_area.addWidget(add_btn)
        layout.addLayout(input_area)

    def _subscribe_events(self):
        self._event_bus.subscribe("foreshadow:created", lambda e: self._refresh_list())
        self._event_bus.subscribe("foreshadow:updated", lambda e: self._refresh_list())
        self._event_bus.subscribe("foreshadow:deleted", lambda e: self._refresh_list())
        self._event_bus.subscribe("foreshadow:toggled", lambda e: self._refresh_list())

    def on_show(self):
        self._refresh_list()

    def _on_toggle_hidden(self, checked):
        self._show_hidden = checked
        self._refresh_list()

    def _refresh_list(self):
        self._tree.clear()
        try:
            fs = self._project_service.foreshadow_service
            items = fs.list_foreshadows(include_hidden=self._show_hidden)
            for idx, f in enumerate(items, 1):
                status = "👁 隐藏" if f.hidden else "◎"
                sw = QTreeWidgetItem([str(idx), f.content, status])
                sw.setData(0, Qt.ItemDataRole.UserRole, f.foreshadow_id)
                if f.hidden:
                    sw.setForeground(1, Qt.GlobalColor.gray)
                self._tree.addTopLevelItem(sw)
        except Exception:
            pass

    def _get_selected_id(self):
        item = self._tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _add_foreshadow(self):
        text = self._input.toPlainText().strip()
        if not text:
            return
        try:
            self._project_service.foreshadow_service.add_foreshadow(text)
            self._input.clear()
            self._refresh_list()
        except ValueError as e:
            mb_error(self, "错误", str(e))

    def _on_edit(self, item):
        fid = item.data(0, Qt.ItemDataRole.UserRole)
        f = self._project_service.foreshadow_service.get_foreshadow(fid)
        if not f:
            return
        dlg = dialog_toplevel(self, "编辑伏笔", 500, 350)
        lay = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setPlainText(f.content)
        lay.addWidget(text)
        btn = QHBoxLayout()
        save = QPushButton("保存")
        cancel = QPushButton("取消")

        def do_save():
            try:
                self._project_service.foreshadow_service.update_foreshadow(fid, text.toPlainText().strip())
                dlg.accept()
                self._refresh_list()
            except ValueError as e:
                mb_error(self, "错误", str(e))

        save.clicked.connect(do_save)
        cancel.clicked.connect(dlg.reject)
        btn.addWidget(save)
        btn.addWidget(cancel)
        lay.addLayout(btn)
        dlg.exec()

    def _on_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if not item:
            return
        fid = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("编辑内容", lambda: self._on_edit(item))
        menu.addAction("隐藏/显示", lambda: self._do_toggle(fid))
        menu.addSeparator()
        menu.addAction("删除", lambda: self._do_delete(fid))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _do_toggle(self, fid):
        f = self._project_service.foreshadow_service.get_foreshadow(fid)
        if f:
            self._project_service.foreshadow_service.update_foreshadow(fid, f.content, hidden=not f.hidden)
            self._refresh_list()

    def _do_delete(self, fid):
        if mb_ask(self, "确认", "确定要删除这条伏笔吗？"):
            self._project_service.foreshadow_service.delete_foreshadow(fid)
            self._refresh_list()
