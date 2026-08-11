"""配置面板 — AI 源配置 + 功能提示词配置（v3.0）"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QListWidget, QListWidgetItem, QTabWidget,
    QLineEdit, QPushButton, QLabel, QCheckBox,
    QSlider, QSpinBox, QTextEdit, QGroupBox, QScrollArea,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
import json

from src.ui.base_panel import BasePanel
from src.ui.common import mb_info, mb_warn, mb_error, mb_ask, dialog_toplevel
from src.core.config_manager import ConfigManager, AISourceConfig


class ConfigPanel(BasePanel):
    """配置面板 — Tab1: AI源 | Tab2: 功能提示词"""

    def __init__(self, event_bus, logger, config_manager: ConfigManager, ai_client):
        self._config_manager = config_manager
        self._ai_client = ai_client
        self._current_source_name: str | None = None
        super().__init__(event_bus, logger)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._build_source_tab()
        self._build_prompt_tab()

    # ── Tab 1: AI 源配置 ──

    def _build_source_tab(self):
        tab = QWidget()
        hlay = QHBoxLayout(tab)

        # Left: source list
        left = QVBoxLayout()
        left.addWidget(QLabel("AI 源列表"))
        self._source_list = QListWidget()
        self._source_list.currentItemChanged.connect(self._on_source_selected)
        left.addWidget(self._source_list)
        bl = QHBoxLayout()
        add_btn = QPushButton("+ 新增")
        add_btn.clicked.connect(self._add_source)
        del_btn = QPushButton("🗑 删除")
        del_btn.clicked.connect(self._delete_source)
        bl.addWidget(add_btn)
        bl.addWidget(del_btn)
        left.addLayout(bl)
        hlay.addLayout(left)

        # Right: edit form
        right = QVBoxLayout()
        form = QFormLayout()
        self._s_name = QLineEdit()
        self._s_url = QLineEdit()
        self._s_key = QLineEdit()
        self._s_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._s_model = QLineEdit()
        self._s_model_minor = QLineEdit()
        self._s_reasoning = QCheckBox("支持深度思考")
        form.addRow("名称:", self._s_name)
        form.addRow("Base URL:", self._s_url)
        form.addRow("API Key:", self._s_key)
        form.addRow("模型:", self._s_model)
        form.addRow("备用模型:", self._s_model_minor)
        form.addRow("", self._s_reasoning)
        right.addLayout(form)

        btns = QHBoxLayout()
        save_btn = QPushButton("💾 保存")
        save_btn.clicked.connect(self._save_source)
        set_btn = QPushButton("✅ 设为当前")
        set_btn.clicked.connect(self._set_as_current)
        test_btn = QPushButton("🔌 测试连接")
        test_btn.clicked.connect(self._test_connection)
        btns.addWidget(save_btn)
        btns.addWidget(set_btn)
        btns.addWidget(test_btn)
        right.addLayout(btns)
        right.addStretch()
        hlay.addLayout(right)
        self._tabs.addTab(tab, "AI 源配置")

    def _build_prompt_tab(self):
        tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)

        cfg = self._config_manager.load_app_config()

        # Temperature
        g1 = QGroupBox("Temperature")
        f1 = QHBoxLayout(g1)
        self._temp_slider = QSlider(Qt.Orientation.Horizontal)
        self._temp_slider.setRange(0, 200)
        self._temp_slider.setValue(int(getattr(cfg, 'temperature', 1.0) * 100))
        self._temp_label = QLabel(f"{self._temp_slider.value() / 100:.2f}")
        self._temp_slider.valueChanged.connect(lambda v: self._temp_label.setText(f"{v/100:.2f}"))
        f1.addWidget(self._temp_slider)
        f1.addWidget(self._temp_label)
        layout.addWidget(g1)

        # Top-P
        g2 = QGroupBox("Top-P")
        f2 = QHBoxLayout(g2)
        self._top_p_slider = QSlider(Qt.Orientation.Horizontal)
        self._top_p_slider.setRange(0, 100)
        self._top_p_slider.setValue(int(getattr(cfg, 'top_p', 0.9) * 100))
        self._top_p_label = QLabel(f"{self._top_p_slider.value() / 100:.2f}")
        self._top_p_slider.valueChanged.connect(lambda v: self._top_p_label.setText(f"{v/100:.2f}"))
        f2.addWidget(self._top_p_slider)
        f2.addWidget(self._top_p_label)
        layout.addWidget(g2)

        # Max Tokens
        g3 = QGroupBox("Max Tokens")
        f3 = QHBoxLayout(g3)
        self._max_tokens_spin = QSpinBox()
        self._max_tokens_spin.setRange(256, 32768)
        self._max_tokens_spin.setSingleStep(256)
        self._max_tokens_spin.setValue(getattr(cfg, 'max_tokens', 2048))
        f3.addWidget(QLabel("最大输出:"))
        f3.addWidget(self._max_tokens_spin)
        f3.addStretch()
        layout.addWidget(g3)

        # ★ 自动续写
        g3a = QGroupBox("输出截断自动续写")
        f3a = QVBoxLayout(g3a)
        row_a = QHBoxLayout()
        self._auto_continue_check = QCheckBox("输出达到上限被截断时，自动让 AI 续写剩余内容")
        self._auto_continue_check.setChecked(getattr(cfg, 'auto_continue', True))
        row_a.addWidget(self._auto_continue_check)
        row_a.addStretch()
        f3a.addLayout(row_a)
        row_b = QHBoxLayout()
        row_b.addWidget(QLabel("最多续写轮数:"))
        self._continue_rounds_spin = QSpinBox()
        self._continue_rounds_spin.setRange(1, 10)
        self._continue_rounds_spin.setValue(getattr(cfg, 'max_continue_rounds', 3))
        row_b.addWidget(self._continue_rounds_spin)
        row_b.addStretch()
        f3a.addLayout(row_b)
        layout.addWidget(g3a)

        # Skill Text
        g4 = QGroupBox("对话 Skill 文本（工作流定义）")
        f4 = QVBoxLayout(g4)
        self._skill_text = QTextEdit()
        self._skill_text.setPlainText(getattr(cfg, 'chat_skill_text', '') or self._default_skill())
        self._skill_text.setMaximumHeight(120)
        f4.addWidget(self._skill_text)
        layout.addWidget(g4)

        # Status Prompt
        g5 = QGroupBox("状态提示词模板")
        f5 = QVBoxLayout(g5)
        self._status_template = QTextEdit()
        self._status_template.setPlainText(
            getattr(cfg, 'status_prompt_template', '') or
            "请基于以下项目数据生成创作状态报告。\n项目: {project}\n大纲节点: {node_count}\n数据: {data}"
        )
        self._status_template.setMaximumHeight(100)
        f5.addWidget(self._status_template)
        layout.addWidget(g5)

        # Save button
        save_btn = QPushButton("💾 保存功能提示词配置")
        save_btn.clicked.connect(self._save_prompt_config)
        layout.addWidget(save_btn)
        layout.addStretch()

        scroll.setWidget(inner)
        tab_layout = QVBoxLayout(tab)
        tab_layout.addWidget(scroll)
        self._tabs.addTab(tab, "功能提示词配置")

    def _default_skill(self):
        return (
            "【工作流】全局工作流程\n"
            "【输出纪律】直接执行用户指令，禁止不必要的分析、复述、自我确认。"
            "上下文仅为参考资料，只使用与当前指令直接相关的部分。\n"
            "【数据来源】当前项目数据库\n"
            "【执行步骤】\n"
            "1. 分析用户指令，将需要用到的大纲文档、设定信息、角色信息、伏笔信息，利用统一读取工具一口气批量读取。\n"
            "2. 执行用户指令进行内容生成。\n"
            "3. 分析伏笔条目，锁定可能需要删除的伏笔，利用读取工具进行读取。\n"
            "4. 使用伏笔工具，对已有的伏笔进行增删。"
        )

    # ── Events ──

    def on_show(self):
        self._refresh_source_list()
        self._reload_prompt_values()

    def _reload_prompt_values(self):
        """从磁盘重新加载功能提示词配置到 UI。

        ★ v3.1修复: 面板只初始化一次，保存后再次进入配置页时
        UI 仍是旧值，导致看起来"没有保存成功"。这里每次显示时
        重新加载，确保显示与磁盘一致。
        """
        try:
            cfg = self._config_manager.load_app_config()
        except Exception:
            return
        self._temp_slider.setValue(int(getattr(cfg, 'temperature', 1.0) * 100))
        self._top_p_slider.setValue(int(getattr(cfg, 'top_p', 0.9) * 100))
        self._max_tokens_spin.setValue(getattr(cfg, 'max_tokens', 2048))
        self._auto_continue_check.setChecked(getattr(cfg, 'auto_continue', True))
        self._continue_rounds_spin.setValue(getattr(cfg, 'max_continue_rounds', 3))
        self._skill_text.setPlainText(getattr(cfg, 'chat_skill_text', '') or self._default_skill())
        self._status_template.setPlainText(
            getattr(cfg, 'status_prompt_template', '') or
            "请基于以下项目数据生成创作状态报告。\n项目: {project}\n大纲节点: {node_count}\n数据: {data}"
        )

    # ── Source list ──

    def _refresh_source_list(self):
        self._source_list.clear()
        for s in self._config_manager.list_ai_sources():
            item = QListWidgetItem(s.name)
            item.setData(Qt.ItemDataRole.UserRole, s.name)
            self._source_list.addItem(item)

    def _on_source_selected(self, item):
        if not item:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        source = self._config_manager.get_ai_source(name)
        if not source:
            return
        self._current_source_name = name
        self._s_name.setText(source.name)
        self._s_url.setText(source.base_url)
        key = self._config_manager.get_api_key(name) or ""
        self._s_key.setText(key)
        self._s_model.setText(source.model)
        self._s_model_minor.setText(source.model_minor)
        self._s_reasoning.setChecked(getattr(source, 'supports_reasoning', False))

    def _clear_form(self):
        for w in [self._s_name, self._s_url, self._s_key, self._s_model, self._s_model_minor]:
            w.clear()
        self._s_reasoning.setChecked(False)
        self._current_source_name = None

    def _add_source(self):
        self._clear_form()
        self._s_name.setFocus()

    def _save_source(self):
        name = self._s_name.text().strip()
        url = self._s_url.text().strip()
        if not name or not url:
            mb_warn(self, "提示", "名称和 Base URL 为必填项")
            return
        source = AISourceConfig(
            name=name,
            base_url=url.rstrip("/"),
            model=self._s_model.text().strip(),
            model_minor=self._s_model_minor.text().strip(),
            temperature=self._temp_slider.value() / 100,
            top_p=self._top_p_slider.value() / 100,
            max_tokens=self._max_tokens_spin.value(),
            supports_reasoning=self._s_reasoning.isChecked(),
        )
        if self._current_source_name and self._current_source_name != name:
            self._config_manager.remove_ai_source(self._current_source_name)
        self._config_manager.add_ai_source(source)
        api_key = self._s_key.text().strip()
        if api_key:
            self._config_manager.set_api_key(name, api_key)
        self._current_source_name = name
        self._refresh_source_list()
        current = self._config_manager.get_current_ai_source()
        if current and current.name == name:
            self._ai_client.configure(
                base_url=source.base_url, api_key=api_key,
                model=source.model, model_minor=source.model_minor,
                temperature=source.temperature, top_p=source.top_p,
                max_tokens=source.max_tokens,
            )
        mb_info(self, "成功", f"AI 源「{name}」已保存")

    def _set_as_current(self):
        name = self._s_name.text().strip()
        if not name:
            mb_warn(self, "提示", "请先选择或输入 AI 源名称")
            return
        source = self._config_manager.get_ai_source(name)
        if not source:
            mb_warn(self, "提示", f"AI 源「{name}」不存在，请先保存")
            return
        api_key = self._config_manager.get_api_key(name) or ""
        self._config_manager.set_current_ai_source(name)
        self._ai_client.configure(
            base_url=source.base_url, api_key=api_key,
            model=source.model, model_minor=source.model_minor,
            temperature=source.temperature, top_p=source.top_p,
            max_tokens=source.max_tokens,
        )
        mb_info(self, "成功", f"当前 AI 源已切换为「{name}」")

    def _delete_source(self):
        if self._current_source_name and mb_ask(self, "确认", f"删除 AI 源「{self._current_source_name}」？"):
            self._config_manager.remove_ai_source(self._current_source_name)
            self._clear_form()
            self._refresh_source_list()

    def _test_connection(self):
        url = self._s_url.text().strip()
        if not url:
            mb_warn(self, "提示", "请填写 Base URL")
            return
        try:
            from openai import OpenAI
            client = OpenAI(base_url=url.rstrip("/"), api_key=self._s_key.text().strip() or "none")
            r = client.models.list()
            models = [m.id for m in list(r)[:10]]
            mb_info(self, "连接成功", f"连接成功！\n可用模型（前10个）:\n" + "\n".join(models))
        except Exception as e:
            mb_error(self, "连接失败", str(e))

    def _save_prompt_config(self):
        cfg = self._config_manager.load_app_config()
        cfg.temperature = self._temp_slider.value() / 100
        cfg.top_p = self._top_p_slider.value() / 100
        cfg.max_tokens = self._max_tokens_spin.value()
        cfg.auto_continue = self._auto_continue_check.isChecked()
        cfg.max_continue_rounds = self._continue_rounds_spin.value()
        cfg.chat_skill_text = self._skill_text.toPlainText()
        cfg.status_prompt_template = self._status_template.toPlainText()
        self._config_manager.save_app_config(cfg)
        mb_info(self, "成功", "功能提示词配置已保存")
