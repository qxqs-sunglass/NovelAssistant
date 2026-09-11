"""导出面板 — 统合导出入口（v3.2）

将原先分散在大纲/角色/设定/日志各面板的导出功能统一到本页面：
  - 左侧复选树：按需勾选要导出的内容（大纲节点 / 角色 / 设定文档 / 伏笔 / 日志）
  - 中部统计：实时显示勾选数量
  - 右侧选项：输出目录、是否合并为单文件、开始导出

统一输出 Markdown（日志默认 .txt，可切换为 .md）。
"""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QSplitter, QLabel, QTextEdit,
    QPushButton, QLineEdit, QComboBox, QFileDialog, QCheckBox,
    QTreeWidget, QTreeWidgetItem, QWidget, QGroupBox,
)
from PySide6.QtCore import Qt

from src.ui.base_panel import BasePanel
from src.ui.common import mb_info, mb_warn, mb_error


class ExportPanel(BasePanel):
    """统合导出面板"""

    # 内容类型常量
    TYPE_OUTLINE = "outline"
    TYPE_CHARACTERS = "characters"
    TYPE_SETTINGS = "settings"
    TYPE_FORESHADOW = "foreshadow"
    TYPE_LOG = "log"

    def __init__(self, event_bus, logger, project_service, config_manager=None):
        self._project_service = project_service
        self._config_manager = config_manager
        super().__init__(event_bus, logger)

    # ==================== UI ====================

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 顶部标题 + 工具栏
        tb = QHBoxLayout()
        tb.addWidget(QLabel("📤 导出中心 — 勾选需要导出的内容"))
        tb.addStretch()
        btn_all = QPushButton("全选")
        btn_all.clicked.connect(lambda: self._set_all_checked(True))
        btn_none = QPushButton("全不选")
        btn_none.clicked.connect(lambda: self._set_all_checked(False))
        btn_refresh = QPushButton("🔄 刷新")
        btn_refresh.clicked.connect(self._refresh_tree)
        tb.addWidget(btn_all)
        tb.addWidget(btn_none)
        tb.addWidget(btn_refresh)
        layout.addLayout(tb)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左：内容复选树 ──
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("内容选择"))
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["内容", "数量"])
        self._tree.setColumnWidth(0, 320)
        self._tree.itemChanged.connect(self._on_item_changed)
        ll.addWidget(self._tree)
        splitter.addWidget(left)

        # ── 右：统计 + 选项 ──
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)

        # 统计
        stat_box = QGroupBox("已勾选统计")
        sl = QVBoxLayout(stat_box)
        self._stat_label = QLabel("暂无勾选")
        self._stat_label.setWordWrap(True)
        sl.addWidget(self._stat_label)
        rl.addWidget(stat_box)

        # 输出选项
        opt_box = QGroupBox("导出选项")
        ol = QVBoxLayout(opt_box)

        ol.addWidget(QLabel("输出目录:"))
        dir_row = QHBoxLayout()
        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText("选择导出目录...")
        dir_row.addWidget(self._dir_edit, 1)
        browse = QPushButton("浏览")
        browse.clicked.connect(self._choose_dir)
        dir_row.addWidget(browse)
        ol.addLayout(dir_row)

        self._merge_check = QCheckBox("合并为单个 Markdown 文件")
        self._merge_check.setToolTip("勾选后所有内容合并写入一个 .md 文件；否则按类型分文件输出")
        ol.addWidget(self._merge_check)

        self._log_fmt = QComboBox()
        self._log_fmt.addItems(["日志格式: .txt", "日志格式: .md"])
        ol.addWidget(self._log_fmt)

        ol.addStretch()
        rl.addWidget(opt_box)

        # 导出按钮
        self._export_btn = QPushButton("🚀 开始导出")
        self._export_btn.setMinimumHeight(36)
        self._export_btn.clicked.connect(self._do_export)
        rl.addWidget(self._export_btn)

        splitter.addWidget(right)
        splitter.setSizes([520, 320])
        layout.addWidget(splitter, 1)

    def _subscribe_events(self):
        # 项目切换后刷新树
        self._event_bus.subscribe("project:switched", lambda e: self._refresh_tree())
        # 数据变更后刷新
        for evt in ("outline:tree_changed", "chapter:saved",
                    "character:created", "character:updated", "character:deleted",
                    "foreshadow:created", "foreshadow:updated", "foreshadow:deleted",
                    "setting:updated", "camp:created", "camp:updated",
                    "camp:deleted"):
            self._event_bus.subscribe(evt, self._on_data_changed)

    def _on_data_changed(self, event):
        self._schedule_refresh()

    def on_show(self):
        self._refresh_tree()

    # ==================== 树构建 ====================

    def _refresh_tree(self):
        """重建复选树（保留原有勾选状态尽量恢复）"""
        checked = self._collect_checked_keys()

        self._tree.blockSignals(True)
        self._tree.clear()

        self._build_outline_branch(checked)
        self._build_characters_branch(checked)
        self._build_settings_branch(checked)
        self._build_foreshadow_branch(checked)
        self._build_log_branch(checked)

        self._tree.blockSignals(False)
        self._on_item_changed(None, 0)

    def _build_outline_branch(self, checked: set):
        """构建大纲分支 — 完全展开的树，用户按需勾选"""
        root = QTreeWidgetItem(["📖 大纲", ""])
        root.setFlags(root.flags() | Qt.ItemFlag.ItemIsUserCheckable |
                      Qt.ItemFlag.ItemIsAutoTristate)
        root.setCheckState(0, Qt.CheckState.Unchecked)
        self._tree.addTopLevelItem(root)

        try:
            nodes = self._project_service.get_outline_tree()
        except Exception:
            nodes = []
        if not nodes:
            return

        by_id = {n.node_id: n for n in nodes}
        for n in nodes:
            n._children = []
        roots = [n for n in nodes if not n.parent_id or n.parent_id not in by_id]
        roots.sort(key=lambda n: n.order)
        for n in nodes:
            pid = n.parent_id
            if pid and pid in by_id:
                by_id[pid]._children.append(n)
        for r in roots:
            r._children.sort(key=lambda n: n.order)

        total = [0]
        for r in roots:
            self._add_outline_node(root, r, checked, total, by_id)
        root.setText(1, str(total[0]))

    def _add_outline_node(self, parent_item, node, checked: set, total: list, by_id: dict):
        item = QTreeWidgetItem([f"{'─' * max(node.level.value - 1, 0)} {node.title}", ""])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        key = f"outline:{node.node_id}"
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setCheckState(0, Qt.CheckState.Checked if key in checked else Qt.CheckState.Unchecked)
        parent_item.addChild(item)
        total[0] += 1
        for child in getattr(node, "_children", []):
            self._add_outline_node(item, child, checked, total, by_id)
        item.setExpanded(True)

    def _build_characters_branch(self, checked: set):
        root = QTreeWidgetItem(["👤 角色", ""])
        root.setFlags(root.flags() | Qt.ItemFlag.ItemIsUserCheckable |
                      Qt.ItemFlag.ItemIsAutoTristate)
        root.setCheckState(0, Qt.CheckState.Unchecked)
        self._tree.addTopLevelItem(root)
        try:
            chars = self._project_service.character_service.list_characters()
        except Exception:
            chars = []
        root.setText(1, str(len(chars)))
        for ch in chars:
            key = f"char:{ch.char_id}"
            item = QTreeWidgetItem([ch.name or "(未命名)", ""])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setCheckState(0, Qt.CheckState.Checked if key in checked else Qt.CheckState.Unchecked)
            root.addChild(item)

    def _build_settings_branch(self, checked: set):
        root = QTreeWidgetItem(["⚙ 设定", ""])
        root.setFlags(root.flags() | Qt.ItemFlag.ItemIsUserCheckable |
                      Qt.ItemFlag.ItemIsAutoTristate)
        root.setCheckState(0, Qt.CheckState.Unchecked)
        self._tree.addTopLevelItem(root)
        count = [0]
        try:
            cats = self._project_service.list_categories()
        except Exception:
            cats = []
        for cat in cats:
            cat_item = QTreeWidgetItem([cat, ""])
            cat_item.setFlags(cat_item.flags() | Qt.ItemFlag.ItemIsUserCheckable |
                              Qt.ItemFlag.ItemIsAutoTristate)
            cat_item.setCheckState(0, Qt.CheckState.Unchecked)
            root.addChild(cat_item)
            try:
                docs = [d for d in self._project_service.list_docs(cat) if d != "_placeholder"]
            except Exception:
                docs = []
            for doc in docs:
                key = f"setting:{cat}::{doc}"
                item = QTreeWidgetItem([doc, ""])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setData(0, Qt.ItemDataRole.UserRole, key)
                item.setCheckState(0, Qt.CheckState.Checked if key in checked else Qt.CheckState.Unchecked)
                cat_item.addChild(item)
                count[0] += 1
            if docs:
                cat_item.setExpanded(True)
        root.setText(1, str(count[0]))

    def _build_foreshadow_branch(self, checked: set):
        key = "foreshadow:all"
        item = QTreeWidgetItem(["🔮 伏笔", ""])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setCheckState(0, Qt.CheckState.Checked if key in checked else Qt.CheckState.Unchecked)
        try:
            fs = self._project_service.foreshadow_service.list_foreshadows(include_hidden=True)
        except Exception:
            fs = []
        item.setText(1, str(len(fs)))
        self._tree.addTopLevelItem(item)

    def _build_log_branch(self, checked: set):
        key = "log:all"
        item = QTreeWidgetItem(["📋 日志", ""])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setCheckState(0, Qt.CheckState.Checked if key in checked else Qt.CheckState.Unchecked)
        item.setText(1, str(len(self._load_log_lines())))
        self._tree.addTopLevelItem(item)

    # ==================== 勾选状态 ====================

    def _collect_checked_keys(self) -> set:
        """收集当前已勾选的叶子 key"""
        result = set()
        stack = [self._tree.topLevelItem(i) for i in range(self._tree.topLevelItemCount())]
        while stack:
            it_node = stack.pop()
            if it_node is None:
                continue
            key = it_node.data(0, Qt.ItemDataRole.UserRole)
            if key and it_node.checkState(0) == Qt.CheckState.Checked:
                result.add(key)
            for i in range(it_node.childCount()):
                stack.append(it_node.child(i))
        return result

    def _set_all_checked(self, state: bool):
        self._tree.blockSignals(True)
        st = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        stack = [self._tree.topLevelItem(i) for i in range(self._tree.topLevelItemCount())]
        while stack:
            n = stack.pop()
            if n is None:
                continue
            n.setCheckState(0, st)
            for i in range(n.childCount()):
                stack.append(n.child(i))
        self._tree.blockSignals(False)
        self._on_item_changed(None, 0)

    def _on_item_changed(self, item, column):
        keys = self._collect_checked_keys()
        stats = {
            "outline": sum(1 for k in keys if k.startswith("outline:")),
            "char": sum(1 for k in keys if k.startswith("char:")),
            "setting": sum(1 for k in keys if k.startswith("setting:")),
            "foreshadow": 1 if "foreshadow:all" in keys else 0,
            "log": 1 if "log:all" in keys else 0,
        }
        parts = []
        if stats["outline"]:
            parts.append(f"大纲节点: {stats['outline']}")
        if stats["char"]:
            parts.append(f"角色: {stats['char']}")
        if stats["setting"]:
            parts.append(f"设定文档: {stats['setting']}")
        if stats["foreshadow"]:
            parts.append("伏笔: 全部")
        if stats["log"]:
            parts.append("日志: 全部")
        self._stat_label.setText("\n".join(parts) if parts else "暂无勾选")

    def _schedule_refresh(self):
        self._refresh_tree()

    # ==================== 导出执行 ====================

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if d:
            self._dir_edit.setText(d)

    def _default_dir(self) -> str:
        """默认导出目录：workspace/exports/<项目名>"""
        try:
            proj = self._project_service.get_current_project() or "project"
        except Exception:
            proj = "project"
        base = os.path.join(os.getcwd(), "workspace", "exports", proj)
        return base

    def _load_log_lines(self) -> list[str]:
        log_dir = getattr(self._logger, "log_dir", None) or "workspace/logs"
        lines = []
        try:
            files = []
            for root, _dirs, fnames in os.walk(log_dir):
                for fn in fnames:
                    if fn.endswith(".log"):
                        files.append(os.path.join(root, fn))
            files.sort(reverse=True)
            for fp in files[:5]:
                with open(fp, encoding="utf-8") as f:
                    for line in f:
                        lines.append(line.rstrip("\n"))
        except Exception:
            pass
        return lines

    def _do_export(self):
        keys = self._collect_checked_keys()
        if not keys:
            mb_warn(self, "提示", "请先勾选要导出的内容")
            return

        out_dir = self._dir_edit.text().strip() or self._default_dir()
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            mb_error(self, "导出失败", f"无法创建输出目录:\n{e}")
            return

        merge = self._merge_check.isChecked()
        log_md = self._log_fmt.currentIndex() == 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        written = []
        errors = []
        try:
            merged_parts = []
            if merge:
                merged_parts.append(f"# 内容导出\n\n> 导出时间: {now}\n")

            # ── 大纲 ──
            outline_keys = [k for k in keys if k.startswith("outline:")]
            if outline_keys:
                text = self._render_outline(outline_keys)
                if text:
                    if merge:
                        merged_parts.append(text)
                    else:
                        p = os.path.join(out_dir, "大纲.md")
                        self._write(p, text)
                        written.append(p)

            # ── 角色 ──
            char_keys = [k for k in keys if k.startswith("char:")]
            if char_keys:
                text = self._render_characters(char_keys)
                if text:
                    if merge:
                        merged_parts.append(text)
                    else:
                        p = os.path.join(out_dir, "角色.md")
                        self._write(p, text)
                        written.append(p)

            # ── 设定 ──
            setting_keys = [k for k in keys if k.startswith("setting:")]
            if setting_keys:
                text = self._render_settings(setting_keys)
                if text:
                    if merge:
                        merged_parts.append(text)
                    else:
                        p = os.path.join(out_dir, "设定.md")
                        self._write(p, text)
                        written.append(p)

            # ── 伏笔 ──
            if "foreshadow:all" in keys:
                text = self._render_foreshadow()
                if text:
                    if merge:
                        merged_parts.append(text)
                    else:
                        p = os.path.join(out_dir, "伏笔.md")
                        self._write(p, text)
                        written.append(p)

            # ── 日志 ──
            if "log:all" in keys:
                ext = "md" if log_md else "txt"
                lines = self._load_log_lines()
                content = "\n".join(lines)
                if content:
                    if merge:
                        merged_parts.append(f"# 日志\n\n```\n{content}\n```\n")
                    else:
                        p = os.path.join(out_dir, f"日志.{ext}")
                        self._write(p, content)
                        written.append(p)

            if merge and merged_parts:
                p = os.path.join(out_dir, "导出汇总.md")
                self._write(p, "\n\n---\n\n".join(merged_parts))
                written.append(p)
        except Exception as e:
            errors.append(str(e))

        if errors:
            mb_error(self, "导出失败", "\n".join(errors))
            return

        if not written:
            mb_warn(self, "提示", "没有可导出的内容（可能是所选项为空）")
            return

        self._logger.log(f"导出完成，共 {len(written)} 个文件 → {out_dir}", "ExportPanel", "INFO")
        mb_info(self, "导出完成", "已导出以下文件：\n" + "\n".join(written))

    # ── 内容渲染 ──

    def _render_outline(self, keys: list[str]) -> str:
        """按勾选的节点渲染 Markdown（# 数量随层级，节点内容只输出一次）"""
        try:
            nodes = self._project_service.get_outline_tree()
        except Exception:
            return ""
        wanted = {k.split(":", 1)[1] for k in keys}
        lines = ["# 大纲", ""]
        for n in sorted(nodes, key=lambda x: (x.level.value, x.order)):
            if n.node_id not in wanted:
                continue
            try:
                node = self._project_service.get_node(n.node_id) or n
            except Exception:
                node = n
            lines.append(f"{'#' * (node.level.value + 1)} {node.title}")
            body = (getattr(node, "content", "") or "").strip()
            if body:
                lines.append("")
                lines.append(body)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _render_characters(self, keys: list[str]) -> str:
        cs = self._project_service.character_service
        wanted = {k.split(":", 1)[1] for k in keys}
        blocks = ["# 角色", ""]
        for cid in wanted:
            try:
                ch = cs.get_character(cid)
            except Exception:
                ch = None
            if not ch:
                continue
            blocks.append(self._character_markdown(ch, cs))
            blocks.append("")
        return "\n".join(blocks).rstrip() + "\n"

    def _character_markdown(self, ch, cs) -> str:
        """单个角色 → Markdown（含阵营名称解析）"""
        names = []
        for cid in (ch.camp_ids or []):
            try:
                camp = cs.get_camp(cid)
                if camp:
                    names.append(camp.name)
            except Exception:
                pass
        camp_text = ", ".join(names) if names else "无"
        return "\n".join([
            f"## {ch.name}",
            "",
            f"- 性别: {ch.gender or '未填写'}",
            f"- 年龄: {ch.age or '未填写'}",
            f"- 生日: {ch.birthday or '未填写'}",
            f"- 阵营: {camp_text}",
            "",
            f"### 简介",
            "",
            ch.bio or "（暂无简介）",
        ])

    def _render_settings(self, keys: list[str]) -> str:
        ps = self._project_service
        by_cat: dict[str, list[str]] = {}
        for k in keys:
            rest = k.split(":", 1)[1]
            if "::" not in rest:
                continue
            cat, doc = rest.split("::", 1)
            by_cat.setdefault(cat, []).append(doc)
        lines = ["# 设定", ""]
        for cat, docs in by_cat.items():
            lines.append(f"## {cat}")
            lines.append("")
            for doc in docs:
                lines.append(f"### {doc}")
                lines.append("")
                try:
                    content = ps.get_setting(cat, doc) or ""
                except Exception:
                    content = ""
                lines.append(content)
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _render_foreshadow(self) -> str:
        try:
            fs = self._project_service.foreshadow_service.list_foreshadows(include_hidden=True)
        except Exception:
            fs = []
        lines = ["# 伏笔", ""]
        for i, f in enumerate(fs, 1):
            mark = "（已隐藏）" if f.hidden else ""
            lines.append(f"{i}. {f.content}{mark}")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _write(path: str, content: str):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
