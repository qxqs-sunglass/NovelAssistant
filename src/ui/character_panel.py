"""角色面板 — 列表 + 字段 + MD 简介 + 阵营标签（v3.0）"""
import json
import shiboken6
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLineEdit, QLabel,
    QTextEdit, QPushButton, QScrollArea, QCheckBox,
    QFormLayout, QFileDialog, QAbstractItemView,
)
from PySide6.QtCore import Qt

from src.ui.base_panel import BasePanel
from src.ui.common import mb_info, mb_error, mb_ask, mb_warn, dialog_toplevel


class CharacterPanel(BasePanel):
    """角色管理面板"""

    def __init__(self, event_bus, logger, project_service):
        self._project_service = project_service
        self._current_char_id: str | None = None
        self._bio_modified = False
        super().__init__(event_bus, logger)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: character list
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self._char_list = QListWidget()
        self._char_list.currentItemChanged.connect(self._on_char_selected)
        ll.addWidget(self._char_list)
        btns = QHBoxLayout()
        new_btn = QPushButton("+ 创建")
        new_btn.clicked.connect(self._create_character)
        del_btn = QPushButton("🗑 删除")
        del_btn.clicked.connect(self._delete_character)
        btns.addWidget(new_btn)
        btns.addWidget(del_btn)
        ll.addLayout(btns)
        splitter.addWidget(left)

        # Right: fields + bio
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)

        # Fields
        form = QHBoxLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("姓名")
        self._gender_edit = QLineEdit()
        self._gender_edit.setPlaceholderText("性别")
        self._age_edit = QLineEdit()
        self._age_edit.setPlaceholderText("年龄")
        self._birthday_edit = QLineEdit()
        self._birthday_edit.setPlaceholderText("生日")
        for e in [self._name_edit, self._gender_edit, self._age_edit, self._birthday_edit]:
            e.editingFinished.connect(self._save_fields)
        form.addWidget(QLabel("姓名:"))
        form.addWidget(self._name_edit)
        form.addWidget(QLabel("性别:"))
        form.addWidget(self._gender_edit)
        form.addWidget(QLabel("年龄:"))
        form.addWidget(self._age_edit)
        form.addWidget(QLabel("生日:"))
        form.addWidget(self._birthday_edit)
        rl.addLayout(form)

        # Bio
        rl.addWidget(QLabel("简介 (Markdown):"))
        self._bio_edit = QTextEdit()
        self._bio_edit.textChanged.connect(lambda: setattr(self, '_bio_modified', True))
        rl.addWidget(self._bio_edit)

        # Save bio button + 导出
        btn_row = QHBoxLayout()
        save_btn = QPushButton("💾 保存简介")
        save_btn.clicked.connect(self._save_bio)
        export_btn = QPushButton("📤 导出")
        export_btn.setToolTip("一键导出当前角色为 Markdown")
        export_btn.clicked.connect(self._export_character)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(export_btn)
        btn_row.addStretch()
        rl.addLayout(btn_row)

        rl.addWidget(QLabel("阵营:"))
        self._camp_tags = QLabel("无")
        self._camp_tags.setWordWrap(True)
        rl.addWidget(self._camp_tags)
        camp_btn = QPushButton("管理阵营")
        camp_btn.clicked.connect(self._show_camp_dialog)
        rl.addWidget(camp_btn)
        rl.addStretch()

        splitter.addWidget(right)
        splitter.setSizes([200, 500])
        layout.addWidget(splitter, 1)

    def _subscribe_events(self):
        self._event_bus.subscribe("character:created", lambda e: self._refresh_list())
        self._event_bus.subscribe("character:updated", lambda e: self._refresh_list())
        self._event_bus.subscribe("character:deleted", lambda e: self._on_char_deleted())
        self._event_bus.subscribe("camp:created", lambda e: self._refresh_all())
        self._event_bus.subscribe("camp:updated", lambda e: self._refresh_all())
        self._event_bus.subscribe("camp:deleted", lambda e: self._refresh_all())

    def on_show(self):
        self._refresh_all()

    def _refresh_all(self):
        self._refresh_list()

    def _refresh_list(self):
        # ★ v3修复: 重建列表前先记住滚动位置与当前选中项，重建后恢复，
        # 避免 clear() 导致滚动条跳回顶部、选中项错乱
        scroll = self._char_list.verticalScrollBar()
        prev_scroll = scroll.value() if scroll else 0
        prev_current = None
        if self._current_char_id:
            item = self._char_list.currentItem()
            prev_current = item.data(Qt.ItemDataRole.UserRole) if item else None
        else:
            prev_current = None

        cs = self._project_service.character_service
        chars = cs.list_characters()
        self._char_list.blockSignals(True)  # 重建期间屏蔽信号，避免 currentItemChanged 误触发
        try:
            self._char_list.clear()
            for ch in sorted(chars, key=lambda c: c.name):
                item = QListWidgetItem(ch.name)
                item.setData(Qt.ItemDataRole.UserRole, ch.char_id)
                self._char_list.addItem(item)
        finally:
            self._char_list.blockSignals(False)

        # 恢复选中项（优先保留之前的 current_char_id；否则回退到重建前的选中项）
        restore_id = self._current_char_id or prev_current
        if restore_id:
            for i in range(self._char_list.count()):
                it = self._char_list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == restore_id:
                    self._char_list.setCurrentItem(it)
                    break

        # 恢复滚动位置
        if scroll:
            scroll.setValue(prev_scroll)

    def _on_char_selected(self, item):
        # ★ v3修复: item 可能在信号回调中已被 clear() 删除（内部 C++ 对象失效），
        # 需先用 shiboken6 校验有效性，避免 "Internal C++ object already deleted"
        if not item or not shiboken6.isValid(item):
            return
        # 先读取 cid —— 必须在任何可能触发列表重建（update_character → character:updated
        # → _refresh_list → clear）的操作之前完成，否则后续 item 会失效
        cid = item.data(Qt.ItemDataRole.UserRole)
        if not cid:
            return
        # 保存上一个角色的未保存改动（此时 _current_char_id 仍指向旧角色）
        self._save_fields()
        self._save_bio()
        # 切换到新角色
        self._current_char_id = cid
        ch = self._project_service.character_service.get_character(cid)
        if not ch:
            return
        self._name_edit.setText(ch.name)
        self._gender_edit.setText(ch.gender or "")
        self._age_edit.setText(ch.age or "")
        self._birthday_edit.setText(ch.birthday or "")
        self._bio_edit.setPlainText(ch.bio or "")
        self._bio_modified = False
        self._refresh_camp_tags()

    def _refresh_camp_tags(self):
        if not self._current_char_id:
            return
        ch = self._project_service.character_service.get_character(self._current_char_id)
        if not ch or not ch.camp_ids:
            self._camp_tags.setText("无")
            return
        camps = []
        for cid in ch.camp_ids:
            c = self._project_service.character_service.get_camp(cid)
            if c:
                camps.append(c.name)
        self._camp_tags.setText(", ".join(camps) if camps else "无")

    def _owned_camp_ids(self) -> set[str]:
        """当前角色已关联的阵营 id 集合"""
        if self._current_char_id:
            ch = self._project_service.character_service.get_character(self._current_char_id)
            if ch:
                return set(ch.camp_ids)
        return set()

    @staticmethod
    def _build_camp_item(c, owned: set[str]) -> QListWidgetItem:
        """构建一个带勾选框的阵营列表项（camp_id 存入 UserRole）"""
        desc_preview = c.description[:30] + "..." if len(c.description) > 30 else c.description
        item = QListWidgetItem(f"{c.name}  — {desc_preview}")
        item.setData(Qt.ItemDataRole.UserRole, c.camp_id)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if c.camp_id in owned else Qt.CheckState.Unchecked
        )
        return item

    def _save_fields(self):
        if not self._current_char_id:
            return
        name = self._name_edit.text().strip()
        if not name:
            return
        ch = self._project_service.character_service.get_character(self._current_char_id)
        new_name = name
        new_gender = self._gender_edit.text().strip()
        new_age = self._age_edit.text().strip()
        new_birthday = self._birthday_edit.text().strip()
        # ★ v3修复: 字段无变化时跳过保存，避免每次都发布 character:updated
        # 事件导致列表被无谓重建（滚动条跳回顶部 / 选中项错乱）
        if ch and (
            ch.name == new_name
            and (ch.gender or "") == new_gender
            and (ch.age or "") == new_age
            and (ch.birthday or "") == new_birthday
        ):
            return
        try:
            self._project_service.character_service.update_character(
                self._current_char_id, name=new_name,
                gender=new_gender,
                age=new_age,
                birthday=new_birthday,
            )
        except Exception as e:
            mb_error(self, "错误", str(e))

    def _save_bio(self):
        if not self._current_char_id or not self._bio_modified:
            return
        try:
            self._project_service.character_service.update_character(
                self._current_char_id, bio=self._bio_edit.toPlainText(),
            )
            self._bio_modified = False
        except Exception as e:
            mb_error(self, "错误", str(e))

    def _export_character(self):
        """一键导出当前角色为 Markdown 文件"""
        if not self._current_char_id:
            mb_warn(self, "提示", "请先在左侧选择一个角色")
            return
        # 先保存未提交的改动
        self._save_fields()
        self._save_bio()

        ch = self._project_service.character_service.get_character(self._current_char_id)
        if not ch:
            mb_error(self, "错误", "角色数据不存在")
            return

        default_name = ch.name or "角色"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出角色", f"{default_name}.md", "Markdown (*.md)",
        )
        if not path:
            return

        # 组装 Markdown 内容
        camps = []
        for cid in ch.camp_ids:
            c = self._project_service.character_service.get_camp(cid)
            if c:
                camps.append(c.name)
        camp_text = ", ".join(camps) if camps else "无"

        parts = [
            f"# {ch.name}",
            "",
            f"- 性别: {ch.gender or '未填写'}",
            f"- 年龄: {ch.age or '未填写'}",
            f"- 生日: {ch.birthday or '未填写'}",
            f"- 阵营: {camp_text}",
            "",
            "## 简介",
            "",
            ch.bio or "（暂无简介）",
        ]
        content = "\n".join(parts)

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            mb_error(self, "错误", f"导出失败: {e}")
            return
        mb_info(self, "导出完成", f"角色「{ch.name}」已导出到:\n{path}")

    def _create_character(self):
        dlg = dialog_toplevel(self, "创建角色", 300, 120)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("角色名称:"))
        entry = QLineEdit()
        lay.addWidget(entry)
        btns = QHBoxLayout()
        ok = QPushButton("创建")
        cancel = QPushButton("取消")

        def do_create():
            name = entry.text().strip()
            if name:
                try:
                    self._project_service.character_service.create_character(name)
                    self._refresh_list()
                    dlg.accept()
                except ValueError as e:
                    mb_error(self, "错误", str(e))

        ok.clicked.connect(do_create)
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        dlg.exec()

    def _delete_character(self):
        if not self._current_char_id:
            return
        ch = self._project_service.character_service.get_character(self._current_char_id)
        if ch and mb_ask(self, "确认删除", f"确定删除角色「{ch.name}」？"):
            try:
                self._project_service.character_service.delete_character(self._current_char_id)
                self._current_char_id = None
                self._refresh_list()
            except Exception as e:
                mb_error(self, "错误", str(e))

    def _on_char_deleted(self):
        if self._current_char_id:
            ch = self._project_service.character_service.get_character(self._current_char_id)
            if not ch:
                self._current_char_id = None
        self._refresh_list()

    def _show_camp_dialog(self):
        """阵营管理对话框 — 列表 + 排序 + 增删改 + 勾选关联当前角色（★ 修复无法添加）"""
        cs = self._project_service.character_service

        dlg = dialog_toplevel(self, "管理阵营", 520, 460)
        layout = QVBoxLayout(dlg)

        tip = QLabel("☑ 勾选即生效（实时保存到当前角色）；选中条目可编辑，名称留空则新建")
        tip.setStyleSheet("color:#888;")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        # ── 列表区（含勾选框）+ 排序按钮 ──
        list_outer = QWidget()
        lo = QHBoxLayout(list_outer)
        lo.setContentsMargins(0, 0, 0, 0)
        camp_list = QListWidget()
        camp_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lo.addWidget(camp_list, 1)

        order_side = QWidget()
        ol = QVBoxLayout(order_side)
        ol.setContentsMargins(4, 0, 0, 0)
        up_btn = QPushButton("▲")
        up_btn.clicked.connect(lambda: self._move_camp_dlg(camp_list, cs, -1))
        down_btn = QPushButton("▼")
        down_btn.clicked.connect(lambda: self._move_camp_dlg(camp_list, cs, 1))
        ol.addWidget(up_btn)
        ol.addWidget(down_btn)
        ol.addStretch(1)
        order_side.setLayout(ol)
        lo.addWidget(order_side)
        layout.addWidget(list_outer, 1)

        # ── 编辑区 ──
        edit = QWidget()
        el = QFormLayout(edit)
        name_edit = QLineEdit()
        desc_edit = QLineEdit()
        el.addRow("名称:", name_edit)
        el.addRow("简介:", desc_edit)
        layout.addWidget(edit)

        # ── 公共回调 ──
        def apply_assoc():
            """将列表勾选结果实时写入当前角色的 camp_ids"""
            if not self._current_char_id:
                return
            selected = []
            for i in range(camp_list.count()):
                item = camp_list.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    selected.append(item.data(Qt.ItemDataRole.UserRole))
            try:
                cs.update_character(self._current_char_id, camp_ids=selected)
                self._refresh_camp_tags()
                self._refresh_list()
            except Exception as e:
                mb_error(self, "错误", f"更新阵营失败: {e}")

        def refresh_list():
            """重建列表，勾选当前角色所属阵营"""
            camp_list.blockSignals(True)
            try:
                camp_list.clear()
                owned = self._owned_camp_ids()
                for c in cs.list_camps():
                    camp_list.addItem(self._build_camp_item(c, owned))
            finally:
                camp_list.blockSignals(False)

        def on_select(item):
            if item:
                cid = item.data(Qt.ItemDataRole.UserRole)
                c = cs.get_camp(cid)
                if c:
                    name_edit.setText(c.name)
                    desc_edit.setText(c.description)

        def on_check_changed(item):
            # ★ 勾选变化实时应用，无需额外按钮
            if item is not None and self._current_char_id:
                apply_assoc()

        camp_list.currentItemChanged.connect(on_select)
        camp_list.itemChanged.connect(on_check_changed)
        refresh_list()

        # ── 按钮区 ──
        btns = QHBoxLayout()
        save_btn = QPushButton("保存阵营")
        delete_btn = QPushButton("🗑 删除")
        close_btn = QPushButton("关闭")

        def do_save():
            name = name_edit.text().strip()
            if not name:
                mb_warn(self, "提示", "请输入阵营名称")
                return
            try:
                item = camp_list.currentItem()
                if item:
                    cid = item.data(Qt.ItemDataRole.UserRole)
                    cs.update_camp(cid, name=name, description=desc_edit.text())
                    # 更新后若改名，同步列表显示
                    c = cs.get_camp(cid)
                    if c:
                        owned = self._owned_camp_ids()
                        item.setData(Qt.ItemDataRole.UserRole, c.camp_id)
                        item.setText(f"{c.name}  — {c.description[:30] + '...' if len(c.description) > 30 else c.description}")
                        item.setCheckState(
                            Qt.CheckState.Checked if c.camp_id in owned else Qt.CheckState.Unchecked
                        )
                else:
                    new_camp = cs.create_camp(name, desc_edit.text())
                    # ★ 新建阵营自动加入当前角色并勾选
                    if self._current_char_id:
                        ch = cs.get_character(self._current_char_id)
                        if ch and new_camp.camp_id not in ch.camp_ids:
                            cs.update_character(self._current_char_id,
                                                camp_ids=ch.camp_ids + [new_camp.camp_id])
                    refresh_list()
                    # 自动选中新建项
                    for i in range(camp_list.count()):
                        it = camp_list.item(i)
                        if it and it.data(Qt.ItemDataRole.UserRole) == new_camp.camp_id:
                            camp_list.setCurrentItem(it)
                            break
                name_edit.clear()
                desc_edit.clear()
                self._refresh_camp_tags()
                self._refresh_list()
            except Exception as e:
                mb_error(self, "错误", f"保存阵营失败: {e}")

        def do_delete():
            item = camp_list.currentItem()
            if item:
                cid = item.data(Qt.ItemDataRole.UserRole)
                c = cs.get_camp(cid)
                if c and mb_ask(self, "确认删除", f"确定要删除阵营「{c.name}」吗？"):
                    try:
                        cs.delete_camp(cid)
                        refresh_list()
                        self._refresh_camp_tags()
                        self._refresh_list()
                    except Exception as e:
                        mb_error(self, "错误", f"删除阵营失败: {e}")

        save_btn.clicked.connect(do_save)
        delete_btn.clicked.connect(do_delete)
        close_btn.clicked.connect(dlg.accept)
        btns.addWidget(save_btn)
        btns.addWidget(delete_btn)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        layout.addLayout(btns)
        dlg.exec()

    def _move_camp_dlg(self, camp_list, cs, delta: int):
        """移动选中阵营的显示顺序（▲▼）"""
        item = camp_list.currentItem()
        if not item:
            return
        idx = camp_list.row(item)
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= camp_list.count():
            return
        ids = [c.camp_id for c in cs.list_camps()]
        ids[idx], ids[new_idx] = ids[new_idx], ids[idx]
        cs.reorder_camps(ids)

        # 记住各阵营的勾选状态，重建后恢复
        owned = set()
        if self._current_char_id:
            ch = cs.get_character(self._current_char_id)
            if ch:
                owned = set(ch.camp_ids)

        camp_list.blockSignals(True)
        try:
            camp_list.clear()
            for c in cs.list_camps():
                camp_list.addItem(self._build_camp_item(c, owned))
        finally:
            camp_list.blockSignals(False)
        camp_list.setCurrentRow(new_idx)
        self._refresh_camp_tags()
        self._refresh_list()
