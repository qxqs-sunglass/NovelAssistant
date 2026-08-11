"""状态面板 — 项目统计 + AI 生成状态报告（v3.0）"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton, QGroupBox,
)
from PySide6.QtCore import Qt

from src.ui.base_panel import BasePanel
from src.ui.common import mb_warn, mb_ask


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
        # ★ v3补齐: 加载已保存的创作状态（持久化）
        self._load_saved_status()

    def _load_saved_status(self):
        """从项目目录加载已保存的创作状态"""
        try:
            ps = self._project_service
            if not ps.get_current_project():
                self._stats_label.setText("项目: 无\n大纲节点: 0\n角色: 0\n伏笔: 0\n设定分类: 0")
                self._status_text.setPlainText("（暂无项目，请先在大纲面板创建或选择项目）")
                return
            proj_dir = ps._get_project_dir()
            if proj_dir:
                status_file = proj_dir / "status.md"
                if status_file.exists():
                    with open(status_file, "r", encoding="utf-8") as f:
                        self._status_text.setPlainText(f.read())
                        return
            self._refresh_basic_status()
        except Exception:
            self._refresh_basic_status()

    def _save_status(self, content: str):
        """保存创作状态到项目目录"""
        try:
            ps = self._project_service
            proj_dir = ps._get_project_dir()
            if proj_dir:
                (proj_dir / "status.md").write_text(content, encoding="utf-8")
        except Exception:
            pass

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
        # ★ v3补齐: Token 消耗确认
        if not mb_ask(
            self,
            "Token 消耗提醒",
            "此功能需要将大量创作数据上传至 AI 进行分析，预计消耗较多 token。\n\n"
            "上传内容：\n"
            "• 大纲 L1~L4（不含正文 L5）\n"
            "• 角色栏所有角色信息\n"
            "• 设定栏所有设定信息\n"
            "• 伏笔栏未隐藏的伏笔信息\n\n"
            "是否继续？",
        ):
            return

        # ★ v3补齐: 收集完整上下文（大纲 L1~L4 + 角色 + 设定 + 伏笔）
        try:
            ps = self._project_service
            data_parts = []

            all_nodes = ps.get_outline_tree() or []
            l1_l4 = [n for n in all_nodes if getattr(n, "level", None) and n.level.value <= 4]
            l5_nodes = [n for n in all_nodes if getattr(n, "level", None) and n.level.value == 5]
            completed_l5 = sum(1 for n in l5_nodes if getattr(n, "status", None) and n.status.value == "completed")
            total_words = sum(getattr(n, "word_count", 0) or 0 for n in l5_nodes)
            completed_nodes = sum(1 for n in all_nodes if getattr(n, "status", None) and n.status.value == "completed")

            current = ps.get_current_project()

            # 1. 项目概况
            data_parts.append(f"【项目概况】\n项目: {current}\n"
                              f"大纲节点: {len(l1_l4)} (已完成 {completed_nodes})\n"
                              f"正文: {len(l5_nodes)} 章 ({completed_l5} 已完成) | 总字数: {total_words:,}")

            # 2. 大纲 L1~L4（含完整内容）
            if l1_l4:
                data_parts.append("\n【大纲树】")
                for n in sorted(l1_l4, key=lambda x: (x.level.value, x.order)):
                    level_name = {1: "L1-大纲", 2: "L2-卷纲", 3: "L3-简纲", 4: "L4-章纲"}.get(n.level.value, "")
                    status_icon = {"completed": "✓", "in_progress": "●", "todo": "○", "ignored": "⊘"}.get(
                        n.status.value, "○")
                    node = ps.get_node(n.node_id)
                    content = node.content if node and node.content else "(无内容)"
                    data_parts.append(f"- [{level_name}] {status_icon} {n.title}\n  {content}")

            # 3. 角色信息
            cs = ps.character_service
            char_ctx = cs.get_ai_context()
            if char_ctx:
                data_parts.append("\n" + char_ctx)

            # 4. 设定信息
            categories = ps.list_categories()
            if categories:
                data_parts.append("\n【设定信息】")
                for cat in categories:
                    docs = ps.list_docs(cat)
                    if docs:
                        data_parts.append(f"\n## {cat}")
                        for doc_name in docs[:5]:
                            content = ps.get_setting(cat, doc_name)
                            if content:
                                preview = content[:300] + "..." if len(content) > 300 else content
                                data_parts.append(f"- {doc_name}:\n  {preview}")

            # 5. 伏笔信息
            fs = ps.foreshadow_service
            foreshadow_ctx = fs.get_ai_context()
            if foreshadow_ctx:
                data_parts.append("\n" + foreshadow_ctx)

            full_data = "\n".join(data_parts)

            template = "请基于以下项目数据生成一份创作状态报告。"
            if self._config_manager:
                cfg = self._config_manager.load_app_config()
                t = getattr(cfg, 'status_prompt_template', '').strip()
                if t:
                    template = t
            template = (template
                        .replace("{project}", current or "")
                        .replace("{node_count}", str(len(l1_l4)))
                        .replace("{chapter_count}", str(len(l5_nodes)))
                        .replace("{completed_count}", str(completed_l5))
                        .replace("{word_count:,}", f"{total_words:,}")
                        .replace("{word_count}", str(total_words))
                        .replace("{data}", full_data))

            from src.services.ai_client import ChatMessage
            msgs = [ChatMessage("user", template)]
            from threading import Thread
            from datetime import datetime
            from PySide6.QtCore import QTimer

            self._status_text.setPlainText("⏳ AI 正在分析创作数据...")

            def generate():
                try:
                    resp = self._ai_client.chat(msgs, system_prompt="")
                    text = resp.content
                    # ★ v3补齐: 持久化保存 + 时间戳
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                    saved = f"🕐 {ts} | 共 {total_words:,} 字\n\n{text}"
                    QTimer.singleShot(0, lambda s=saved: self._status_text.setPlainText(s))
                    self._save_status(saved)
                except Exception as e:
                    QTimer.singleShot(0, lambda e=e: self._status_text.setPlainText(f"生成失败: {e}"))

            Thread(target=generate, daemon=True).start()
        except Exception as e:
            self._status_text.setPlainText(f"错误: {e}")

    def get_ai_context(self) -> str:
        """供 ChatPanel 调用：获取当前状态摘要（仅返回 AI 生成的内容）"""
        try:
            content = self._status_text.toPlainText()
            # 仅返回 AI 生成的状态（以时间戳开头），排除基础统计和提示文字
            if content and content.startswith("🕐"):
                return f"【当前创作状态】\n{content}"
        except Exception:
            pass
        return ""
