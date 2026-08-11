"""对话面板 — 气泡卡片 + 流式渲染 + 深度思考 + 异步加载（v3.0 PySide6）"""
import json
import threading
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame,
    QLabel, QTextEdit, QPushButton, QTextBrowser,
    QComboBox, QCheckBox, QTreeWidget, QTreeWidgetItem,
    QSplitter, QSizePolicy, QApplication, QMenu,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont

from src.ui.base_panel import BasePanel
from src.ui.common import (
    dialog_toplevel, ReasoningWindow, DetailPopup,
)
from src.services.ai_client import AIClient, ChatMessage, AIClientError
from src.services.session_manager import SessionManager


class MessageBubble(QFrame):
    """单条消息气泡卡片"""
    CLICKED = Signal(dict)

    def __init__(self, role: str, content: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        self._meta: dict = {}
        self._setup_ui(content)

    def _setup_ui(self, content: str):
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)

        if self.role == "system":
            self._build_system(content)
        elif self.role in ("user", "assistant"):
            self._build_chat_bubble(content)

    def _build_system(self, content: str):
        self.setStyleSheet("background: #e9ecef; border-radius: 4px;")
        label = QLabel(content)
        label.setWordWrap(True)
        label.setStyleSheet("color: #666; font-size: 12px;")
        self.layout().addWidget(label)

    def _build_chat_bubble(self, content: str):
        is_user = self.role == "user"
        if is_user:
            self.setStyleSheet("background: #0078d4; border-radius: 8px;")
        else:
            self.setStyleSheet("background: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px;")

        # Role label
        role_label = QLabel("👤 你" if is_user else "🤖 AI")
        role_label.setStyleSheet(
            f"color: {'#e8f0fe' if is_user else '#999'}; font-size: 10px; font-weight:bold; border:none;"
        )
        self.layout().addWidget(role_label)

        # Content
        self._text = QTextBrowser()
        self._text.setReadOnly(True)
        self._text.setHtml(
            f"<p style='color:{'white' if is_user else '#333'}; white-space:pre-wrap;'>{content}</p>"
        )
        self._text.setStyleSheet(f"background: transparent; border: none; color: {'white' if is_user else '#333'};")
        self._text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layout().addWidget(self._text)

    def set_meta(self, meta: dict):
        self._meta = meta

    def meta(self) -> dict:
        return self._meta

    def append_stream(self, text: str):
        """流式追加文本"""
        cursor = self._text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._text.setTextCursor(cursor)
        current = self._text.toPlainText()
        self._text.setHtml(
            f"<p style='color:{'white' if self.role == 'user' else '#333'}; white-space:pre-wrap;'>{current}{text}</p>"
        )

    def set_content(self, text: str):
        """设置完整内容"""
        self._text.setHtml(
            f"<p style='color:{'white' if self.role == 'user' else '#333'}; white-space:pre-wrap;'>{text}</p>"
        )


class CollapsibleCard(QFrame):
    """折叠卡片 — 点击展开/收起详情"""

    def __init__(self, icon: str, title: str, detail: str, parent=None):
        super().__init__(parent)
        self._detail = detail
        self._title = title
        self._expanded = False
        self._setup_ui(icon)

    def _setup_ui(self, icon: str):
        self.setStyleSheet("background: #eef1f5; border-radius: 4px;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)

        # Header (clickable)
        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        self._header_btn = QPushButton(f"{icon} {self._title}  ▸ 点击展开")
        self._header_btn.setStyleSheet("background: transparent; border: none; color: #555; text-align: left;")
        self._header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_btn.clicked.connect(self._toggle)
        hl.addWidget(self._header_btn)
        hl.addStretch()
        detail_btn = QPushButton("[右键 查看详情]")
        detail_btn.setStyleSheet("background: transparent; border: none; color: #777; font-size: 10px;")
        detail_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        detail_btn.clicked.connect(lambda: DetailPopup.show(self, self._title, self._detail))
        hl.addWidget(detail_btn)
        layout.addWidget(header)

        # Detail (hidden by default)
        self._detail_widget = QTextEdit()
        self._detail_widget.setReadOnly(True)
        self._detail_widget.setPlainText(self._detail)
        self._detail_widget.setMaximumHeight(150)
        self._detail_widget.setVisible(False)
        layout.addWidget(self._detail_widget)

    def _toggle(self):
        self._expanded = not self._expanded
        self._detail_widget.setVisible(self._expanded)
        arrow = "▾" if self._expanded else "▸"
        self._header_btn.setText(f"{self._header_btn.text()[0]} {self._title}  {arrow} " + ("收起" if self._expanded else "点击展开"))


class ChatPanel(BasePanel):
    """AI 对话面板 — 气泡卡片 + 流式渲染 + 深度思考（v3.0）"""

    SKILL = (
        "【工作流】全局工作流程\n"
        "【输出纪律】直接执行用户指令，禁止不必要的分析、复述、自我确认。上下文仅为参考资料，只使用与当前指令直接相关的部分。\n"
        "【数据来源】当前项目数据库\n"
        "【执行步骤】\n"
        "1. 分析用户指令，将需要用到的大纲文档、设定信息、角色信息、伏笔信息，利用统一读取工具一口气批量读取。\n"
        "2. 执行用户指令进行内容生成。\n"
        "3. 分析伏笔条目，锁定可能需要删除的伏笔，利用读取工具进行读取。\n"
        "4. 使用伏笔工具，对已有的伏笔进行增删。"
    )

    chunk_received = Signal(str)
    response_ended = Signal(dict)
    # ★ v3修复: 深度思考事件跨线程桥接（AI 线程 emit → 主线程槽执行）
    reasoning_chunk_sig = Signal(str)
    reasoning_end_sig = Signal()
    # ★ v4修复: 错误/工具调用/工具结果事件同样需要跨线程桥接
    response_error_sig = Signal(dict)
    tool_call_sig = Signal(dict)
    tool_result_sig = Signal(dict)
    # ★ v4修复: 流式结束后的 UI 复位（避免在 AI 线程直接操作控件）
    stream_finished = Signal()

    def __init__(self, event_bus, logger,
                 ai_client: AIClient, session_manager: SessionManager,
                 project_service, config_manager=None, tool_registry=None):
        self._ai_client = ai_client
        self._session_manager = session_manager
        self._project_service = project_service
        self._config_manager = config_manager
        self._tool_registry = tool_registry

        self._current_session_id: str | None = None
        self._response_buffer = ""
        self._is_streaming = False
        self._streaming_bubble: MessageBubble | None = None
        self._reasoning_window: ReasoningWindow | None = None
        self._current_reasoning = ""
        self._pending_tool_calls: list = []
        self._msg_data: list = []
        self._session_history: list = []
        # ★ v3修复: 提示词状态
        self._sys_prompt = ""
        self._add_prompt = ""
        self._add_enabled = False
        # ★ v3修复: 内容勾选状态 {key: checked}
        self._content_selected: dict[str, bool] = {}
        self._content_panel_visible = False
        # ★ v3修复: 内容树重建/批量更新时的信号屏蔽标志
        self._content_updating = False

        super().__init__(event_bus, logger)

        # Connect thread-safe signals
        self.chunk_received.connect(self._do_insert_chunk)
        self.response_ended.connect(self._do_response_end)
        self.reasoning_chunk_sig.connect(self._do_reasoning_chunk)
        self.reasoning_end_sig.connect(self._do_reasoning_end)
        self.response_error_sig.connect(self._do_response_error)
        self.tool_call_sig.connect(self._do_tool_call)
        self.tool_result_sig.connect(self._do_tool_result)
        self.stream_finished.connect(self._do_stream_finished)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # ── Toolbar ──
        tb = QHBoxLayout()
        tb.addWidget(QLabel("项目:"))
        self._project_combo = QComboBox()
        self._project_combo.currentTextChanged.connect(self._on_chat_project)
        tb.addWidget(self._project_combo)
        tb.addWidget(QLabel("会话:"))
        self._session_combo = QComboBox()
        self._session_combo.setMinimumWidth(150)
        self._session_combo.currentTextChanged.connect(self._on_session_selected)
        tb.addWidget(self._session_combo)
        new_sess = QPushButton("+")
        new_sess.clicked.connect(self._new_session)
        del_sess = QPushButton("🗑")
        del_sess.clicked.connect(self._delete_session)
        tb.addWidget(new_sess)
        tb.addWidget(del_sess)
        self._tool_check = QCheckBox("🔧 工具")
        self._tool_check.setChecked(True)
        tb.addWidget(self._tool_check)
        layout.addLayout(tb)

        # ── Prompt header ──
        prompt_header = QHBoxLayout()
        self._prompt_summary = QLabel("提示词: 无")
        self._prompt_summary.setStyleSheet("color: #666; font-size: 11px;")
        prompt_btn = QPushButton("📝 提示词")
        prompt_btn.clicked.connect(self._toggle_prompts)
        prompt_header.addWidget(prompt_btn)
        prompt_header.addWidget(self._prompt_summary, 1)
        # ★ v3修复: 内容勾选按钮
        self._content_summary_btn = QPushButton("📎 勾选")
        self._content_summary_btn.clicked.connect(self._toggle_content_panel)
        prompt_header.addWidget(self._content_summary_btn)
        layout.addLayout(prompt_header)

        # ★ v3修复: 内容勾选面板（默认隐藏）
        self._content_panel = QWidget()
        cp_layout = QVBoxLayout(self._content_panel)
        cp_layout.setContentsMargins(4, 0, 4, 0)
        cp_header = QHBoxLayout()
        self._content_count_label = QLabel("已选: 0")
        self._content_count_label.setStyleSheet("color: #666; font-size: 11px;")
        sel_all = QPushButton("全选")
        sel_all.setFixedWidth(52)
        sel_all.clicked.connect(self._select_all_content)
        desel_all = QPushButton("全不选")
        desel_all.setFixedWidth(52)
        desel_all.clicked.connect(self._deselect_all_content)
        cp_header.addWidget(self._content_count_label)
        cp_header.addStretch(1)
        cp_header.addWidget(sel_all)
        cp_header.addWidget(desel_all)
        cp_layout.addLayout(cp_header)
        self._content_tree = QTreeWidget()
        self._content_tree.setHeaderHidden(True)
        self._content_tree.setMaximumHeight(180)
        self._content_tree.itemChanged.connect(self._on_content_item_changed)
        cp_layout.addWidget(self._content_tree)
        self._content_panel.hide()
        layout.addWidget(self._content_panel)

        # ── Messages area ──
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setStyleSheet("background: #f5f6fa; border: none;")
        self._scroll_inner = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_inner)
        self._scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll_layout.addStretch()
        self._scroll_area.setWidget(self._scroll_inner)
        layout.addWidget(self._scroll_area, 1)
        # ★ v3修复: 消息区右键菜单（复制全文/重试）
        self._scroll_area.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._scroll_area.customContextMenuRequested.connect(self._show_msg_menu)

        # ── Input area ──
        input_frame = QWidget()
        il = QHBoxLayout(input_frame)
        il.setContentsMargins(0, 4, 0, 0)
        self._input_text = QTextEdit()
        self._input_text.setMaximumHeight(80)
        self._input_text.setPlaceholderText("输入消息... (Ctrl+Enter 发送)")
        # ★ v3补齐: 实时 token 估算
        self._input_text.textChanged.connect(self._update_token_estimate)
        # Use QShortcut instead of eventFilter on non-QObject
        from PySide6.QtGui import QShortcut, QKeySequence
        sc = QShortcut(QKeySequence("Ctrl+Return"), self._input_text)
        sc.activated.connect(self._on_send)
        il.addWidget(self._input_text)
        self._token_label = QLabel("")
        self._token_label.setStyleSheet("color:#999; font-size:10px;")
        self._token_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._token_label.setFixedWidth(60)
        il.addWidget(self._token_label)
        self._send_btn = QPushButton("发送")
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setStyleSheet("background:#0078d4; color:white; padding:8px 16px; font-weight:bold;")
        il.addWidget(self._send_btn)
        layout.addWidget(input_frame)

    def _subscribe_events(self):
        self._event_bus.subscribe("ai:response_chunk", self._on_chunk)
        self._event_bus.subscribe("ai:response_end", self._on_response_end_evt)
        self._event_bus.subscribe("ai:response_error", self._on_response_error_evt)
        self._event_bus.subscribe("ai:tool_call", self._on_tool_call_evt)
        self._event_bus.subscribe("ai:tool_result", self._on_tool_result_evt)
        self._event_bus.subscribe("ai:reasoning_chunk", self._on_reasoning_chunk)
        self._event_bus.subscribe("ai:reasoning_end", self._on_reasoning_end)
        self._event_bus.subscribe("project:switched", self._on_project_changed_evt)

    def on_show(self):
        # ★ v3修复: 恢复提示词状态 + 上次会话
        self._load_prompt_state()
        self._restore_last_session()
        self._refresh_session_list()
        self._refresh_chat_projects()
        # ★ v3移植: 刷新内容勾选树
        self._refresh_content_tree()

    def on_close(self):
        # ★ v3修复: 保存提示词状态 + 当前会话
        self._save_prompt_state()
        try:
            if self._config_manager and self._current_session_id:
                cfg = self._config_manager.load_app_config()
                cfg.last_session_id = self._current_session_id
                self._config_manager.save_app_config(cfg)
        except Exception:
            pass

    # ★ v3修复: 提示词状态保存/恢复
    def _save_prompt_state(self):
        try:
            if not self._config_manager:
                return
            cfg = self._config_manager.load_app_config()
            cfg.last_sys_prompt_content = self._sys_prompt
            cfg.last_add_prompt_content = self._add_prompt
            cfg.last_add_enabled = self._add_enabled
            self._config_manager.save_app_config(cfg)
        except Exception:
            pass

    def _load_prompt_state(self):
        try:
            if not self._config_manager:
                return
            cfg = self._config_manager.load_app_config()
            self._sys_prompt = getattr(cfg, "last_sys_prompt_content", "") or ""
            self._add_prompt = getattr(cfg, "last_add_prompt_content", "") or ""
            self._add_enabled = bool(getattr(cfg, "last_add_enabled", False))
            self._update_prompt_summary()
        except Exception:
            pass

    def _update_prompt_summary(self):
        try:
            summary = f"系统提示词: {len(self._sys_prompt)} 字"
            if self._add_enabled:
                summary += f" + 附加: {len(self._add_prompt)} 字"
            self._prompt_summary.setText(f"提示词: {summary}")
        except Exception:
            pass

    def _restore_last_session(self):
        """恢复上次关闭时打开的会话"""
        try:
            if not self._config_manager:
                return
            cfg = self._config_manager.load_app_config()
            sid = getattr(cfg, "last_session_id", "") or ""
            if sid and self._current_session_id is None:
                # 会话仍存在则恢复
                s = self._session_manager.get_session(sid)
                if s:
                    self._switch_session(sid)
        except Exception:
            pass

    # ── Session management ──

    def _refresh_session_list(self):
        self._session_combo.blockSignals(True)
        self._session_combo.clear()
        sessions = self._session_manager.list_sessions()
        for s in sessions:
            self._session_combo.addItem(f"{s.title[:20]} ({s.message_count}条)", s.session_id)
        self._session_combo.blockSignals(False)

    def _on_session_selected(self, text):
        idx = self._session_combo.currentIndex()
        if idx < 0:
            return
        sid = self._session_combo.itemData(idx)
        if sid:
            self._switch_session(sid)

    def _switch_session(self, session_id: str):
        self._current_session_id = session_id
        self._session_history = []
        self._clear_messages()
        self._add_system_bubble("⏳ 正在加载历史会话...")

        def load():
            try:
                s = self._session_manager.get_session(session_id)
                msgs = list(s.messages) if s else []
                self._session_history = msgs
                # ★ v3修复: 指定 receiver=self，回调在面板所在线程（主线程）执行
                QTimer.singleShot(0, self, lambda: self._render_history(msgs, 0))
            except Exception:
                QTimer.singleShot(0, self, lambda: self._add_system_bubble("加载失败"))

        threading.Thread(target=load, daemon=True).start()

    def _render_history(self, msgs: list, index: int):
        if index == 0:
            self._clear_messages()
        batch = msgs[index:index + 10]
        for m in batch:
            self._append_message(m.get("role", "system"), m.get("content", ""), m)
        if index + 10 < len(msgs):
            # ★ v3修复: 指定 receiver=self，分批调度回到主线程
            QTimer.singleShot(10, self, lambda: self._render_history(msgs, index + 10))

    def _new_session(self):
        s = self._session_manager.create_session()
        self._current_session_id = s.session_id
        self._clear_messages()
        self._session_history = []
        self._refresh_session_list()

    def _delete_session(self):
        if self._current_session_id:
            self._session_manager.delete_session(self._current_session_id)
            self._current_session_id = None
            self._clear_messages()
            self._session_history = []
            self._refresh_session_list()

    def _clear_messages(self):
        while self._scroll_layout.count() > 1:
            item = self._scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._msg_data = []
        self._streaming_bubble = None

    def _scroll_to_bottom(self):
        QTimer.singleShot(50, lambda: self._scroll_area.verticalScrollBar().setValue(
            self._scroll_area.verticalScrollBar().maximum()))

    # ── Message rendering ──

    def _add_system_bubble(self, text: str):
        bubble = MessageBubble("system", text)
        self._scroll_layout.insertWidget(self._scroll_layout.count() - 1, bubble)
        self._scroll_to_bottom()

    def _append_message(self, role: str, content: str, meta: dict = None,
                        streamable: bool = False, add_to_data: bool = True):
        if add_to_data:
            self._msg_data.append({"role": role, "content": content})

        # Tool message → collapsible
        if role == "tool":
            try:
                data = json.loads(content)
                if data.get("action") == "call":
                    self._add_collapsible("🔧", f"调用工具: {data.get('tool', '?')}",
                                          json.dumps(data.get("args", {}), ensure_ascii=False))
                elif data.get("action") == "result":
                    self._add_collapsible("✅", f"工具结果: {data.get('tool', '?')}",
                                          str(data.get("result", "")))
                return
            except Exception:
                pass

        # System
        if role == "system":
            self._add_system_bubble(content)
            return

        # User / Assistant bubble
        align_layout = QHBoxLayout()
        bubble = MessageBubble(role, content)
        bubble.set_meta(meta or {})

        if role == "user":
            align_layout.addStretch()
            align_layout.addWidget(bubble, 0)
        else:
            align_layout.addWidget(bubble, 0)
            align_layout.addStretch()

        container = QWidget()
        container.setLayout(align_layout)
        self._scroll_layout.insertWidget(self._scroll_layout.count() - 1, container)

        if streamable:
            self._streaming_bubble = bubble

        # Assistant meta: reasoning / tool_calls
        if role == "assistant" and meta:
            reasoning = meta.get("reasoning")
            if reasoning:
                self._add_collapsible("🧠", "深度思考", reasoning)
            for tc in meta.get("tool_calls", []):
                self._add_collapsible("🔧", f"调用工具: {tc.get('tool', '?')}",
                                      json.dumps(tc.get("args", {}), ensure_ascii=False))
        self._scroll_to_bottom()

    def _add_collapsible(self, icon: str, title: str, detail: str):
        card = CollapsibleCard(icon, title, detail)
        self._scroll_layout.insertWidget(self._scroll_layout.count() - 1, card)
        self._scroll_to_bottom()

    # ── Sending ──

    def _on_send(self):
        # ★ v3移植: 流式输出中点击发送按钮 = 停止
        if self._is_streaming:
            self._stop_streaming()
            return
        text = self._input_text.toPlainText().strip()
        if not text:
            return
        self._input_text.clear()

        if not self._current_session_id:
            s = self._session_manager.create_session()
            self._current_session_id = s.session_id
            self._refresh_session_list()

        # ★ v3移植: 勾选内容引用（仅存摘要，发送时实时读取内容）
        refs_display = self._get_selected_display()
        self._append_message("user", text)
        user_meta = {"refs_display": refs_display} if refs_display else None
        self._session_manager.add_message(self._current_session_id, "user", text,
                                          meta=user_meta)

        # Build system prompt: Skill + 用户系统提示词 + 附加提示词
        skill = self._get_skill_text()
        system_prompt = skill
        if self._sys_prompt.strip():
            system_prompt += "\n\n" + self._sys_prompt.strip()
        if self._add_enabled and self._add_prompt.strip():
            system_prompt += "\n\n" + self._add_prompt.strip()

        # ★ v3移植: 用户消息 = 文本 + 项目上下文数据（大纲树/角色集/设定集/伏笔/勾选内容）
        selected = self._gather_selected_content()
        context_parts = []
        outline_ctx = self._get_outline_tree_context()
        if outline_ctx:
            context_parts.append(f"【大纲树】\n{outline_ctx}")
        char_ctx = self._get_character_summary_context()
        if char_ctx:
            context_parts.append(f"【角色集】\n{char_ctx}")
        settings_ctx = self._get_settings_summary_context()
        if settings_ctx:
            context_parts.append(f"【设定集】\n{settings_ctx}")
        foreshadow_ctx = self._get_foreshadow_context()
        if foreshadow_ctx:
            context_parts.append(foreshadow_ctx)
        if selected:
            context_parts.append(f"【勾选内容】\n{selected}")

        full_text = text
        if context_parts:
            full_text += "\n\n---\n" + "\n\n---\n".join(context_parts)

        # History messages
        history = self._session_manager.get_message_history(self._current_session_id, 50)
        history = [m for m in history if m.get("role") not in ("tool",)][:-1]
        messages = [ChatMessage(m["role"], m["content"]) for m in history]
        messages = self._trim_history(messages)
        messages.append(ChatMessage("user", full_text))

        self._response_buffer = ""
        self._is_streaming = True
        self._current_reasoning = ""
        self._pending_tool_calls = []
        self._streaming_bubble = None
        self._reasoning_window = None
        self._send_btn.setText("⏹ 停止")
        self._send_btn.setStyleSheet("background:#d32f2f; color:white; padding:8px 16px;")

        use_tools = self._tool_check.isChecked() and self._tool_registry is not None
        max_rounds = 5
        if self._config_manager:
            max_rounds = self._config_manager.load_app_config().max_tool_rounds or 5

        def stream_thread():
            try:
                if use_tools:
                    for chunk in self._ai_client.chat_with_tools(messages, self._tool_registry, system_prompt, max_rounds):
                        # ★ v5修复: 真正消费生成器产出的事件，避免 error 等事件被丢弃
                        if not isinstance(chunk, dict):
                            continue
                        ctype = chunk.get("type")
                        if ctype == "tool_call":
                            self._event_bus.publish(
                                "ai:tool_call",
                                {"tool": chunk.get("tool", "?"), "args": chunk.get("args", {}),
                                 "hallucinated": bool(chunk.get("hallucinated", False))},
                                "ChatPanel",
                            )
                        elif ctype == "tool_result":
                            self._event_bus.publish(
                                "ai:tool_result",
                                {"tool": chunk.get("tool", "?"), "result": chunk.get("result", "")},
                                "ChatPanel",
                            )
                        elif ctype == "error":
                            # AIClient 内部在 yield error 前已发布 ai:response_end（超过最大轮数时），
                            # 这里不再重复补发，否则会触发两次 _do_response_end、
                            # 导致重复写入两条助手消息（"两次消息"问题）。
                            pass
                        # chunk/ hallucination_detected 事件已在 AIClient 内部发布，无需重复处理
                else:
                    for chunk in self._ai_client.chat_stream(messages, system_prompt):
                        pass
            except AIClientError as e:
                self._event_bus.publish("ai:response_error", {"error": str(e)}, "ChatPanel")
            except Exception as e:
                self._event_bus.publish("ai:response_error", {"error": str(e)}, "ChatPanel")
            finally:
                self._is_streaming = False
                # ★ v4修复: 通过信号回主线程复位按钮，避免在 AI 线程直接操作控件
                self.stream_finished.emit()

        threading.Thread(target=stream_thread, daemon=True).start()

    def _get_skill_text(self):
        try:
            if self._config_manager:
                cfg = self._config_manager.load_app_config()
                if getattr(cfg, 'chat_skill_text', '').strip():
                    return cfg.chat_skill_text
        except Exception:
            pass
        return self.SKILL

    def _trim_history(self, messages: list, keep: int = 10) -> list:
        if len(messages) <= keep * 2:
            return messages
        return messages[-(keep * 2):]

    # ★ v3补齐: token 估算
    def _estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数（中文≈1字1token，其他字符≈3字1token）"""
        chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other = len(text) - chinese
        return chinese + int(other / 3)

    def _update_token_estimate(self):
        """实时更新输入框 token 估算"""
        text = self._input_text.toPlainText()
        n = self._estimate_tokens(text)
        self._token_label.setText(f"~{n}tk" if n > 0 else "")

    # ── ★ v3移植: 内容勾选面板 ──

    def _toggle_content_panel(self):
        """显示/隐藏内容勾选面板"""
        if self._content_panel_visible:
            self._content_panel.hide()
        else:
            self._content_panel.show()
            self._refresh_content_tree(keep_state=True)
        self._content_panel_visible = not self._content_panel_visible

    def _refresh_content_tree(self, keep_state: bool = False):
        """重建内容勾选树（大纲节点 + 设定文档）"""
        saved_state = dict(self._content_selected) if keep_state else {}
        self._content_tree.blockSignals(True)
        self._content_tree.clear()
        self._content_selected.clear()

        if not self._project_service.get_current_project():
            self._content_tree.blockSignals(False)
            self._update_content_count()
            return

        # 大纲节点（树形）
        nodes = self._project_service.get_outline_tree()
        roots = [n for n in nodes if n.parent_id is None]
        for root in sorted(roots, key=lambda n: n.order):
            self._insert_content_node(None, root, nodes, "📗", saved_state)

        # 设定分类
        for cat in self._project_service.list_categories():
            cat_item = QTreeWidgetItem([f"📁 {cat}"])
            self._content_tree.addTopLevelItem(cat_item)
            for doc in self._project_service.list_docs(cat):
                key = f"setting:{cat}/{doc}"
                checked = saved_state.get(key, False)
                self._content_selected[key] = checked
                doc_item = QTreeWidgetItem([f"📄 {doc}"])
                doc_item.setData(0, Qt.ItemDataRole.UserRole, key)
                doc_item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                cat_item.addChild(doc_item)

        self._content_tree.blockSignals(False)
        self._update_content_count()

    def _insert_content_node(self, parent_item, node, all_nodes, icon: str,
                             saved_state: dict | None = None):
        """递归插入大纲节点到勾选树"""
        key = f"outline:{node.node_id}"
        checked = (saved_state or {}).get(key, False)
        self._content_selected[key] = checked
        item = QTreeWidgetItem([f"{icon} {node.title}"])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        if parent_item is None:
            self._content_tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)
        for cid in node.children_ids:
            child = next((n for n in all_nodes if n.node_id == cid), None)
            if child:
                self._insert_content_node(item, child, all_nodes, "📄", saved_state)

    def _on_content_item_changed(self, item: QTreeWidgetItem, column: int):
        """勾选状态变更 → 同步 dict + 显示前缀"""
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not key or self._content_updating:
            return
        checked = item.checkState(0) == Qt.CheckState.Checked
        self._content_selected[key] = checked
        self._content_updating = True
        try:
            self._sync_item_prefix(item, key)
        finally:
            self._content_updating = False
        self._update_content_count()

    def _sync_item_prefix(self, item: QTreeWidgetItem, key: str):
        """勾选状态由原生复选框显示，无需额外前缀"""
        return

    def _update_content_count(self):
        count = sum(1 for v in self._content_selected.values() if v)
        self._content_count_label.setText(f"已选: {count}")
        # 更新勾选按钮摘要文本
        if count == 0:
            self._content_summary_btn.setText("📎 勾选")
        else:
            names = []
            for key, checked in self._content_selected.items():
                if not checked:
                    continue
                if key.startswith("outline:"):
                    names.append("大纲·" + key.split(":", 1)[1][:8])
                elif key.startswith("setting:"):
                    names.append("设定·" + key.split(":", 1)[1].split("/")[-1][:12])
            summary = ", ".join(names[:3])
            if len(names) > 3:
                summary += f" 等{len(names)}项"
            self._content_summary_btn.setText(f"📎 {summary}")

    def _select_all_content(self):
        self._walk_set_all_checkstate(Qt.CheckState.Checked)

    def _deselect_all_content(self):
        self._walk_set_all_checkstate(Qt.CheckState.Unchecked)

    def _walk_set_all_checkstate(self, state: Qt.CheckState):
        """批量设置所有勾选项（信号屏蔽 + 直接同步 dict）"""
        self._content_updating = True
        try:
            def walk(item: QTreeWidgetItem):
                key = item.data(0, Qt.ItemDataRole.UserRole)
                if key:
                    self._content_selected[key] = (state == Qt.CheckState.Checked)
                    item.setCheckState(0, state)
                    self._sync_item_prefix(item, key)
                for i in range(item.childCount()):
                    walk(item.child(i))
            for i in range(self._content_tree.topLevelItemCount()):
                walk(self._content_tree.topLevelItem(i))
        finally:
            self._content_updating = False
        self._update_content_count()

    def _gather_selected_content(self) -> str:
        """收集所有勾选的内容，拼接为一段上下文（发送时实时读取）"""
        parts = []
        for key, checked in self._content_selected.items():
            if not checked:
                continue
            if key.startswith("outline:"):
                node_id = key.replace("outline:", "")
                node = self._project_service.get_node(node_id)
                if node and node.content:
                    parts.append(f"【{node.title}】\n{node.content}")
            elif key.startswith("setting:"):
                cat_doc = key.replace("setting:", "")
                cat, doc = cat_doc.split("/", 1)
                text = self._project_service.get_setting(cat, doc)
                if text:
                    parts.append(f"【设定: {cat}/{doc}】\n{text}")
        return "\n\n---\n\n".join(parts)

    def _get_selected_display(self) -> str:
        """获取勾选项的显示摘要（仅标题，不读内容）"""
        names = []
        for key, checked in self._content_selected.items():
            if not checked:
                continue
            if key.startswith("outline:"):
                node_id = key.replace("outline:", "")
                node = self._project_service.get_node(node_id)
                if node:
                    names.append(f"大纲·{node.title}")
            elif key.startswith("setting:"):
                cat_doc = key.replace("setting:", "")
                names.append(f"设定·{cat_doc}")
        return ", ".join(names)

    # ── ★ v3移植: 项目上下文组装（发送时拼接） ──

    def _get_outline_tree_context(self) -> str:
        """大纲树上下文 — Linux 树形结构 + 节点ID + 特别标注"""
        try:
            ps = self._project_service
            if not ps or not ps.get_current_project():
                return ""
            nodes = ps.get_outline_tree()
            if not nodes:
                return ""
            children_map: dict[str, list] = {}
            roots: list = []
            for n in nodes:
                if n.parent_id is None:
                    roots.append(n)
                else:
                    children_map.setdefault(n.parent_id, []).append(n)
            for v in children_map.values():
                v.sort(key=lambda x: x.order)

            lines = []
            lines.append("▼ 这是项目大纲的树形结构，每行末尾带 (id=xxx)，可直接用 fetch 工具读取对应 id 的文档，无需额外调用 list_outline")

            def _walk(node, prefix: str, is_last: bool):
                connector = "└── " if is_last else "├── "
                level_name = {1: "L1", 2: "L2", 3: "L3", 4: "L4", 5: "L5"}.get(node.level.value, "")
                status_icon = {"completed": "✓", "in_progress": "●", "todo": "○", "ignored": "⊘"}.get(node.status.value, "○")
                lines.append(f"{prefix}{connector}{status_icon} {node.title} [{level_name}] (id={node.node_id})")
                kids = children_map.get(node.node_id, [])
                child_prefix = prefix + ("    " if is_last else "│   ")
                for i, kid in enumerate(kids):
                    _walk(kid, child_prefix, i == len(kids) - 1)

            for i, root in enumerate(sorted(roots, key=lambda x: x.order)):
                _walk(root, "", i == len(roots) - 1)
            return "\n".join(lines)
        except Exception:
            return ""

    def _get_character_summary_context(self) -> str:
        """角色集上下文 — 姓名+ID+性别+阵营，不含简介"""
        try:
            cs = self._project_service.character_service
            chars = cs.list_characters()
            if not chars:
                return ""
            lines = ["▼ 角色列表，每行带 char_id，可直接用 fetch 工具读取对应角色的完整信息"]
            for c in chars:
                camp_names = []
                for cid in c.camp_ids:
                    camp = cs.get_camp(cid)
                    if camp:
                        camp_names.append(camp.name)
                camps_str = f" [阵营: {', '.join(camp_names)}]" if camp_names else ""
                gender_str = f" {c.gender}" if c.gender else ""
                lines.append(f"- {c.name}{gender_str}{camps_str} (char_id={c.char_id})")
            return "\n".join(lines)
        except Exception:
            return ""

    def _get_settings_summary_context(self) -> str:
        """设定集上下文 — 分类名+文档名，标注可直接用 fetch 读取"""
        try:
            ps = self._project_service
            cats = ps.list_categories()
            if not cats:
                return ""
            lines = ["▼ 设定文档列表（category + doc 名），可直接用 fetch(target='setting', category=..., ids=[...]) 读取"]
            for cat in cats:
                docs = ps.list_docs(cat)
                if docs:
                    lines.append(f"## {cat}")
                    for d in docs:
                        lines.append(f"- {d} (category={cat})")
            return "\n".join(lines) if len(lines) > 1 else ""
        except Exception:
            return ""

    def _get_foreshadow_context(self) -> str:
        """伏笔上下文 — 未隐藏伏笔条目，含 ID，可直接用 fetch 读取"""
        try:
            fs = self._project_service.foreshadow_service
            items = fs.list_foreshadows(include_hidden=False)
            if not items:
                return ""
            lines = ["▼ 当前未隐藏伏笔条目，每行带 id，可直接用 fetch 读取"]
            for i, f in enumerate(items, 1):
                lines.append(f"{i}. {f.content} (id={f.foreshadow_id})")
            return "\n".join(lines)
        except Exception:
            return ""

    # ── ★ v3移植: 消息区基础操作（右键菜单） ──

    def _show_msg_menu(self, pos):
        """消息区右键菜单：复制全文 / 重试"""
        menu = QMenu(self)
        act_copy = menu.addAction("📋 复制全文")
        act_copy.triggered.connect(self._copy_all_messages)
        act_retry = menu.addAction("🔄 重试")
        act_retry.triggered.connect(self._retry_last)
        menu.exec(self._scroll_area.viewport().mapToGlobal(pos))

    def _copy_all_messages(self):
        """复制消息区全部内容到剪贴板（静默，与 v2 行为一致）"""
        lines = []
        role_names = {"user": "👤 你", "assistant": "🤖 AI", "system": "⚙ 系统"}
        for m in self._msg_data:
            role = role_names.get(m.get("role", ""), m.get("role", ""))
            lines.append(f"{role}: {m.get('content', '')}")
        text = "\n\n".join(lines)
        if text:
            QApplication.clipboard().setText(text)

    def _retry_last(self):
        """重试：取出最后一条用户消息重新发送"""
        if self._is_streaming or not self._current_session_id:
            return
        history = self._session_manager.get_message_history(self._current_session_id, 100)
        last_user = ""
        for m in reversed(history):
            if m.get("role") == "user":
                last_user = m["content"]
                break
        if last_user:
            self._input_text.setPlainText(last_user)
            self._on_send()

    def _stop_streaming(self):
        """停止当前 AI 流式输出"""
        try:
            if hasattr(self._ai_client, "cancel"):
                self._ai_client.cancel()
        except Exception:
            pass
        self._is_streaming = False
        self._send_btn.setText("发送")
        self._send_btn.setStyleSheet("background:#0078d4; color:white; padding:8px 16px; font-weight:bold;")
        self._add_system_bubble("⏹ 已停止生成")

    # ── Streaming handlers (called from daemon thread → use signals) ──

    def _on_chunk(self, event):
        self.chunk_received.emit(event.data.get("text", ""))

    def _do_insert_chunk(self, text: str):
        self._response_buffer += text
        if self._streaming_bubble is None:
            self._append_message("assistant", "", streamable=True, add_to_data=True)
        if self._streaming_bubble:
            self._streaming_bubble.append_stream(text)
        self._scroll_to_bottom()

    def _on_response_end_evt(self, event):
        self.response_ended.emit(event.data)

    def _do_response_end(self, data: dict):
        full = data.get("full_text", self._response_buffer)
        if self._streaming_bubble:
            self._streaming_bubble.set_content(full)
        # Update msg_data
        for m in reversed(self._msg_data):
            if m.get("role") == "assistant":
                m["content"] = full
                break
        if self._current_session_id:
            meta = {}
            reasoning = data.get("reasoning") or self._current_reasoning
            if reasoning:
                meta["reasoning"] = reasoning
            if self._pending_tool_calls:
                meta["tool_calls"] = self._pending_tool_calls
            self._session_manager.add_message(self._current_session_id, "assistant", full,
                                              meta=meta if meta else None)

    def _on_response_error_evt(self, event):
        # ★ v4修复: 仅转发信号（AI 线程），UI 操作在 _do_response_error（主线程）
        self.response_error_sig.emit(event.data)

    def _on_tool_call_evt(self, event):
        # ★ v4修复: 仅转发信号（AI 线程），UI 操作在 _do_tool_call（主线程）
        self.tool_call_sig.emit(event.data)

    def _on_tool_result_evt(self, event):
        # ★ v4修复: 仅转发信号（AI 线程），UI 操作在 _do_tool_result（主线程）
        self.tool_result_sig.emit(event.data)

    def _do_response_error(self, data: dict):
        self._add_system_bubble(f"❌ 错误: {data.get('error', '未知')}")

    def _do_tool_call(self, data: dict):
        tool = data.get("tool", "?")
        args = data.get("args", {})
        self._pending_tool_calls.append({"tool": tool, "args": args,
                                         "hallucinated": bool(data.get("hallucinated", False))})
        self._add_collapsible("🔧", f"调用工具: {tool}", json.dumps(args, ensure_ascii=False))

    def _do_tool_result(self, data: dict):
        tool = data.get("tool", "?")
        result = data.get("result", "")
        self._add_collapsible("✅", f"工具结果: {tool}", str(result)[:2000])

    def _do_stream_finished(self):
        self._send_btn.setText("发送")
        self._send_btn.setStyleSheet(
            "background:#0078d4; color:white; padding:8px 16px; font-weight:bold;")

    def _on_reasoning_chunk(self, event):
        # ★ v3修复: 仅转发信号（AI 线程），UI 操作在 _do_reasoning_chunk（主线程）
        self.reasoning_chunk_sig.emit(event.data.get("text", ""))

    def _do_reasoning_chunk(self, text: str):
        self._current_reasoning += text
        if self._reasoning_window is None or not self._reasoning_window.is_alive():
            self._reasoning_window = ReasoningWindow(self)
        self._reasoning_window.append(text)

    def _on_reasoning_end(self, event):
        # ★ v3修复: 仅转发信号（AI 线程），UI 操作在 _do_reasoning_end（主线程）
        self.reasoning_end_sig.emit()

    def _do_reasoning_end(self):
        if self._reasoning_window and self._reasoning_window.is_alive():
            self._reasoning_window.finish()

    # ── Prompt management ──

    def _toggle_prompts(self):
        dlg = dialog_toplevel(self, "提示词", 500, 400)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("系统提示词:"))
        sys_prompt = QTextEdit()
        sys_prompt.setPlaceholderText("输入系统提示词（将拼接在 Skill 文本之后）...")
        sys_prompt.setPlainText(self._sys_prompt)  # ★ v3修复: 预填已保存内容
        lay.addWidget(sys_prompt)
        lay.addWidget(QLabel("附加提示词:"))
        add_prompt = QTextEdit()
        add_prompt.setPlaceholderText("输入附加提示词（可选启用）...")
        add_prompt.setPlainText(self._add_prompt)  # ★ v3修复: 预填已保存内容
        lay.addWidget(add_prompt)
        enable = QCheckBox("启用附加提示词")
        enable.setChecked(self._add_enabled)  # ★ v3修复: 恢复启用状态
        lay.addWidget(enable)
        btn = QHBoxLayout()
        ok = QPushButton("确定")
        cancel = QPushButton("取消")

        def on_ok():
            self._sys_prompt = sys_prompt.toPlainText()
            self._add_prompt = add_prompt.toPlainText()
            self._add_enabled = enable.isChecked()
            self._update_prompt_summary()
            dlg.accept()

        ok.clicked.connect(on_ok)
        cancel.clicked.connect(dlg.reject)
        btn.addWidget(ok)
        btn.addWidget(cancel)
        lay.addLayout(btn)
        dlg.exec()

    def _on_chat_project(self, name):
        if name:
            self._project_service.switch_project(name)

    def _on_project_changed_evt(self, event):
        pass

    def _refresh_chat_projects(self):
        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        for p in self._project_service.list_projects():
            self._project_combo.addItem(p.name)
        cur = self._project_service.get_current_project()
        if cur:
            self._project_combo.setCurrentText(cur)
        self._project_combo.blockSignals(False)
