"""大纲面板 — 树形结构管理 + 三栏编辑器（v3.0，移除了 AI 生成功能）"""
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QListWidget, QListWidgetItem,
    QLineEdit, QTextEdit, QPushButton, QLabel, QComboBox, QMenu, QHeaderView, QMessageBox,
)
from PySide6.QtCore import Qt

from src.ui.base_panel import BasePanel
from src.ui.common import mb_info, mb_warn, mb_error, mb_ask, dialog_toplevel
from src.services.project_service import NodeStatus


class OutlinePanel(BasePanel):
    """大纲面板 — 三栏布局"""

    STATUS_MAP = {NodeStatus.TODO: "○ 待开始", NodeStatus.IN_PROGRESS: "● 进行中",
                  NodeStatus.COMPLETED: "✓ 已完成", NodeStatus.IGNORED: "⊘ 忽略"}
    STATUS_REV = {v: k for k, v in STATUS_MAP.items()}

    def __init__(self, event_bus, logger, project_service, config_manager=None):
        self._project_service = project_service
        self._config_manager = config_manager
        self._current_node_id: str | None = None
        self._content_modified = False
        self._expanded_ids: set[str] = set()
        self._rebuilding = False
        super().__init__(event_bus, logger)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Project toolbar
        tb = QHBoxLayout()
        self._project_combo = QComboBox()
        self._project_combo.currentTextChanged.connect(self._switch_project)
        tb.addWidget(QLabel("项目:"))
        tb.addWidget(self._project_combo, 1)
        new_btn = QPushButton("+ 新项目")
        new_btn.clicked.connect(self._create_project)
        tb.addWidget(new_btn)
        layout.addLayout(tb)

        # Three columns
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: outline tree
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabel("大纲树")
        self._tree.setColumnCount(1)
        self._tree.itemClicked.connect(self._on_node_selected)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.itemExpanded.connect(self._on_tree_expanded)
        self._tree.itemCollapsed.connect(self._on_tree_collapsed)
        ll.addWidget(self._tree)
        splitter.addWidget(left)

        # Middle: child list + buttons
        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(4, 4, 4, 4)
        ml.addWidget(QLabel("子节点"))
        self._child_list = QListWidget()
        ml.addWidget(self._child_list)
        # Buttons
        btn_grid = QVBoxLayout()
        for text, slot in [
            ("▲ 上移", self._move_child_up), ("▼ 下移", self._move_child_down),
            ("✏ 重命名", self._rename_selected), ("+ 新建", self._create_child),
            ("⬆ 升级", self._promote_node), ("⬇ 降级", self._demote_node),
            ("合并", self._merge_nodes), ("🗑 删除", self._delete_node),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            btn_grid.addWidget(btn)
        ml.addLayout(btn_grid)
        splitter.addWidget(mid)

        # Right: editor
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        # Title
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("标题")
        self._title_edit.editingFinished.connect(self._on_title_changed)
        rl.addWidget(QLabel("标题:"))
        rl.addWidget(self._title_edit)
        # Status
        self._status_combo = QComboBox()
        for s in self.STATUS_MAP.values():
            self._status_combo.addItem(s)
        self._status_combo.currentTextChanged.connect(self._on_status_changed)
        rl.addWidget(QLabel("状态:"))
        rl.addWidget(self._status_combo)
        # Content
        self._editor = QTextEdit()
        self._editor.setPlaceholderText("正文内容...")
        self._editor.textChanged.connect(lambda: setattr(self, '_content_modified', True))
        rl.addWidget(QLabel("内容:"))
        rl.addWidget(self._editor, 1)
        # Stats
        self._stats_label = QLabel("字数: 0")
        rl.addWidget(self._stats_label)
        # Save
        save_btn = QPushButton("💾 保存")
        save_btn.clicked.connect(self._save_node)
        rl.addWidget(save_btn)
        splitter.addWidget(right)

        splitter.setSizes([220, 180, 400])
        layout.addWidget(splitter, 1)

    def _subscribe_events(self):
        self._event_bus.subscribe("outline:tree_changed", lambda e: (self._refresh_tree(), self._update_stats()))
        self._event_bus.subscribe("project:switched", lambda e: self._refresh_all())

    def on_show(self):
        # ★ v3修复: 首次进入加载上次展开状态
        if not getattr(self, "_expanded_loaded", False):
            self._load_expanded_state()
            self._expanded_loaded = True
        self._refresh_all()

    def on_close(self):
        self._save_node()
        self._save_expanded_state()

    # ★ v3修复: 展开状态保存/恢复
    def _save_expanded_state(self):
        try:
            if not self._config_manager:
                return
            cfg = self._config_manager.load_app_config()
            cfg.outline_expanded_ids = sorted(self._expanded_ids)
            self._config_manager.save_app_config(cfg)
        except Exception:
            pass

    def _load_expanded_state(self):
        try:
            if not self._config_manager:
                return
            cfg = self._config_manager.load_app_config()
            ids = getattr(cfg, "outline_expanded_ids", []) or []
            self._expanded_ids = set(ids) if isinstance(ids, list) else set()
        except Exception:
            self._expanded_ids = set()

    # ── Project management ──

    def _refresh_all(self):
        self._refresh_project_list()
        self._refresh_tree()

    def _refresh_project_list(self):
        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        for p in self._project_service.list_projects():
            self._project_combo.addItem(p.name)
        cur = self._project_service.get_current_project()
        if cur:
            self._project_combo.setCurrentText(cur)
        self._project_combo.blockSignals(False)

    def _switch_project(self, name):
        if not name or name == self._project_service.get_current_project():
            return
        self._project_service.switch_project(name)
        self._refresh_all()

    def _create_project(self):
        dlg = dialog_toplevel(self, "创建项目", 300, 150)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("项目名称:"))
        entry = QLineEdit()
        lay.addWidget(entry)
        btns = QHBoxLayout()
        ok = QPushButton("创建")
        cancel = QPushButton("取消")
        ok.clicked.connect(lambda: self._do_create_project(entry.text().strip(), dlg))
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        dlg.exec()

    def _do_create_project(self, name, dlg):
        if name:
            self._project_service.create_project(name)
            self._refresh_all()
            dlg.accept()

    # ── Tree ──

    def _refresh_tree(self):
        expanded = self._expanded_ids or set()
        self._rebuilding = True
        try:
            self._tree.clear()
            if not self._project_service.get_current_project():
                return
            nodes = self._project_service.get_outline_tree()
            if not nodes:
                return
            roots = [n for n in nodes if n.parent_id is None]
            for root in sorted(roots, key=lambda n: n.order):
                self._insert_tree_node(None, root, nodes, expanded)
        finally:
            self._rebuilding = False

    def _insert_tree_node(self, parent, node, all_nodes, expanded):
        icon = {1: "📗", 2: "📘", 3: "📙", 4: "📕", 5: "📄"}.get(node.level.value, "📄")
        sicon = {NodeStatus.COMPLETED: "✓", NodeStatus.IN_PROGRESS: "●",
                 NodeStatus.TODO: "○", NodeStatus.IGNORED: "⊘"}.get(node.status, "○")
        item = QTreeWidgetItem([f"{icon} {sicon} {node.title}"])
        item.setData(0, Qt.ItemDataRole.UserRole, node.node_id)
        if parent:
            parent.addChild(item)
        else:
            self._tree.addTopLevelItem(item)
        if node.node_id in expanded:
            item.setExpanded(True)
        for cid in node.children_ids:
            child = next((n for n in all_nodes if n.node_id == cid), None)
            if child:
                self._insert_tree_node(item, child, all_nodes, expanded)

    def _on_node_selected(self, item):
        if not item:
            return
        self._save_node()
        nid = item.data(0, Qt.ItemDataRole.UserRole)
        self._current_node_id = nid
        node = self._project_service.get_node(nid)
        if not node:
            return
        self._title_edit.setText(node.title)
        self._status_combo.setCurrentText(self.STATUS_MAP.get(node.status, "○ 待开始"))
        self._editor.setPlainText(node.content)
        self._content_modified = False
        self._stats_label.setText(f"字数: {node.word_count}")
        self._refresh_children()

    def _on_tree_expanded(self, item):
        if self._rebuilding:
            return
        nid = item.data(0, Qt.ItemDataRole.UserRole)
        if nid:
            self._expanded_ids.add(nid)

    def _on_tree_collapsed(self, item):
        if self._rebuilding:
            return
        nid = item.data(0, Qt.ItemDataRole.UserRole)
        if nid:
            self._expanded_ids.discard(nid)

    def _on_tree_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if not item:
            return
        nid = item.data(0, Qt.ItemDataRole.UserRole)
        self._tree.setCurrentItem(item)
        self._on_node_selected(item)
        menu = QMenu(self)
        menu.addAction("更改父节点", lambda: self._change_parent())
        menu.addAction("✏ 重命名", self._rename_selected)
        menu.addSeparator()
        menu.addAction("+ 新建子节点", self._create_child)
        menu.addAction("合并子节点", self._merge_nodes)
        menu.addAction("🗑 删除", self._delete_node)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    # ── Operations ──

    def _refresh_children(self):
        self._child_list.clear()
        if not self._current_node_id:
            return
        node = self._project_service.get_node(self._current_node_id)
        if not node:
            return
        for cid in node.children_ids:
            child = self._project_service.get_node(cid)
            if child:
                item = QListWidgetItem(child.title)
                item.setData(Qt.ItemDataRole.UserRole, cid)
                self._child_list.addItem(item)

    def _create_child(self):
        if not self._current_node_id:
            mb_info(self, "提示", "请先选择父节点")
            return
        dlg = dialog_toplevel(self, "创建子节点", 300, 120)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("节点标题:"))
        entry = QLineEdit()
        lay.addWidget(entry)
        btns = QHBoxLayout()
        ok = QPushButton("创建")
        cancel = QPushButton("取消")
        ok.clicked.connect(lambda: self._do_create_child(entry.text().strip(), dlg))
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        dlg.exec()

    def _do_create_child(self, title, dlg):
        if title and self._current_node_id:
            self._project_service.create_node(self._current_node_id, title)
            self._refresh_tree()
            self._update_stats()
            dlg.accept()

    def _merge_nodes(self):
        items = self._child_list.selectedItems()
        if len(items) < 2:
            mb_info(self, "提示", "请在子节点列表中选择至少 2 个节点")
            return
        dlg = dialog_toplevel(self, "合并节点", 300, 120)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("合并后标题:"))
        entry = QLineEdit()
        lay.addWidget(entry)
        btns = QHBoxLayout()
        ok = QPushButton("合并")
        cancel = QPushButton("取消")
        ok.clicked.connect(lambda: self._do_merge(entry.text().strip(), items, dlg))
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        dlg.exec()

    def _do_merge(self, title, items, dlg):
        if title:
            ids = [i.data(Qt.ItemDataRole.UserRole) for i in items]
            self._project_service.merge_nodes(ids, title, parent_id=self._current_node_id)
            self._refresh_tree()
            self._update_stats()
            dlg.accept()

    def _delete_node(self):
        if not self._current_node_id:
            return
        node = self._project_service.get_node(self._current_node_id)
        if not node:
            return
        if mb_ask(self, "确认删除", f"确定删除「{node.title}」及其所有子节点？"):
            self._project_service.delete_node(self._current_node_id)
            self._current_node_id = None
            self._refresh_tree()
            self._update_stats()

    def _save_node(self):
        if not self._current_node_id or not self._content_modified:
            return
        title = self._title_edit.text().strip()
        content = self._editor.toPlainText()
        self._project_service.update_node(self._current_node_id, title=title, content=content)
        self._content_modified = False
        self._refresh_tree()
        self._update_stats()

    def _on_title_changed(self):
        self._content_modified = True

    def _on_status_changed(self, text):
        if not self._current_node_id:
            return
        status = self.STATUS_REV.get(text, NodeStatus.TODO)
        node = self._project_service.get_node(self._current_node_id)
        if node and node.status != status:
            self._project_service.update_node(self._current_node_id, status=status)
            self._content_modified = True
            self._refresh_tree()

    def _rename_selected(self):
        if not self._current_node_id:
            mb_info(self, "提示", "请先选择节点")
            return
        node = self._project_service.get_node(self._current_node_id)
        if not node:
            return
        dlg = dialog_toplevel(self, "重命名", 300, 120)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("新标题:"))
        entry = QLineEdit(node.title)
        lay.addWidget(entry)
        btns = QHBoxLayout()
        ok = QPushButton("确定")
        cancel = QPushButton("取消")
        ok.clicked.connect(lambda: self._do_rename(entry.text().strip(), dlg))
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        dlg.exec()

    def _do_rename(self, title, dlg):
        if title and self._current_node_id:
            self._project_service.update_node(self._current_node_id, title=title)
            self._refresh_tree()
            self._update_stats()
            dlg.accept()

    def _move_child_up(self):
        self._move_tree_node(-1)

    def _move_child_down(self):
        self._move_tree_node(1)

    def _move_tree_node(self, delta):
        items = self._child_list.selectedItems()
        if not items or not self._current_node_id:
            return
        cid = items[0].data(Qt.ItemDataRole.UserRole)
        self._project_service.move_node(cid, delta)
        self._refresh_tree()
        self._refresh_children()

    def _promote_node(self):
        if not self._current_node_id:
            mb_info(self, "提示", "请先选择节点")
            return
        try:
            self._project_service.promote_node(self._current_node_id)
            self._refresh_tree()
            self._update_stats()
        except ValueError as e:
            mb_info(self, "提示", str(e))

    def _demote_node(self):
        if not self._current_node_id:
            mb_info(self, "提示", "请先选择节点")
            return
        try:
            self._project_service.demote_node(self._current_node_id)
            self._refresh_tree()
            self._update_stats()
        except ValueError as e:
            mb_info(self, "提示", str(e))

    def _change_parent(self):
        if not self._current_node_id:
            return
        node = self._project_service.get_node(self._current_node_id)
        if not node:
            return
        all_nodes = self._project_service.get_outline_tree()
        dlg = dialog_toplevel(self, "选择新父节点", 360, 320)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"将「{node.title}」移动到哪个父节点下？"))
        tree = QTreeWidget()
        tree.setHeaderLabel("可选父节点")
        for n in all_nodes:
            if n.node_id != self._current_node_id:
                item = QTreeWidgetItem([n.title])
                item.setData(0, Qt.ItemDataRole.UserRole, n.node_id)
                tree.addTopLevelItem(item)
        lay.addWidget(tree)
        btns = QHBoxLayout()
        ok = QPushButton("确定")
        cancel = QPushButton("取消")

        def do_move():
            sel = tree.currentItem()
            if sel:
                pid = sel.data(0, Qt.ItemDataRole.UserRole)
                self._project_service.change_parent(self._current_node_id, pid)
                self._refresh_tree()
                self._update_stats()
                dlg.accept()

        ok.clicked.connect(do_move)
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        dlg.exec()

    def _update_stats(self):
        nodes = self._project_service.get_outline_tree()
        if nodes:
            self._stats_label.setText(f"大纲节点: {len(nodes)}")
