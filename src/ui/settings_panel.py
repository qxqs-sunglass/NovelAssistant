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
        # ★ v3补齐: 分类排序
        b1_sort = QHBoxLayout()
        cat_up = QPushButton("▲ 上移")
        cat_up.clicked.connect(self._move_cat_up)
        cat_down = QPushButton("▼ 下移")
        cat_down.clicked.connect(self._move_cat_down)
        b1_sort.addWidget(cat_up)
        b1_sort.addWidget(cat_down)
        b1_sort.addStretch()
        ll.addLayout(b1_sort)
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
        ren_doc = QPushButton("重命名")
        ren_doc.clicked.connect(self._rename_doc_btn)
        del_doc = QPushButton("🗑 删除")
        del_doc.clicked.connect(self._delete_doc)
        b2.addWidget(new_doc)
        b2.addWidget(ren_doc)
        b2.addWidget(del_doc)
        ml.addLayout(b2)
        b2_sort = QHBoxLayout()
        doc_up = QPushButton("▲ 上移")
        doc_up.clicked.connect(self._move_doc_up)
        doc_down = QPushButton("▼ 下移")
        doc_down.clicked.connect(self._move_doc_down)
        b2_sort.addWidget(doc_up)
        b2_sort.addWidget(doc_down)
        b2_sort.addStretch()
        ml.addLayout(b2_sort)
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

    def _subscribe_events(self):
        # ★ v3补齐: 项目切换时自动保存并清空编辑器
        self._event_bus.subscribe("project:switched", lambda e: self._on_project_switched())

    def _on_project_switched(self):
        self._save_doc()
        self._current_cat = None
        self._current_doc = None
        self._editor.clear()
        self._content_modified = False

    def on_show(self):
        self._refresh_cats()

    def on_close(self):
        self._save_doc()

    def _refresh_cats(self):
        self._cat_list.clear()
        for c in self._project_service.list_categories():
            # ★ v3修复: list_categories 返回字符串列表
            item = QListWidgetItem(c)
            item.setData(Qt.ItemDataRole.UserRole, c)
            self._cat_list.addItem(item)

    def _refresh_docs(self):
        self._doc_list.clear()
        if not self._current_cat:
            return
        for d in self._project_service.list_docs(self._current_cat):
            item = QListWidgetItem(d)
            item.setData(Qt.ItemDataRole.UserRole, d)
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
            # ★ v3修复: get_setting 直接返回内容字符串
            content = self._project_service.get_setting(self._current_cat, self._current_doc) or ""
            self._editor.setPlainText(content)
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
            # ★ v3修复: service 无 create_category，用 v2 同款占位文件方式
            self._project_service.save_setting(name, "_placeholder", "")
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
            # ★ v3修复: 用 save_setting 创建文档（含初始标题）
            self._project_service.save_setting(self._current_cat, name, f"# {name}")
            self._refresh_docs()
            dlg.accept()

    def _save_doc(self):
        if not self._current_cat or not self._current_doc or not self._content_modified:
            return
        # ★ v3修复: 用 save_setting 保存内容
        self._project_service.save_setting(
            self._current_cat, self._current_doc,
            self._editor.toPlainText(),
        )
        self._content_modified = False

    def _delete_doc(self):
        if self._current_doc and mb_ask(self, "确认", f"删除文档「{self._current_doc}」？"):
            self._project_service.delete_setting(self._current_cat, self._current_doc)
            self._current_doc = None
            self._editor.clear()
            self._refresh_docs()

    # ── ★ v3补齐: 分类/文档排序与文档重命名 ──

    def _move_cat_up(self):
        """分类上移一位"""
        item = self._cat_list.currentItem()
        if not item:
            return
        idx = self._cat_list.row(item)
        if idx <= 0:
            return
        cats = [self._cat_list.item(i).text() for i in range(self._cat_list.count())]
        cats[idx], cats[idx - 1] = cats[idx - 1], cats[idx]
        self._project_service.reorder_categories(cats)
        self._refresh_cats()
        self._cat_list.setCurrentRow(idx - 1)

    def _move_cat_down(self):
        """分类下移一位"""
        item = self._cat_list.currentItem()
        if not item:
            return
        idx = self._cat_list.row(item)
        cats = [self._cat_list.item(i).text() for i in range(self._cat_list.count())]
        if idx >= len(cats) - 1:
            return
        cats[idx], cats[idx + 1] = cats[idx + 1], cats[idx]
        self._project_service.reorder_categories(cats)
        self._refresh_cats()
        self._cat_list.setCurrentRow(idx + 1)

    def _rename_doc_btn(self):
        """按钮触发文档重命名"""
        item = self._doc_list.currentItem()
        if not item or not self._current_cat:
            mb_info(self, "提示", "请先选择一个文档")
            return
        old_name = item.text()
        dlg = dialog_toplevel(self, "重命名文档", 300, 120)
        l = QVBoxLayout(dlg)
        l.addWidget(QLabel("新名称:"))
        e = QLineEdit(old_name)
        l.addWidget(e)
        btns = QHBoxLayout()
        ok = QPushButton("确定")
        cancel = QPushButton("取消")

        def do_rename():
            new_name = e.text().strip()
            if new_name and new_name != old_name:
                self._project_service.rename_setting(self._current_cat, old_name, new_name)
                if self._current_doc == old_name:
                    self._current_doc = new_name
                self._refresh_docs()
                dlg.accept()

        ok.clicked.connect(do_rename)
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        l.addLayout(btns)
        dlg.exec()

    def _move_doc_up(self):
        """文档上移一位"""
        item = self._doc_list.currentItem()
        if not item or not self._current_cat:
            return
        idx = self._doc_list.row(item)
        if idx <= 0:
            return
        docs = [self._doc_list.item(i).text() for i in range(self._doc_list.count())]
        docs[idx], docs[idx - 1] = docs[idx - 1], docs[idx]
        self._project_service.reorder_docs(self._current_cat, docs)
        self._refresh_docs()
        self._doc_list.setCurrentRow(idx - 1)

    def _move_doc_down(self):
        """文档下移一位"""
        item = self._doc_list.currentItem()
        if not item or not self._current_cat:
            return
        idx = self._doc_list.row(item)
        docs = [self._doc_list.item(i).text() for i in range(self._doc_list.count())]
        if idx >= len(docs) - 1:
            return
        docs[idx], docs[idx + 1] = docs[idx + 1], docs[idx]
        self._project_service.reorder_docs(self._current_cat, docs)
        self._refresh_docs()
        self._doc_list.setCurrentRow(idx + 1)

    def _export_all(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出设定", "", "Markdown (*.md)")
        if not path:
            return
        out = []
        for c in self._project_service.list_categories():
            out.append(f"# {c}")
            for d in self._project_service.list_docs(c):
                out.append(f"## {d}")
                out.append(self._project_service.get_setting(c, d) or "")
                out.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        mb_info(self, "导出完成", f"已导出到 {path}")
