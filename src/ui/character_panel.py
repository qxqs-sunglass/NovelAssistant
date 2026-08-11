"""角色面板 — 列表 + 字段 + MD 简介 + 阵营标签（v3.0）"""
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLineEdit, QLabel,
    QTextEdit, QPushButton, QScrollArea, QCheckBox,
    QFormLayout,
)
from PySide6.QtCore import Qt

from src.ui.base_panel import BasePanel
from src.ui.common import mb_error, mb_ask, mb_warn, dialog_toplevel


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

        # Save bio button
        save_btn = QPushButton("💾 保存简介")
        save_btn.clicked.connect(self._save_bio)
        rl.addWidget(save_btn)

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
        cs = self._project_service.character_service
        chars = cs.list_characters()
        self._char_list.clear()
        for ch in sorted(chars, key=lambda c: c.name):
            item = QListWidgetItem(ch.name)
            item.setData(Qt.ItemDataRole.UserRole, ch.char_id)
            self._char_list.addItem(item)

    def _on_char_selected(self, item):
        if not item:
            return
        self._save_fields()
        self._save_bio()
        cid = item.data(Qt.ItemDataRole.UserRole)
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

    def _save_fields(self):
        if not self._current_char_id:
            return
        name = self._name_edit.text().strip()
        if not name:
            return
        try:
            self._project_service.character_service.update_character(
                self._current_char_id, name=name,
                gender=self._gender_edit.text().strip(),
                age=self._age_edit.text().strip(),
                birthday=self._birthday_edit.text().strip(),
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
        """阵营管理对话框 — 列表 + 排序 + 增删改 + 自动关联当前角色（★ v3修复）"""
        cs = self._project_service.character_service

        dlg = dialog_toplevel(self, "管理阵营", 500, 420)
        layout = QVBoxLayout(dlg)

        # ── 列表区 + 排序按钮 ──
        list_outer = QWidget()
        lo = QHBoxLayout(list_outer)
        lo.setContentsMargins(0, 0, 0, 0)
        camp_list = QListWidget()
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

        def refresh_list():
            camp_list.clear()
            for c in cs.list_camps():
                desc_preview = c.description[:30] + "..." if len(c.description) > 30 else c.description
                camp_list.addItem(f"{c.name}  — {desc_preview}")

        def on_select(item):
            if item:
                camps = cs.list_camps()
                idx = camp_list.row(item)
                if idx < len(camps):
                    name_edit.setText(camps[idx].name)
                    desc_edit.setText(camps[idx].description)

        camp_list.currentItemChanged.connect(on_select)
        refresh_list()

        # ── 按钮区 ──
        btns = QHBoxLayout()
        save_btn = QPushButton("新建/更新")
        delete_btn = QPushButton("🗑 删除")
        close_btn = QPushButton("关闭")

        def do_save():
            name = name_edit.text().strip()
            if not name:
                mb_warn(self, "提示", "请输入阵营名称")
                return
            try:
                item = camp_list.currentItem()
                camps = cs.list_camps()
                if item and camp_list.row(item) < len(camps):
                    cs.update_camp(camps[camp_list.row(item)].camp_id,
                                   name=name, description=desc_edit.text())
                else:
                    new_camp = cs.create_camp(name, desc_edit.text())
                    # ★ 自动关联当前角色
                    if self._current_char_id:
                        ch = cs.get_character(self._current_char_id)
                        if ch and new_camp.camp_id not in ch.camp_ids:
                            cs.update_character(self._current_char_id,
                                                camp_ids=ch.camp_ids + [new_camp.camp_id])
                refresh_list()
                name_edit.clear()
                desc_edit.clear()
                self._refresh_camp_tags()
                self._refresh_character_list()
            except Exception as e:
                mb_error(self, "错误", f"保存阵营失败: {e}")

        def do_delete():
            item = camp_list.currentItem()
            if item:
                camps = cs.list_camps()
                idx = camp_list.row(item)
                if idx < len(camps) and mb_ask(self, "确认删除", f"确定要删除阵营「{camps[idx].name}」吗？"):
                    try:
                        cs.delete_camp(camps[idx].camp_id)
                        refresh_list()
                        self._refresh_camp_tags()
                        self._refresh_character_list()
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
        camp_list.clear()
        for c in cs.list_camps():
            desc_preview = c.description[:30] + "..." if len(c.description) > 30 else c.description
            camp_list.addItem(f"{c.name}  — {desc_preview}")
        camp_list.setCurrentRow(new_idx)
        self._refresh_camp_tags()
        self._refresh_character_list()
