"""状态面板 — 项目统计 + AI 生成状态报告（v3.0）"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton, QGroupBox,
)
from PySide6.QtCore import Qt

from src.ui.base_panel import BasePanel
from src.ui.common import mb_warn


class StatusPanel(BasePanel):
    """创作状态面板"""

    def __init__(self, event_bus, logger, ai_client, project_service, config_manager=None):
        self._ai_client = ai_client
        self._project_service = project_service
        self._config_manager = config_manager
        super().__init__(event_bus, logger)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        # Basic stats
        stats_group = QGroupBox("项目概览")
        s_layout = QVBoxLayout(stats_group)
        self._stats_label = QLabel("加载中...")
        self._stats_label.setWordWrap(True)
        s_layout.addWidget(self._stats_label)
        layout.addWidget(stats_group)

        # AI generate
        gen_group = QGroupBox("AI 生成状态报告")
        g_layout = QVBoxLayout(gen_group)
        gen_btn = QPushButton("🤖 生成状态报告")
        gen_btn.clicked.connect(self._on_ai_generate)
        g_layout.addWidget(gen_btn)
        self._status_text = QTextEdit()
        self._status_text.setPlaceholderText("点击上方按钮生成创作状态报告...")
        self._status_text.setReadOnly(True)
        g_layout.addWidget(self._status_text)
        layout.addWidget(gen_group, 1)

    def on_show(self):
        self._refresh_basic_status()

    def _refresh_basic_status(self):
        try:
            current = self._project_service.get_current_project()
            nodes = self._project_service.get_outline_tree()
            chars = self._project_service.character_service.list_characters()
            fs = self._project_service.foreshadow_service.list_foreshadows()
            cats = self._project_service.list_categories()
            info = (
                f"项目: {current or '无'}\n"
                f"大纲节点: {len(nodes) if nodes else 0}\n"
                f"角色: {len(chars)}\n"
                f"伏笔: {len(fs)}\n"
                f"设定分类: {len(cats)}"
            )
            self._stats_label.setText(info)
        except Exception:
            self._stats_label.setText("无法加载项目信息")

    def _on_ai_generate(self):
        if not self._ai_client:
            mb_warn(self, "AI 未配置", "请先在配置面板中配置 AI 源。")
            return
        if not self._config_manager or not self._config_manager.get_current_ai_source():
            mb_warn(self, "AI 未配置", "请先在配置面板中激活 AI 源。")
            return
        try:
            current = self._project_service.get_current_project()
            nodes = self._project_service.get_outline_tree()
            chars = self._project_service.character_service.list_characters()
            fs = self._project_service.foreshadow_service.list_foreshadows()

            data = f"项目: {current}\n大纲节点数: {len(nodes) if nodes else 0}\n角色数: {len(chars)}\n伏笔数: {len(fs)}"

            template = "请基于以下项目数据生成一份创作状态报告。"
            if self._config_manager:
                cfg = self._config_manager.load_app_config()
                t = getattr(cfg, 'status_prompt_template', '').strip()
                if t:
                    template = t
            template = template.replace("{project}", current or "").replace(
                "{node_count}", str(len(nodes) if nodes else 0)).replace("{data}", data)

            from src.services.ai_client import ChatMessage
            msgs = [ChatMessage("user", template)]
            from threading import Thread

            def generate():
                try:
                    resp = self._ai_client.chat(msgs, system_prompt="")
                    self._status_text.setPlainText(resp.content)
                except Exception as e:
                    self._status_text.setPlainText(f"生成失败: {e}")

            Thread(target=generate, daemon=True).start()
        except Exception as e:
            self._status_text.setPlainText(f"错误: {e}")
