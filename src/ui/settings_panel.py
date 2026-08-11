"""设定面板 — 分类 + 文档管理 + Markdown 编辑（v3.0）"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLineEdit, QLabel,
    QTextEdit, QPushButton, QFileDialog,
)
from PySide6.QtCore import Qt

from src.ui.base_panel import BasePanel
from src.ui.common import mb_info, mb_ask, dialog_toplevel


class SettingsPanel(BasePanel):
    """设定管理面板"""

    def __init__(self, event_bus, logger, project_service):
        self._project_service = project_service
        self._current_cat: str | None = None
        self._current_doc: str | None = None
        self._content_modified = False
        super().__init__(event_bus, logger)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: categories
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("分类"))
        self._cat_list = QListWidget()
        self._cat_list.currentItemChanged.connect(self._on_cat_selected)
        ll.addWidget(self._cat_list)
        b1 = QHBoxLayout()
        new_cat = QPushButton("+ 新建")
        new_cat.clicked.connect(self._create_category)

        def rename_cat():
            if self._current_cat:
                dlg = dialog_toplevel(self, "重命名分类", 300, 120)
                l = QVBoxLayout(dlg)
                l.addWidget(QLabel("新名称:"))
                e = QLineEdit(self._current_cat)
                l.addWidget(e)
                btns = QHBoxLayout()
                ok = QPushButton("确定")
                cancel = QPushButton("取消")
                ok.clicked.connect(lambda: self._do_rename_cat(e.text().strip(), dlg))
                cancel.clicked.connect(dlg.reject)
                btns.addWidget(ok)
                btns.addWidget(cancel)
                l.addLayout(btns)
                dlg.exec()

        del_cat = QPushButton("🗑 删除")
        del_cat.clicked.connect(self._delete_category)
        ren_cat = QPushButton("重命名")
        ren_cat.clicked.connect(rename_cat)
        b1.addWidget(new_cat)
        b1.addWidget(ren_cat)
        b1.addWidget(del_cat)
        ll.addLayout(b1)
        splitter.addWidget(left)

        # Middle: docs
        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(QLabel("文档"))
        self._doc_list = QListWidget()
        self._doc_list.currentItemChanged.connect(self._on_doc_selected)
        ml.addWidget(self._doc_list)
        b2 = QHBoxLayout()
        new_doc = QPushButton("+ 新建")
        new_doc.clicked.connect(self._create_doc)
        del_doc = QPushButton("🗑 删除")
        del_doc.clicked.connect(self._delete_doc)
        b2.addWidget(new_doc)
        b2.addWidget(del_doc)
        ml.addLayout(b2)
        splitter.addWidget(mid)

        # Right: editor
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        rl.addWidget(QLabel("内容:"))
        self._editor = QTextEdit()
        self._editor.textChanged.connect(lambda: setattr(self, '_content_modified', True))
        rl.addWidget(self._editor, 1)
        save_btn = QPushButton("💾 保存")
        save_btn.clicked.connect(self._save_doc)
        export_btn = QPushButton("导出")
        export_btn.clicked.connect(self._export_all)
        btns = QHBoxLayout()
        btns.addWidget(save_btn)
        btns.addWidget(export_btn)
        rl.addLayout(btns)
        splitter.addWidget(right)

        splitter.setSizes([180, 180, 400])
        layout.addWidget(splitter, 1)

    def on_show(self):
        self._refresh_cats()

    def on_close(self):
        self._save_doc()

    def _refresh_cats(self):
        self._cat_list.clear()
        for c in self._project_service.list_categories():
            item = QListWidgetItem(c.name)
            item.setData(Qt.ItemDataRole.UserRole, c.name)
            self._cat_list.addItem(item)

    def _refresh_docs(self):
        self._doc_list.clear()
        if not self._current_cat:
            return
        for d in self._project_service.list_settings(self._current_cat):
            item = QListWidgetItem(d.name)
            item.setData(Qt.ItemDataRole.UserRole, d.name)
            self._doc_list.addItem(item)

    def _on_cat_selected(self, item):
        self._save_doc()
        self._current_cat = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._current_doc = None
        self._editor.clear()
        self._refresh_docs()

    def _on_doc_selected(self, item):
        self._save_doc()
        self._current_doc = item.data(Qt.ItemDataRole.UserRole) if item else None
        if self._current_doc and self._current_cat:
            doc = self._project_service.get_setting(self._current_cat, self._current_doc)
            self._editor.setPlainText(doc.content if doc else "")
            self._content_modified = False
        else:
            self._editor.clear()

    def _create_category(self):
        dlg = dialog_toplevel(self, "新建分类", 300, 120)
        l = QVBoxLayout(dlg)
        l.addWidget(QLabel("分类名称:"))
        e = QLineEdit()
        l.addWidget(e)
        btns = QHBoxLayout()
        ok = QPushButton("创建")
        cancel = QPushButton("取消")
        ok.clicked.connect(lambda: self._do_create_cat(e.text().strip(), dlg))
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        l.addLayout(btns)
        dlg.exec()

    def _do_create_cat(self, name, dlg):
        if name:
            self._project_service.create_category(name)
            self._refresh_cats()
            dlg.accept()

    def _do_rename_cat(self, name, dlg):
        if name and self._current_cat:
            self._project_service.rename_category(self._current_cat, name)
            self._current_cat = name
            self._refresh_cats()
            dlg.accept()

    def _delete_category(self):
        if self._current_cat and mb_ask(self, "确认", f"删除分类「{self._current_cat}」及其所有文档？"):
            self._project_service.delete_category(self._current_cat)
            self._current_cat = None
            self._current_doc = None
            self._editor.clear()
            self._refresh_cats()

    def _create_doc(self):
        if not self._current_cat:
            mb_info(self, "提示", "请先选择分类")
            return
        dlg = dialog_toplevel(self, "新建文档", 300, 120)
        l = QVBoxLayout(dlg)
        l.addWidget(QLabel("文档名称:"))
        e = QLineEdit()
        l.addWidget(e)
        btns = QHBoxLayout()
        ok = QPushButton("创建")
        cancel = QPushButton("取消")
        ok.clicked.connect(lambda: self._do_create_doc(e.text().strip(), dlg))
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        l.addLayout(btns)
        dlg.exec()

    def _do_create_doc(self, name, dlg):
        if name and self._current_cat:
            self._project_service.create_setting(self._current_cat, name)
            self._refresh_docs()
            dlg.accept()

    def _save_doc(self):
        if not self._current_cat or not self._current_doc or not self._content_modified:
            return
        self._project_service.update_setting(
            self._current_cat, self._current_doc,
            content=self._editor.toPlainText(),
        )
        self._content_modified = False

    def _delete_doc(self):
        if self._current_doc and mb_ask(self, "确认", f"删除文档「{self._current_doc}」？"):
            self._project_service.delete_setting(self._current_cat, self._current_doc)
            self._current_doc = None
            self._editor.clear()
            self._refresh_docs()

    def _export_all(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出设定", "", "Markdown (*.md)")
        if not path:
            return
        out = []
        for c in self._project_service.list_categories():
            out.append(f"# {c.name}")
            for d in self._project_service.list_settings(c.name):
                out.append(f"## {d.name}")
                out.append(d.content or "")
                out.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        mb_info(self, "导出完成", f"已导出到 {path}")
