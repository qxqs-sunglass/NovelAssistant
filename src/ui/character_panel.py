"""角色面板 — 列表 + 字段 + MD 简介 + 阵营标签（v3.1 重构）"""
import shiboken6
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLineEdit, QLabel,
    QTextEdit, QPushButton, QFileDialog, QAbstractItemView,
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
        export_all_btn = QPushButton("📤 导出全部")
        export_all_btn.setToolTip("一键导出全部角色为 Markdown")
        export_all_btn.clicked.connect(self._export_all_characters)
        btns.addWidget(new_btn)
        btns.addWidget(del_btn)
        btns.addWidget(export_all_btn)
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
        camp_btns = QHBoxLayout()
        create_camp_btn = QPushButton("＋ 创建阵营")
        create_camp_btn.clicked.connect(self._create_camp_dialog)
        select_camp_btn = QPushButton("☑ 选择阵营")
        select_camp_btn.clicked.connect(self._show_camp_dialog)
        camp_btns.addWidget(create_camp_btn)
        camp_btns.addWidget(select_camp_btn)
        rl.addLayout(camp_btns)
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
        """刷新角色列表 + 当前角色的阵营标签（避免 camp 变更后标签残留旧数据）"""
        self._refresh_list()
        self._refresh_camp_tags()

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

    def _camp_names_of(self, ch) -> list[str]:
        """将角色 camp_ids 解析为阵营名称列表（按已声明的顺序）"""
        cs = self._project_service.character_service
        return [c.name for cid in ch.camp_ids if (c := cs.get_camp(cid))]

    def _refresh_camp_tags(self):
        if not self._current_char_id:
            return
        ch = self._project_service.character_service.get_character(self._current_char_id)
        if not ch:
            self._camp_tags.setText("无")
            return
        names = self._camp_names_of(ch)
        self._camp_tags.setText(", ".join(names) if names else "无")

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

    def _character_markdown(self, ch) -> str:
        """将单个角色组装为 Markdown 文本（使用实例的阵营服务）"""
        names = self._camp_names_of(ch)
        camp_text = ", ".join(names) if names else "无"
        return "\n".join([
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
        ])

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

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._character_markdown(ch))
        except Exception as e:
            mb_error(self, "错误", f"导出失败: {e}")
            return
        mb_info(self, "导出完成", f"角色「{ch.name}」已导出到:\n{path}")

    def _export_all_characters(self):
        """一键导出全部角色为一个 Markdown 文件"""
        cs = self._project_service.character_service
        chars = cs.list_characters()
        if not chars:
            mb_warn(self, "提示", "暂无角色可导出")
            return

        # 导出前先保存当前角色的未提交改动，避免漏掉最新内容
        self._save_fields()
        self._save_bio()

        default_name = "全部角色"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出全部角色", f"{default_name}.md", "Markdown (*.md)",
        )
        if not path:
            return

        blocks = [f"# 全部角色", "", f"> 共 {len(chars)} 位角色", ""]
        for i, ch in enumerate(chars, 1):
            full = cs.get_character(ch.char_id)
            if full:
                ch = full
            blocks.append(f"\n---\n\n### {i}. {ch.name}\n")
            blocks.append(self._character_markdown(ch))

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(blocks))
        except Exception as e:
            mb_error(self, "错误", f"导出失败: {e}")
            return
        mb_info(self, "导出完成", f"已导出全部角色（{len(chars)} 位）到:\n{path}")

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

    def _create_camp_dialog(self):
        """创建阵营弹窗 — 独立于选择阵营，仅负责创建，不自动关联当前角色"""
        cs = self._project_service.character_service

        dlg = dialog_toplevel(self, "创建阵营", 360, 200)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("阵营名称:"))
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("必填")
        layout.addWidget(name_edit)
        layout.addWidget(QLabel("简介 (可选):"))
        desc_edit = QLineEdit()
        layout.addWidget(desc_edit)

        btns = QHBoxLayout()
        ok = QPushButton("创建")
        cancel = QPushButton("取消")
        btns.addWidget(ok)
        btns.addWidget(cancel)
        btns.addStretch(1)
        layout.addLayout(btns)

        def do_create():
            name = name_edit.text().strip()
            if not name:
                mb_warn(self, "提示", "请输入阵营名称")
                return
            try:
                cs.create_camp(name, desc_edit.text())
                self._refresh_all()
                dlg.accept()
            except ValueError as e:
                mb_error(self, "错误", str(e))

        ok.clicked.connect(do_create)
        cancel.clicked.connect(dlg.reject)
        name_edit.returnPressed.connect(do_create)
        dlg.exec()

    def _show_camp_dialog(self):
        """选择阵营对话框 — 仅勾选关联当前角色（创建已分离到独立弹窗）"""
        cs = self._project_service.character_service

        dlg = dialog_toplevel(self, "选择阵营", 440, 420)
        layout = QVBoxLayout(dlg)

        tip = QLabel("☑ 勾选即生效（实时保存到当前角色）")
        tip.setStyleSheet("color:#888;")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        camp_list = QListWidget()
        camp_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(camp_list, 1)

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

        def on_check_changed(item):
            # 勾选变化实时应用，无需额外按钮
            if item is not None and self._current_char_id:
                selected = []
                for i in range(camp_list.count()):
                    it = camp_list.item(i)
                    if it.checkState() == Qt.CheckState.Checked:
                        selected.append(it.data(Qt.ItemDataRole.UserRole))
                try:
                    cs.update_character(self._current_char_id, camp_ids=selected)
                    self._refresh_all()
                except Exception as e:
                    mb_error(self, "错误", f"更新阵营失败: {e}")

        camp_list.itemChanged.connect(on_check_changed)
        refresh_list()

        btns = QHBoxLayout()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        layout.addLayout(btns)
        dlg.exec()
