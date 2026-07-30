"""
功能面板 — ChatPanel / OutlinePanel / CharacterPanel / ForeshadowPanel / SettingsPanel / StatusPanel / ConfigPanel / LogPanel

每个面板封装自己的 UI 逻辑，通过 BasePanel 基类统一生命周期。
★ v2.0: 新增 CharacterPanel / ForeshadowPanel / StatusPanel，修复 LogPanel
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime

from src.core.event_bus import EventBus
from src.core.logger import Logger
from src.core.config_manager import ConfigManager, AISourceConfig
from src.services.ai_client import AIClient, ChatMessage, AIClientError
from src.services.session_manager import SessionManager, Session, SessionMeta
from src.services.project_service import (
    ProjectService, ProjectMeta, OutlineNode, OutlineLevel, NodeStatus,
)


# ==================== 面板基类 ====================

class BasePanel(ABC):
    """面板基类 — 统一生命周期接口"""

    def __init__(self, parent: tk.Frame, event_bus: EventBus, logger: Logger):
        self.frame = tk.Frame(parent, bg="#fafafa")
        self._event_bus = event_bus
        self._logger = logger
        self._setup_ui()
        self._subscribe_events()

    @abstractmethod
    def _setup_ui(self) -> None:
        """构建面板 UI"""

    def _subscribe_events(self) -> None:
        """订阅事件总线（子类按需覆盖）"""

    def on_show(self) -> None:
        """面板被切换到前台时调用"""

    def on_close(self) -> None:
        """面板关闭时调用"""


# ==================== ChatPanel ====================

class ChatPanel(BasePanel):
    """AI 对话面板 — 含提示词选择、内容勾选、工具调用"""

    def __init__(self, parent, event_bus, logger,
                 ai_client: AIClient, session_manager: SessionManager,
                 project_service: ProjectService):
        self._ai_client = ai_client
        self._session_manager = session_manager
        self._project_service = project_service
        from src.core.config_manager import ConfigManager
        self._config_manager: Optional[ConfigManager] = None  # 由 MainWindow 注入
        self._tool_registry = None  # 由 MainWindow 注入
        self._current_session_id: Optional[str] = None
        self._response_buffer = ""
        self._is_streaming = False
        self._ai_prefix_inserted = False    # AI 前缀是否已插入
        # 内容勾选状态: {node_id: (title, content, checked)}
        self._content_checkboxes: dict[str, tk.BooleanVar] = {}
        self._selected_content: list[str] = []  # 选中的完整文本
        super().__init__(parent, event_bus, logger)

    def set_tool_registry(self, registry) -> None:
        self._tool_registry = registry

    def set_config_manager(self, cm: "ConfigManager") -> None:
        self._config_manager = cm

    def _setup_ui(self):
        # ── 工具栏：紧凑布局 ──
        toolbar = tk.Frame(self.frame, bg="#f0f0f0", height=32)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)
        tk.Label(toolbar, text="项目:", font=("Microsoft YaHei", 9), bg="#f0f0f0",
                 ).pack(side="left", padx=(6, 1), pady=4)
        self._chat_project_var = tk.StringVar(value="无项目")
        self._chat_project_menu = ttk.OptionMenu(toolbar, self._chat_project_var, "无项目",
                                                  command=self._on_chat_project)
        self._chat_project_menu.pack(side="left", padx=1)
        tk.Label(toolbar, text="对话:", font=("Microsoft YaHei", 9), bg="#f0f0f0",
                 ).pack(side="left", padx=(8, 1))
        self._session_var = tk.StringVar(value="新对话")
        self._session_menu = ttk.OptionMenu(toolbar, self._session_var, "新对话",
                                            command=self._on_session_selected)
        self._session_menu.pack(side="left", padx=1)
        tk.Button(toolbar, text="+", command=self._new_session,
                  font=("Microsoft YaHei", 9), padx=4, relief="flat",
                  bg="#f0f0f0").pack(side="left", padx=1)
        tk.Button(toolbar, text="🗑", command=self._delete_session,
                  font=("Microsoft YaHei", 9), padx=4, relief="flat",
                  bg="#f0f0f0").pack(side="left", padx=1)
        # 工具开关
        self._tool_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(toolbar, text="🔧工具", variable=self._tool_enabled,
                       font=("Microsoft YaHei", 9), bg="#f0f0f0",
                       relief="flat").pack(side="right", padx=6)

        # ── 提示词折叠栏（默认收起，点展开编辑） ──
        self._prompt_header = tk.Frame(self.frame, bg="#eef2f7", height=30)
        self._prompt_header.pack(fill="x")
        self._prompt_header.pack_propagate(False)

        tk.Button(self._prompt_header, text="📝 提示词", command=self._toggle_prompts,
                  font=("Microsoft YaHei", 9, "bold"), bg="#eef2f7", relief="flat",
                  padx=6).pack(side="left", padx=4)
        self._prompt_summary_var = tk.StringVar(value="无")
        tk.Label(self._prompt_header, textvariable=self._prompt_summary_var,
                 font=("Microsoft YaHei", 9), bg="#eef2f7", fg="#666",
                 anchor="w").pack(side="left", fill="x", expand=True, padx=4)
        tk.Button(self._prompt_header, text="📎勾选", command=self._toggle_content_panel,
                  font=("Microsoft YaHei", 9), padx=6).pack(side="right", padx=4)

        # 提示词编辑面板（默认隐藏）
        self._prompt_panel = tk.Frame(self.frame, bg="#f0f4fa", height=90)
        self._prompts_visible = False

        # 系统提示词行
        sp_row = tk.Frame(self._prompt_panel, bg="#eef2f7")
        sp_row.pack(fill="x", pady=1)
        tk.Label(sp_row, text="系统提示词:", font=("Microsoft YaHei", 9, "bold"),
                 bg="#eef2f7").pack(side="left", padx=4)
        self._sys_prompt_var = tk.StringVar(value="无")
        self._sys_prompt_menu = ttk.OptionMenu(sp_row, self._sys_prompt_var, "无",
                                                command=self._on_sys_prompt_selected)
        self._sys_prompt_menu.pack(side="left", padx=2)
        tk.Button(sp_row, text="💾", command=self._save_sys_prompt,
                  font=("Microsoft YaHei", 8), padx=3).pack(side="left", padx=2)
        self._sys_prompt_text = tk.Text(sp_row, height=2, font=("Microsoft YaHei", 9),
                                         bg="#ffffff", borderwidth=1, relief="solid")
        self._sys_prompt_text.pack(side="left", fill="x", expand=True, padx=4, pady=2)

        # 附加提示词行
        ap_row = tk.Frame(self._prompt_panel, bg="#f5f0e8")
        ap_row.pack(fill="x", pady=1)
        tk.Label(ap_row, text="附加提示词:", font=("Microsoft YaHei", 9),
                 bg="#f5f0e8").pack(side="left", padx=4)
        self._add_prompt_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(ap_row, text="启用", variable=self._add_prompt_enabled,
                        font=("Microsoft YaHei", 9), bg="#f5f0e8").pack(side="left", padx=2)
        self._add_prompt_var = tk.StringVar(value="无")
        self._add_prompt_menu = ttk.OptionMenu(ap_row, self._add_prompt_var, "无",
                                                command=self._on_add_prompt_selected)
        self._add_prompt_menu.pack(side="left", padx=2)
        tk.Button(ap_row, text="💾", command=self._save_add_prompt,
                  font=("Microsoft YaHei", 8), padx=3).pack(side="left", padx=2)
        self._add_prompt_text = tk.Text(ap_row, height=2, font=("Microsoft YaHei", 9),
                                         bg="#ffffff", borderwidth=1, relief="solid")
        self._add_prompt_text.pack(side="left", fill="x", expand=True, padx=4, pady=2)

        # ── 内容勾选面板（默认隐藏） ──
        self._content_panel = tk.Frame(self.frame, bg="#fafafa", height=160)

        # 已选内容摘要栏
        self._content_summary_var = tk.StringVar(value="📎 未勾选任何内容")
        summary_bar = tk.Frame(self._content_panel, bg="#e8f0fe", height=28)
        summary_bar.pack(fill="x", side="top")
        summary_bar.pack_propagate(False)
        tk.Label(summary_bar, textvariable=self._content_summary_var,
                 font=("Microsoft YaHei", 9), bg="#e8f0fe", fg="#1565c0",
                 anchor="w").pack(fill="x", padx=8, pady=4)

        self._content_tree = ttk.Treeview(self._content_panel, show="tree",
                                          selectmode="browse")
        self._content_tree.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        self._content_tree.bind("<Button-1>", self._on_content_toggle)
        self._content_panel_visible = False

        # 勾选操作按钮
        content_btn = tk.Frame(self._content_panel, bg="#fafafa")
        content_btn.pack(fill="x", side="bottom", padx=4, pady=2)
        tk.Button(content_btn, text="全选", command=self._select_all_content,
                  font=("Microsoft YaHei", 8), padx=4).pack(side="left", padx=2)
        tk.Button(content_btn, text="全不选", command=self._deselect_all_content,
                  font=("Microsoft YaHei", 8), padx=4).pack(side="left", padx=2)
        self._content_count_label = tk.Label(content_btn, text="已选: 0",
                                             font=("Microsoft YaHei", 8), bg="#fafafa")
        self._content_count_label.pack(side="right", padx=6)

        # ── 消息区 ──
        msg_frame = tk.Frame(self.frame, bg="#ffffff")
        msg_frame.pack(fill="both", expand=True)

        self._msg_text = tk.Text(msg_frame, wrap="word", state="disabled",
                                 font=("Microsoft YaHei", 11), bg="#ffffff",
                                 fg="#333333", padx=16, pady=8, borderwidth=0)
        self._msg_text.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(msg_frame, command=self._msg_text.yview)
        scrollbar.pack(side="right", fill="y")
        self._msg_text.config(yscrollcommand=scrollbar.set)

        # 消息区右键菜单
        self._msg_menu = tk.Menu(self.frame, tearoff=0)
        self._msg_menu.add_command(label="📋 复制全文", command=self._copy_all_messages)
        self._msg_menu.add_command(label="🔄 重试", command=self._retry_last)
        self._msg_text.bind("<Button-3>", lambda e: self._msg_menu.post(e.x_root, e.y_root))

        # 占位提示（无消息时显示）
        self._msg_placeholder = tk.Label(
            msg_frame, text="💬 暂无消息\n在下方输入框开始对话",
            font=("Microsoft YaHei", 12), bg="#ffffff", fg="#bbbbbb", justify="center"
        )
        self._msg_placeholder.place(relx=0.5, rely=0.5, anchor="center")

        # ── 输入区 ──
        input_frame = tk.Frame(self.frame, bg="#f5f5f5", height=80)
        input_frame.pack(fill="x", side="bottom")
        input_frame.pack_propagate(False)

        self._input_text = tk.Text(input_frame, wrap="word", height=3,
                                   font=("Microsoft YaHei", 11), borderwidth=1, relief="solid")
        self._input_text.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        self._input_text.bind("<Control-Return>", self._on_send)

        self._send_btn = tk.Button(input_frame, text="发送", command=self._on_send,
                                   font=("Microsoft YaHei", 11, "bold"), bg="#0078d4",
                                   fg="#ffffff", padx=16, pady=4, relief="flat")
        self._send_btn.pack(side="right", padx=8, pady=8)
        self._token_label = tk.Label(input_frame, text="", font=("Microsoft YaHei", 8),
                                     fg="#999", bg=self.frame["bg"])
        self._token_label.pack(side="right", padx=(0, 4))
        # 绑定输入事件 → 实时估算 token
        self._input_text.bind("<KeyRelease>", lambda e: self._update_token_estimate(), add="+")

        # 初始显示占位提示
        self._show_placeholder()

    def _subscribe_events(self):
        self._event_bus.subscribe("ai:response_chunk", self._on_chunk)
        self._event_bus.subscribe("ai:response_end", self._on_response_end)
        self._event_bus.subscribe("ai:response_error", self._on_response_error)
        self._event_bus.subscribe("ai:tool_call", self._on_tool_call)
        self._event_bus.subscribe("ai:tool_result", self._on_tool_result)
        self._event_bus.subscribe("project:switched", self._on_project_changed)

    def on_show(self):
        self._refresh_session_list()
        self._refresh_prompt_list()
        self._refresh_chat_projects()
        self._refresh_content_tree()
        # 注入完成后更新工具开关状态
        if self._config_manager:
            cfg = self._config_manager.load_app_config()
            self._tool_enabled.set(cfg.tool_enabled)
        # 强制刷新项目显示
        self._sync_project_display()

    # ── 提示词管理 ──

    def _refresh_prompt_list(self):
        if not self._config_manager:
            return
        # 刷新系统提示词下拉框
        sys_menu = self._sys_prompt_menu["menu"]
        sys_menu.delete(0, "end")
        sys_menu.add_command(label="无", command=lambda: self._on_sys_prompt_selected("无"))
        for p in self._config_manager.list_prompts("system"):
            sys_menu.add_command(label=p["name"],
                                 command=lambda n=p["name"]: self._on_sys_prompt_selected(n))
        # 刷新附加提示词下拉框
        add_menu = self._add_prompt_menu["menu"]
        add_menu.delete(0, "end")
        add_menu.add_command(label="无", command=lambda: self._on_add_prompt_selected("无"))
        for p in self._config_manager.list_prompts("additional"):
            add_menu.add_command(label=p["name"],
                                 command=lambda n=p["name"]: self._on_add_prompt_selected(n))

    def _on_sys_prompt_selected(self, name: str):
        """选中系统提示词 → 填入系统提示词文本框"""
        self._sys_prompt_var.set(name)
        self._sys_prompt_text.delete("1.0", "end")
        if name == "无":
            return
        if self._config_manager:
            for p in self._config_manager.list_prompts("system"):
                if p["name"] == name:
                    self._sys_prompt_text.insert("1.0", p["content"])
                    return

    def _on_add_prompt_selected(self, name: str):
        """选中附加提示词 → 填入附加提示词文本框，自动勾选启用"""
        self._add_prompt_var.set(name)
        self._add_prompt_text.delete("1.0", "end")
        if name == "无":
            return
        self._add_prompt_enabled.set(True)
        if self._config_manager:
            for p in self._config_manager.list_prompts("additional"):
                if p["name"] == name:
                    self._add_prompt_text.insert("1.0", p["content"])
                    return

    def _save_sys_prompt(self):
        """保存当前系统提示词"""
        name = self._sys_prompt_var.get()
        content = self._sys_prompt_text.get("1.0", "end-1c").strip()
        if not content:
            return
        if name in ("无", ""):
            from tkinter import simpledialog
            name = simpledialog.askstring("保存提示词", "输入系统提示词名称:", parent=self.frame)
            if not name:
                return
        if self._config_manager:
            self._config_manager.save_prompt(name, content, "system")
            self._sys_prompt_var.set(name)
            self._refresh_prompt_list()

    def _save_add_prompt(self):
        """保存当前附加提示词"""
        name = self._add_prompt_var.get()
        content = self._add_prompt_text.get("1.0", "end-1c").strip()
        if not content:
            return
        if name in ("无", ""):
            from tkinter import simpledialog
            name = simpledialog.askstring("保存提示词", "输入附加提示词名称:", parent=self.frame)
            if not name:
                return
        if self._config_manager:
            self._config_manager.save_prompt(name, content, "additional")
            self._add_prompt_var.set(name)
            self._refresh_prompt_list()

    # ── 内容勾选 ──

    def _toggle_content_panel(self):
        if self._content_panel_visible:
            self._content_panel.pack_forget()
        else:
            self._content_panel.pack(fill="x", before=self._msg_text.master)
            self._refresh_content_tree(keep_state=True)
        self._content_panel_visible = not self._content_panel_visible

    def _refresh_content_tree(self, keep_state: bool = False):
        # 保留已有勾选状态
        saved_state = {}
        if keep_state:
            for key, var in self._content_checkboxes.items():
                saved_state[key] = var.get()

        self._content_tree.delete(*self._content_tree.get_children())
        self._content_checkboxes.clear()
        if not self._project_service.get_current_project():
            return

        # 大纲节点
        nodes = self._project_service.get_outline_tree()
        roots = [n for n in nodes if n.parent_id is None]
        for root in sorted(roots, key=lambda n: n.order):
            self._insert_content_node("", root, nodes, "📗", saved_state)

        # 设定分类
        for cat in self._project_service.list_categories():
            cat_iid = self._content_tree.insert("", "end", text=f"📁 {cat}", open=False)
            for doc in self._project_service.list_docs(cat):
                key = f"setting:{cat}/{doc}"
                var = tk.BooleanVar(value=saved_state.get(key, False))
                self._content_checkboxes[key] = var
                self._content_tree.insert(cat_iid, "end", text=f"☐ {doc}",
                                          tags=(key,), open=False)

        # 恢复勾选状态下的节点显示
        if keep_state:
            self._refresh_content_tree_display()
        self._update_content_count()

    def _insert_content_node(self, parent_iid: str, node, all_nodes, icon: str,
                              saved_state: dict | None = None):
        key = f"outline:{node.node_id}"
        var = tk.BooleanVar(value=(saved_state or {}).get(key, False))
        self._content_checkboxes[key] = var
        iid = self._content_tree.insert(parent_iid, "end", text=f"☐ {icon} {node.title}",
                                        tags=(key,), open=False)
        for cid in node.children_ids:
            child = next((n for n in all_nodes if n.node_id == cid), None)
            if child:
                self._insert_content_node(iid, child, all_nodes, "📄", saved_state)

    def _on_content_toggle(self, event):
        """点击树节点切换勾选状态"""
        iid = self._content_tree.identify_row(event.y)
        if not iid:
            return
        tags = self._content_tree.item(iid, "tags")
        if not tags:
            return
        key = tags[0]
        if key in self._content_checkboxes:
            var = self._content_checkboxes[key]
            var.set(not var.get())
            self._update_content_node_display(iid, key, var.get())
            self._update_content_count()

    def _update_content_node_display(self, iid, key, checked):
        """更新节点的显示文字（勾选标记）"""
        text = self._content_tree.item(iid, "text")
        # 移除现有的"☑ "或"☐ "前缀
        for prefix in ("☑ ", "☐ "):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        # 根据 checked 状态加上新前缀
        new_prefix = "☑ " if checked else "☐ "
        self._content_tree.item(iid, text=new_prefix + text)

    def _update_content_count(self):
        count = sum(1 for v in self._content_checkboxes.values() if v.get())
        self._content_count_label.config(text=f"已选: {count}")
        # 更新摘要栏
        if count == 0:
            self._content_summary_var.set("📎 未勾选任何内容")
        else:
            names = []
            for key, var in self._content_checkboxes.items():
                if var.get():
                    if key.startswith("outline:"):
                        names.append("大纲·" + key.split(":", 1)[1][:8])
                    elif key.startswith("setting:"):
                        names.append("设定·" + key.split(":", 1)[1].split("/")[-1][:12])
            summary = "📎 " + ", ".join(names[:5])
            if len(names) > 5:
                summary += f" 等{len(names)}项"
            self._content_summary_var.set(summary)

    def _select_all_content(self):
        for var in self._content_checkboxes.values():
            var.set(True)
        self._refresh_content_tree_display()

    def _deselect_all_content(self):
        for var in self._content_checkboxes.values():
            var.set(False)
        self._refresh_content_tree_display()

    def _refresh_content_tree_display(self):
        """根据 BooleanVar 刷新所有节点显示"""
        for iid in self._content_tree.get_children():
            self._walk_update_display(iid)
        self._update_content_count()

    def _walk_update_display(self, iid):
        tags = self._content_tree.item(iid, "tags")
        if tags and tags[0] in self._content_checkboxes:
            checked = self._content_checkboxes[tags[0]].get()
            self._update_content_node_display(iid, tags[0], checked)
        for child in self._content_tree.get_children(iid):
            self._walk_update_display(child)

    def _gather_selected_content(self) -> str:
        """收集所有勾选的内容，拼接为一段上下文（发送时实时读取）"""
        parts = []
        for key, var in self._content_checkboxes.items():
            if not var.get():
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
        for key, var in self._content_checkboxes.items():
            if not var.get():
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

    # ── 发送与对话 ──

    def _estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数（中文≈1字1token，英文≈0.75词1token）"""
        chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other = len(text) - chinese
        return chinese + int(other / 3)

    def _update_token_estimate(self):
        """实时更新输入框 token 估算"""
        text = self._input_text.get("1.0", "end-1c")
        n = self._estimate_tokens(text)
        self._token_label.config(text=f"~{n}tk" if n > 0 else "")

    def _trim_history(self, messages: list, keep: int = 10) -> list:
        """保留最近 keep 轮对话，旧历史合并为一条系统摘要注入"""
        if len(messages) <= keep:
            return messages
        old = messages[:-keep]
        recent = messages[-keep:]
        summary_parts = []
        for m in old:
            role = "用户" if m.role == "user" else "AI"
            snippet = m.content[:200].replace("\n", " ")
            summary_parts.append(f"[{role}]: {snippet}...")
        summary = "【历史对话摘要】\n" + "\n".join(summary_parts)
        return [ChatMessage("system", summary)] + recent

    # ★ v2.0: 状态和伏笔上下文采集
    def _get_status_context(self) -> str:
        """从 StatusPanel 获取当前创作状态"""
        try:
            if hasattr(self, '_project_service') and self._project_service:
                # 基本状态（不依赖 StatusPanel 实例）
                ps = self._project_service
                if ps.get_current_project():
                    tree = ps.get_outline_tree()
                    total = len(tree)
                    completed = sum(1 for n in tree if n.status.value == "completed")
                    l5_count = len([n for n in tree if n.level.value == 5])
                    total_words = sum(n.word_count for n in tree if n.level.value == 5)
                    return (
                        f"【当前创作状态】\n"
                        f"项目: {ps.get_current_project()}\n"
                        f"大纲节点: {total} (已完成 {completed})\n"
                        f"正文章节: {l5_count}\n"
                        f"总字数: {total_words:,}"
                    )
        except Exception:
            pass
        return ""

    def _get_foreshadow_context(self) -> str:
        """从 ForeshadowService 获取未隐藏的伏笔"""
        try:
            if hasattr(self, '_project_service') and self._project_service:
                return self._project_service.foreshadow_service.get_ai_context()
        except Exception:
            pass
        return ""

    def _toggle_prompts(self):
        """展开/收起提示词编辑面板"""
        if self._prompts_visible:
            self._prompt_panel.pack_forget()
            self._prompts_visible = False
        else:
            self._prompt_panel.pack(fill="x", after=self._prompt_header)
            self._prompts_visible = True
            # 刷新摘要
            sp = self._sys_prompt_text.get("1.0", "end-1c").strip()
            self._prompt_summary_var.set(sp[:50] + ("…" if len(sp) > 50 else "") if sp else "无")

    def _stop_streaming(self):
        """停止当前 AI 流式输出"""
        self._ai_client.cancel() if hasattr(self._ai_client, "cancel") else None
        self._is_streaming = False
        self._send_btn.config(text="发送", bg="#0078d4", command=self._on_send)
        self._append_message("system", "⏹ 已停止生成")

    def _on_send(self, event=None):
        text = self._input_text.get("1.0", "end-1c").strip()
        if not text or self._is_streaming:
            return "break"
        self._input_text.delete("1.0", "end")

        if not self._current_session_id:
            s = self._session_manager.create_session()
            self._current_session_id = s.session_id
            self._refresh_session_list()

        # 勾选内容：仅存引用（显示摘要），发送时实时读取
        refs_display = self._get_selected_display()
        self._append_message("user", text)
        user_meta = {"refs_display": refs_display} if refs_display else None
        self._session_manager.add_message(
            self._current_session_id, "user", text, meta=user_meta)

        # ★ v2.0: 按新顺序拼接上下文
        # 1. 获取状态信息 (StatusPanel)
        status_context = self._get_status_context()
        # 2. 获取伏笔信息 (ForeshadowService, 仅 unhidden)
        foreshadow_context = self._get_foreshadow_context()
        # 3. 获取勾选内容（原有逻辑）
        selected = self._gather_selected_content()

        # 组装: 消息 → 状态 → 伏笔 → 勾选内容
        full_text = text
        if status_context:
            full_text += f"\n\n---\n{status_context}"
        if foreshadow_context:
            full_text += f"\n\n---\n{foreshadow_context}"
        if selected:
            full_text += f"\n\n---\n【以下为选中的已有内容供参考】\n{selected}"

        messages = [ChatMessage("user", full_text)]
        # 添加历史（排除 tool 消息 + 旧历史裁剪）
        history = [
            m for m in self._session_manager.get_message_history(self._current_session_id, 50)
            if m.get("role") not in ("tool",)
        ][:-1]  # 排除当前 user 消息（已单独构建 full_text）
        history_msgs = [
            ChatMessage(m["role"], m["content"])
            for m in history
        ]
        messages = self._trim_history(history_msgs) + messages

        # 组合系统提示词 + 附加提示词（若启用）
        system_prompt = self._sys_prompt_text.get("1.0", "end-1c").strip()
        if self._add_prompt_enabled.get():
            add_prompt = self._add_prompt_text.get("1.0", "end-1c").strip()
            if add_prompt:
                system_prompt = system_prompt + "\n\n" + add_prompt if system_prompt else add_prompt

        self._response_buffer = ""
        self._is_streaming = True
        self._ai_prefix_inserted = False
        self._send_btn.config(text="⏹ 停止", bg="#d32f2f", command=self._stop_streaming)

        use_tools = self._tool_enabled.get() and self._tool_registry is not None

        # 从配置读取最大工具调用轮数
        max_rounds = 5
        if self._config_manager:
            max_rounds = self._config_manager.load_app_config().max_tool_rounds or 5

        import threading
        def stream_thread():
            try:
                if use_tools:
                    for chunk in self._ai_client.chat_with_tools(
                        messages, self._tool_registry, system_prompt, max_rounds):
                        pass
                else:
                    for chunk in self._ai_client.chat_stream(messages, system_prompt):
                        pass
            except AIClientError as e:
                self._event_bus.publish("ai:response_error", {"error": str(e)}, "ChatPanel")
            except Exception as e:
                self._event_bus.publish("ai:response_error", {"error": f"内部错误: {e}"}, "ChatPanel")
            finally:
                self._is_streaming = False
                self.frame.after(0, lambda: self._send_btn.config(
                    text="发送", bg="#0078d4", command=self._on_send))

        threading.Thread(target=stream_thread, daemon=True).start()

    def _on_chunk(self, event):
        self._response_buffer += event.data.get("text", "")
        self._msg_text.config(state="normal")
        if not self._ai_prefix_inserted:
            self._msg_text.insert("end", "\n\n🤖 AI: ")
            self._ai_prefix_inserted = True
        self._msg_text.insert("end", event.data.get("text", ""))
        self._msg_text.see("end")
        self._msg_text.config(state="disabled")

    def _on_tool_call(self, event):
        tool = event.data.get("tool", "?")
        args = event.data.get("args", {})
        if tool in ("insert_text_at", "replace_text_at"):
            action = "📝插入" if tool == "insert_text_at" else "✏修改"
            loc = f"行{args.get('line','?')}列{args.get('col','?')}"
            text_preview = str(args.get("text", ""))[:30]
            self._append_message("system", f"{action} {loc}: {text_preview}...")
        else:
            self._append_message("system", f"🔧 AI 调用工具: {tool}({json.dumps(args, ensure_ascii=False)[:80]})")
        if self._current_session_id:
            self._session_manager.add_message(self._current_session_id, "tool",
                json.dumps({"action": "call", "tool": tool, "args": args}, ensure_ascii=False))

    def _on_tool_result(self, event):
        self._append_message("system", f"✅ 工具执行完成: {event.data.get('tool', '?')}")
        if self._current_session_id:
            result = event.data.get("result", "")
            self._session_manager.add_message(self._current_session_id, "tool",
                json.dumps({"action": "result", "tool": event.data.get("tool", ""),
                            "result": str(result)[:500]}, ensure_ascii=False))

    def _on_response_end(self, event):
        if self._current_session_id:
            full = event.data.get("full_text", self._response_buffer)
            self._session_manager.add_message(self._current_session_id, "assistant", full)

    def _on_response_error(self, event):
        self._append_message("system", f"❌ 错误: {event.data.get('error', '未知错误')}")

    def _copy_all_messages(self):
        """复制消息区全文到剪贴板"""
        all_text = self._msg_text.get("1.0", "end-1c")
        self.frame.clipboard_clear()
        self.frame.clipboard_append(all_text)

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
            self._input_text.delete("1.0", "end")
            self._input_text.insert("1.0", last_user)
            self._on_send()

    def _append_message(self, role: str, content: str):
        # 隐藏占位提示
        if hasattr(self, '_msg_placeholder') and self._msg_placeholder.winfo_ismapped():
            self._msg_placeholder.place_forget()
        self._msg_text.config(state="normal")
        prefix = {"user": "👤 你", "assistant": "🤖 AI", "system": "⚙ 系统"}.get(role, role)
        self._msg_text.insert("end", f"\n\n{prefix}: {content}")
        self._msg_text.see("end")
        self._msg_text.config(state="disabled")

    def _new_session(self):
        s = self._session_manager.create_session()
        self._current_session_id = s.session_id
        self._msg_text.config(state="normal")
        self._msg_text.delete("1.0", "end")
        self._msg_text.config(state="disabled")
        self._show_placeholder()
        self._refresh_session_list()

    def _delete_session(self):
        if self._current_session_id:
            self._session_manager.delete_session(self._current_session_id)
            self._current_session_id = None
            self._msg_text.config(state="normal")
            self._msg_text.delete("1.0", "end")
            self._msg_text.config(state="disabled")
            self._show_placeholder()
            self._refresh_session_list()

    def _refresh_session_list(self):
        sessions = self._session_manager.list_sessions()
        menu = self._session_menu["menu"]
        menu.delete(0, "end")
        for s in sessions:
            label = f"{s.title[:20]} ({s.message_count}条)"
            menu.add_command(label=label, command=lambda sid=s.session_id: self._switch_session(sid))
        self._session_var.set("选择对话" if sessions else "新对话")

    def _switch_session(self, session_id: str):
        self._current_session_id = session_id
        s = self._session_manager.get_session(session_id)
        self._msg_text.config(state="normal")
        self._msg_text.delete("1.0", "end")
        if s:
            for m in s.messages:
                self._append_message(m["role"], m["content"])
        self._msg_text.config(state="disabled")
        if not s or not s.messages:
            self._show_placeholder()
        self._session_var.set(s.title[:20] if s else "对话")

    def _show_placeholder(self):
        if hasattr(self, '_msg_placeholder') and not self._msg_placeholder.winfo_ismapped():
            self._msg_placeholder.place(relx=0.5, rely=0.5, anchor="center")

    def _on_session_selected(self, value):
        pass

    def _on_chat_project(self, name: str):
        if name and name != "无项目":
            self._project_service.switch_project(name)

    def _on_project_changed(self, event):
        """项目切换事件 → 更新显示"""
        name = event.data.get("project_name", "")
        self._chat_project_var.set(name or "无项目")
        # 强制刷新菜单选项
        self.frame.after(100, self._refresh_chat_projects)

    def _sync_project_display(self):
        """同步当前项目名到显示"""
        current = self._project_service.get_current_project()
        self._chat_project_var.set(current or "无项目")
        if current:
            self._refresh_chat_projects()

    def _refresh_chat_projects(self):
        menu = self._chat_project_menu["menu"]
        menu.delete(0, "end")
        menu.add_command(label="无项目", command=lambda: self._chat_project_var.set("无项目"))
        for p in self._project_service.list_projects():
            menu.add_command(label=p.name, command=lambda n=p.name: self._on_chat_project(n))
        current = self._project_service.get_current_project()
        self._chat_project_var.set(current or "无项目")


# ==================== OutlinePanel ====================

class OutlinePanel(BasePanel):
    """大纲层级管理面板"""

    def __init__(self, parent, event_bus, logger, project_service: ProjectService):
        self._project_service = project_service
        self._current_node_id: Optional[str] = None
        self._content_modified = False
        self._ai_client = None  # 由 MainWindow 注入
        super().__init__(parent, event_bus, logger)

    def set_ai_client(self, ai_client) -> None:
        """注入 AI 客户端（由 MainWindow 调用）"""
        self._ai_client = ai_client

    def _setup_ui(self):
        # 顶部工具栏
        toolbar = tk.Frame(self.frame, bg="#f0f0f0", height=40)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)

        self._project_var = tk.StringVar(value="无项目")
        self._project_menu = ttk.OptionMenu(toolbar, self._project_var, "无项目",
                                            command=self._switch_project)
        self._project_menu.pack(side="left", padx=4, pady=4)

        tk.Button(toolbar, text="+新项目", command=self._create_project,
                  font=("Microsoft YaHei", 10), padx=6).pack(side="left", pady=4)

        # 内容区：三栏布局
        paned = tk.PanedWindow(self.frame, orient="horizontal", bg="#e0e0e0")
        paned.pack(fill="both", expand=True)

        # 左栏：大纲树
        left_frame = tk.Frame(paned, bg="#fafafa", width=220)
        paned.add(left_frame)

        # 树工具栏
        tree_toolbar = tk.Frame(left_frame, bg="#fafafa")
        tree_toolbar.pack(fill="x", padx=4, pady=(4, 0))
        tk.Label(tree_toolbar, text="大纲树", font=("Microsoft YaHei", 10, "bold"),
                 bg="#fafafa").pack(side="left", padx=(4, 2))
        tk.Button(tree_toolbar, text="📂", command=self._expand_all,
                  font=("Microsoft YaHei", 7), padx=2, relief="flat",
                  bg="#fafafa", activebackground="#e0e0e0").pack(side="right", padx=1)
        tk.Button(tree_toolbar, text="📁", command=self._collapse_all,
                  font=("Microsoft YaHei", 7), padx=2, relief="flat",
                  bg="#fafafa", activebackground="#e0e0e0").pack(side="right", padx=1)
        self._tree_search_var = tk.StringVar()
        self._tree_search_var.trace_add("write", lambda *a: self._filter_tree())
        search_entry = tk.Entry(tree_toolbar, textvariable=self._tree_search_var,
                                font=("Microsoft YaHei", 9), width=10,
                                relief="solid", borderwidth=1)
        search_entry.pack(side="right", padx=2)
        tk.Label(tree_toolbar, text="🔍", font=("Microsoft YaHei", 8),
                 bg="#fafafa").pack(side="right")

        self._tree = ttk.Treeview(left_frame, show="tree", selectmode="browse")
        self._tree.pack(fill="both", expand=True, padx=4, pady=4)
        self._tree.bind("<<TreeviewSelect>>", self._on_node_selected)
        self._tree.bind("<Double-1>", self._on_node_rename)
        self._tree.bind("<Button-3>", self._on_tree_right_click)

        # 右键菜单
        self._tree_menu = tk.Menu(self.frame, tearoff=0)
        self._tree_menu.add_command(label="更改父节点", command=self._change_parent)
        self._tree_menu.add_separator()
        self._status_menu = tk.Menu(self._tree_menu, tearoff=0)
        self._status_menu.add_command(label="○ 待开始",
                                      command=lambda: self._set_node_status(NodeStatus.TODO))
        self._status_menu.add_command(label="● 进行中",
                                      command=lambda: self._set_node_status(NodeStatus.IN_PROGRESS))
        self._status_menu.add_command(label="✓ 已完成",
                                      command=lambda: self._set_node_status(NodeStatus.COMPLETED))
        self._status_menu.add_command(label="⊘ 忽略",
                                      command=lambda: self._set_node_status(NodeStatus.IGNORED))
        self._tree_menu.add_cascade(label="更改状态", menu=self._status_menu)

        # 大纲统计面板（折叠区域）
        self._stats_frame = tk.Frame(left_frame, bg="#f5f5f5", height=80)
        self._stats_frame.pack(fill="x", padx=4, pady=(0, 4))
        self._stats_frame.pack_propagate(False)

        stats_header = tk.Frame(self._stats_frame, bg="#e8e8e8", height=20)
        stats_header.pack(fill="x")
        stats_header.pack_propagate(False)
        self._stats_toggle_btn = tk.Label(stats_header, text="📊 大纲统计 ▲",
                                          font=("Microsoft YaHei", 8, "bold"),
                                          bg="#e8e8e8", fg="#555", anchor="w")
        self._stats_toggle_btn.pack(fill="x", padx=4)
        self._stats_toggle_btn.bind("<Button-1>", lambda e: self._toggle_stats())

        self._stats_body = tk.Frame(self._stats_frame, bg="#f5f5f5")
        self._stats_body.pack(fill="both", expand=True)
        self._stats_labels: dict[str, tk.Label] = {}

    # ── 中栏：子节点列表 + 操作按钮（多行布局） ──
        mid_frame = tk.Frame(paned, bg="#fafafa", width=200)
        paned.add(mid_frame)

        tk.Label(mid_frame, text="子节点", font=("Microsoft YaHei", 10, "bold"),
                 bg="#fafafa", anchor="w").pack(fill="x", padx=8, pady=4)

        self._child_list = tk.Listbox(mid_frame, selectmode="extended",
                                      font=("Microsoft YaHei", 10))
        self._child_list.pack(fill="both", expand=True, padx=4, pady=4)

        # ── 按钮区：多行布局 ──
        btn_container = tk.Frame(mid_frame, bg="#fafafa")
        btn_container.pack(fill="x", padx=4, pady=2)

        # 第1行：排序
        row1 = tk.Frame(btn_container, bg="#fafafa")
        row1.pack(fill="x", pady=1)
        tk.Button(row1, text="▲ 上移", command=self._move_child_up,
                  font=("Microsoft YaHei", 9), padx=4).pack(side="left", padx=1)
        tk.Button(row1, text="▼ 下移", command=self._move_child_down,
                  font=("Microsoft YaHei", 9), padx=4).pack(side="left", padx=1)

        # 第2行：编辑 / 创建
        row2 = tk.Frame(btn_container, bg="#fafafa")
        row2.pack(fill="x", pady=1)
        tk.Button(row2, text="✏ 重命名", command=self._rename_selected,
                  font=("Microsoft YaHei", 9), padx=4).pack(side="left", padx=1)
        tk.Button(row2, text="+ 新建", command=self._create_child,
                  font=("Microsoft YaHei", 9), padx=4).pack(side="left", padx=1)

        # 第3行：层级移动（升级/降级）
        row3 = tk.Frame(btn_container, bg="#fafafa")
        row3.pack(fill="x", pady=1)
        tk.Button(row3, text="⬆ 升级", command=self._promote_node,
                  font=("Microsoft YaHei", 9), padx=4).pack(side="left", padx=1)
        tk.Button(row3, text="⬇ 降级", command=self._demote_node,
                  font=("Microsoft YaHei", 9), padx=4).pack(side="left", padx=1)

        # 第4行：AI 生成
        row4 = tk.Frame(btn_container, bg="#fafafa")
        row4.pack(fill="x", pady=1)
        tk.Button(row4, text="🤖 AI生成", command=self._ai_generate,
                  font=("Microsoft YaHei", 9), bg="#e8f0fe",
                  relief="solid", borderwidth=1, padx=8).pack(side="left", padx=1)

        # 第5行：合并 / 删除
        row5 = tk.Frame(btn_container, bg="#fafafa")
        row5.pack(fill="x", pady=1)
        tk.Button(row5, text="合并", command=self._merge_nodes,
                  font=("Microsoft YaHei", 9), padx=4).pack(side="left", padx=1)
        tk.Button(row5, text="🗑 删除", command=self._delete_node,
                  font=("Microsoft YaHei", 9), padx=4).pack(side="left", padx=1)

        # 右栏：编辑器（标题栏 + 编辑区 + 状态栏）
        right_frame = tk.Frame(paned, bg="#ffffff")
        paned.add(right_frame)

        # ── 标题栏 ──
        title_bar = tk.Frame(right_frame, bg="#f0f0f0", height=34)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)
        tk.Label(title_bar, text="标题:", font=("Microsoft YaHei", 10), bg="#f0f0f0",
                 ).pack(side="left", padx=(8, 2), pady=6)
        self._title_var = tk.StringVar()
        self._title_entry = tk.Entry(title_bar, textvariable=self._title_var,
                                     font=("Microsoft YaHei", 11, "bold"),
                                     relief="flat", bg="#f0f0f0", borderwidth=0)
        self._title_entry.pack(side="left", fill="x", expand=True, padx=2, pady=4)
        self._title_entry.bind("<FocusOut>", lambda e: self._on_title_changed())
        self._title_entry.bind("<Return>", lambda e: self._on_title_changed())

        # 状态指示器
        self._status_var = tk.StringVar(value="○")
        self._status_btn = tk.Button(title_bar, textvariable=self._status_var,
                                     font=("Microsoft YaHei", 11), relief="flat",
                                     bg="#f0f0f0", activebackground="#e0e0e0",
                                     padx=4, command=self._cycle_status)
        self._status_btn.pack(side="right", padx=(0, 8))
        self._status_tooltip = tk.Label(title_bar, text="状态", font=("Microsoft YaHei", 8),
                                        fg="#888", bg="#f0f0f0")
        self._status_tooltip.pack(side="right")

        # ── 编辑区 ──
        self._editor = tk.Text(right_frame, wrap="word", font=("Microsoft YaHei", 11),
                               bg="#ffffff", padx=12, pady=8, borderwidth=0)
        self._editor.pack(fill="both", expand=True)
        self._editor.bind("<KeyRelease>", lambda e: self._mark_modified())

        # ── 底部状态栏 ──
        edit_toolbar = tk.Frame(right_frame, bg="#f5f5f5", height=28)
        edit_toolbar.pack(fill="x", side="bottom")
        tk.Button(edit_toolbar, text="💾 保存", command=self._save_node,
                  font=("Microsoft YaHei", 10), bg="#0078d4", fg="#ffffff",
                  relief="flat", padx=12).pack(side="right", padx=4, pady=2)
        self._word_count_label = tk.Label(edit_toolbar, text="字数: 0",
                                          font=("Microsoft YaHei", 9), bg="#f5f5f5")
        self._word_count_label.pack(side="left", padx=8)
        self._mtime_label = tk.Label(edit_toolbar, text="",
                                     font=("Microsoft YaHei", 8), fg="#999", bg="#f5f5f5")
        self._mtime_label.pack(side="right", padx=(0, 8))

        # ── 键盘快捷键 ──
        self.frame.bind("<Control-s>", lambda e: self._save_node())
        self.frame.bind("<Control-n>", lambda e: self._create_child())
        self.frame.bind("<Control-r>", lambda e: self._rename_selected())
        self.frame.bind("<Delete>", lambda e: self._delete_node())
        self.frame.bind("<Control-Up>", lambda e: self._move_child_up())
        self.frame.bind("<Control-Down>", lambda e: self._move_child_down())
        self.frame.bind("<Control-Left>", lambda e: self._promote_node())
        self.frame.bind("<Control-Right>", lambda e: self._demote_node())

    def _subscribe_events(self):
        self._event_bus.subscribe("outline:tree_changed",
                                  lambda e: (self._refresh_tree(), self._update_stats()))
        self._event_bus.subscribe("project:switched", lambda e: self._refresh_all())

    def on_show(self):
        self._refresh_all()
        self._update_stats()

    def on_close(self):
        self._auto_save_if_dirty()

    def _refresh_all(self):
        self._refresh_project_list()
        self._refresh_tree()

    def _refresh_project_list(self):
        projects = self._project_service.list_projects()
        menu = self._project_menu["menu"]
        menu.delete(0, "end")
        for p in projects:
            menu.add_command(label=p.name,
                             command=lambda n=p.name: self._switch_project(n))
        current = self._project_service.get_current_project()
        # ★ 使用 trace 开关防止 set() 触发 OptionMenu 回调 → _switch_project 递归刷新
        self._suppress_project_callback = True
        self._project_var.set(current or "无项目")
        self._suppress_project_callback = False

    def _switch_project(self, name: str):
        # 防止 OptionMenu 回调在 _refresh_project_list 内部触发递归
        if getattr(self, '_suppress_project_callback', False):
            return
        if not name or name == "无项目":
            return
        if name == self._project_service.get_current_project():
            return
        self._auto_save_if_dirty()
        self._project_service.switch_project(name)
        self._project_var.set(name)
        self._refresh_all()

    def _create_project(self):
        dialog = tk.Toplevel(self.frame)
        dialog.title("创建项目")
        dialog.geometry("300x150")
        dialog.transient(self.frame)

        tk.Label(dialog, text="项目名称:").pack(padx=16, pady=8)
        name_entry = tk.Entry(dialog, width=30)
        name_entry.pack(padx=16, pady=4)
        name_entry.focus()

        def do_create():
            name = name_entry.get().strip()
            if name:
                try:
                    self._project_service.create_project(name)
                    self._project_service.switch_project(name)
                    dialog.destroy()
                    self._refresh_all()
                except ValueError as e:
                    messagebox.showerror("错误", str(e))

        tk.Button(dialog, text="创建", command=do_create).pack(pady=12)
        name_entry.bind("<Return>", lambda e: do_create())

    def _refresh_tree(self):
        self._tree.delete(*self._tree.get_children())
        if not self._project_service.get_current_project():
            return
        nodes = self._project_service.get_outline_tree()
        if not nodes:
            return
        # 找到根节点
        roots = [n for n in nodes if n.parent_id is None]
        for root in sorted(roots, key=lambda n: n.order):
            self._insert_tree_node("", root, nodes)

    def _insert_tree_node(self, parent_iid: str, node: OutlineNode,
                          all_nodes: list[OutlineNode]):
        level_icon = {1: "📗", 2: "📘", 3: "📙", 4: "📕", 5: "📄"}.get(node.level.value, "📄")
        status_icon = {
            NodeStatus.COMPLETED: "✓", NodeStatus.IN_PROGRESS: "●",
            NodeStatus.TODO: "○", NodeStatus.IGNORED: "⊘",
        }.get(node.status, "○")
        iid = self._tree.insert(
            parent_iid, "end", iid=node.node_id,
            text=f"{level_icon} {status_icon} {node.title}",
            open=True,
        )
        for cid in node.children_ids:
            child = next((n for n in all_nodes if n.node_id == cid), None)
            if child:
                self._insert_tree_node(iid, child, all_nodes)

    def _on_node_selected(self, event):
        selection = self._tree.selection()
        if not selection:
            return
        # 自动保存当前编辑中的节点（若已修改）
        self._auto_save_if_dirty()
        self._current_node_id = selection[0]
        node = self._project_service.get_node(self._current_node_id)
        if node is None:
            return

        # ── 标题栏 ──
        self._title_var.set(node.title)
        status_chars = {
            NodeStatus.TODO: "○", NodeStatus.IN_PROGRESS: "●",
            NodeStatus.COMPLETED: "✓", NodeStatus.IGNORED: "⊘",
        }
        self._status_var.set(status_chars.get(node.status, "○"))

        # ── 编辑器 ──
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", node.content)
        self._content_modified = False
        self._word_count_label.config(text=f"字数: {node.word_count}")
        # 修改时间
        mtime = node.updated_at[:16].replace("T", " ") if node.updated_at else ""
        self._mtime_label.config(text=f"修改: {mtime}" if mtime else "")

        # 刷新子节点 + 统计
        self._refresh_children()
        self._update_stats()

    def _create_child(self):
        # 无选中节点且大纲树为空 → 创建根节点
        if not self._current_node_id:
            nodes = self._project_service.get_outline_tree()
            if not nodes:
                parent_id = None
                level = OutlineLevel.OUTLINE
                title_hint = "全书大纲"
            else:
                messagebox.showinfo("提示", "请先在左侧大纲树中选择一个父节点")
                return
        else:
            parent = self._project_service.get_node(self._current_node_id)
            if parent is None:
                return
            parent_id = self._current_node_id
            level = OutlineLevel(min(parent.level.value + 1, 5))
            title_hint = ""

        dialog = tk.Toplevel(self.frame)
        dialog.title("创建节点")
        dialog.geometry("300x120")
        dialog.transient(self.frame)

        tk.Label(dialog, text="节点标题:").pack(padx=16, pady=4)
        entry = tk.Entry(dialog, width=30)
        entry.pack(padx=16, pady=4)
        if title_hint:
            entry.insert(0, title_hint)
        entry.focus()

        def do_create():
            title = entry.get().strip()
            if title:
                self._project_service.create_node(parent_id, title, level)
                dialog.destroy()
                self._refresh_tree()

        tk.Button(dialog, text="创建", command=do_create).pack(pady=8)
        entry.bind("<Return>", lambda e: do_create())

    def _merge_nodes(self):
        selected = self._child_list.curselection()
        if len(selected) < 2:
            messagebox.showinfo("提示", "请在子节点列表中选中至少 2 个节点进行合并")
            return
        children = self._project_service.get_children(self._current_node_id or "")
        child_ids = [children[i].node_id for i in selected if i < len(children)]

        dialog = tk.Toplevel(self.frame)
        dialog.title("合并节点")
        dialog.geometry("300x120")
        dialog.transient(self.frame)
        tk.Label(dialog, text="合并后节点标题:").pack(padx=16, pady=4)
        entry = tk.Entry(dialog, width=30)
        entry.pack(padx=16, pady=4)

        def do_merge():
            title = entry.get().strip()
            if title:
                try:
                    self._project_service.merge_nodes(child_ids, title)
                    dialog.destroy()
                    self._refresh_tree()
                except ValueError as e:
                    messagebox.showerror("错误", str(e))

        tk.Button(dialog, text="合并", command=do_merge).pack(pady=8)
        entry.bind("<Return>", lambda e: do_merge())

    def _delete_node(self):
        if not self._current_node_id:
            return
        node = self._project_service.get_node(self._current_node_id)
        if node is None:
            return
        if messagebox.askyesno("确认删除", f"确定删除「{node.title}」及其所有子节点？"):
            self._project_service.delete_node(self._current_node_id)
            self._current_node_id = None
            self._editor.delete("1.0", "end")
            self._child_list.delete(0, "end")
            self._refresh_tree()

    def _save_node(self):
        if not self._current_node_id:
            return
        content = self._editor.get("1.0", "end-1c")
        self._project_service.update_node(self._current_node_id, content=content)
        self._content_modified = False
        node = self._project_service.get_node(self._current_node_id)
        if node:
            self._word_count_label.config(text=f"字数: {node.word_count}")

    def _mark_modified(self):
        self._content_modified = True

    def _auto_save_if_dirty(self) -> None:
        """离开当前编辑上下文时自动保存（若内容已修改）"""
        if self._content_modified:
            self._save_node()

    # ── 右键菜单：更改父节点 ──

    def _on_tree_right_click(self, event):
        """右击大纲树节点 → 弹出上下文菜单（根节点仅显示状态菜单）"""
        item = self._tree.identify_row(event.y)
        if item:
            self._tree.selection_set(item)
            node = self._project_service.get_node(item)
            if node is not None:
                # 非根节点：显示完整菜单（含更改父节点）
                # 禁用/启用"更改父节点"取决于是否有 parent_id
                self._tree_menu.entryconfigure(0, state="normal" if node.parent_id is not None else "disabled")
                self._tree_menu.post(event.x_root, event.y_root)

    def _change_parent(self):
        """更改当前选中节点的父节点"""
        selection = self._tree.selection()
        if not selection:
            return
        node_id = selection[0]
        node = self._project_service.get_node(node_id)
        if node is None:
            return
        if node.parent_id is None:
            messagebox.showinfo("���示", "根节点不可更改父节点")
            return

        # 收集所有可选父节点（排除自身、子孙节点、当前父节点）
        all_nodes = self._project_service.get_outline_tree()
        # 收集目标节点的所有子孙 ID（防止循环引用）
        descendant_ids = set()

        def _collect_descendants(nid: str):
            cn = self._project_service.get_node(nid)
            if cn is None:
                return
            for cid in cn.children_ids:
                descendant_ids.add(cid)
                _collect_descendants(cid)

        _collect_descendants(node_id)
        descendant_ids.add(node_id)  # 排除自身

        candidates = [
            n for n in all_nodes
            if n.node_id not in descendant_ids
               and n.node_id != node.parent_id
        ]

        if not candidates:
            messagebox.showinfo("提示", "没有可选的父节点")
            return

        # 弹出选择对话框
        dialog = tk.Toplevel(self.frame)
        dialog.title("选择新父节点")
        dialog.geometry("360x320")
        dialog.transient(self.frame)

        tk.Label(dialog, text=f"将「{node.title}」移动到哪个父节点下？",
                 font=("Microsoft YaHei", 10)).pack(padx=16, pady=8)

        listbox = tk.Listbox(dialog, font=("Microsoft YaHei", 10))
        listbox.pack(fill="both", expand=True, padx=16, pady=4)
        level_names = {1: "大纲", 2: "卷纲", 3: "简纲", 4: "章纲", 5: "正文"}
        for n in candidates:
            level_tag = level_names.get(n.level.value, "?")
            listbox.insert("end", f"[{level_tag}] {n.title}")

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(fill="x", padx=16, pady=8)

        def do_move():
            sel = listbox.curselection()
            if not sel:
                return
            new_parent = candidates[sel[0]]
            try:
                self._auto_save_if_dirty()
                new_order = len(self._project_service.get_children(new_parent.node_id))
                self._project_service.move_node(node_id, new_parent.node_id, new_order)
                dialog.destroy()
                self._refresh_tree()
                self._logger.log(
                    f"节点「{node.title}」已移动到「{new_parent.title}」",
                    "OutlinePanel", "INFO",
                )
            except Exception as e:
                messagebox.showerror("错误", str(e))

        tk.Button(btn_frame, text="确认移动", command=do_move,
                  font=("Microsoft YaHei", 10), bg="#0078d4", fg="#ffffff",
                  relief="flat", padx=12).pack(side="right", padx=4)
        tk.Button(btn_frame, text="取消", command=dialog.destroy,
                  font=("Microsoft YaHei", 10)).pack(side="right", padx=4)

    # ── 大纲树工具栏操作 ──

    def _expand_all(self):
        """展开大纲树全部节点"""
        def _expand_children(iid):
            self._tree.item(iid, open=True)
            for child in self._tree.get_children(iid):
                _expand_children(child)
        for root in self._tree.get_children():
            _expand_children(root)

    def _collapse_all(self):
        """折叠大纲树全部节点（保留一级展开）"""
        def _collapse_children(iid):
            for child in self._tree.get_children(iid):
                _collapse_children(child)
            if self._tree.get_children(iid):
                self._tree.item(iid, open=False)
        for root in self._tree.get_children():
            _collapse_children(root)

    def _filter_tree(self):
        """搜索过滤：高亮匹配节点并展开路径"""
        keyword = self._tree_search_var.get().strip()
        # 先全部恢复默认样式和折叠
        def _restore(iid):
            self._tree.item(iid, tags=())
            for child in self._tree.get_children(iid):
                _restore(child)
        for root in self._tree.get_children():
            _restore(root)

        if not keyword:
            return

        # 匹配并展开路径
        self._tree.tag_configure("search_hit", background="#FFFF99", foreground="#000000")
        matched = set()

        def _find_and_expand(iid):
            text = self._tree.item(iid, "text")
            hit = keyword.lower() in text.lower()
            if hit:
                matched.add(iid)
            for child in self._tree.get_children(iid):
                if _find_and_expand(child) or hit:
                    self._tree.item(iid, open=True)
            return hit and not matched.intersection(self._tree.get_children(iid))

        for root in self._tree.get_children():
            _find_and_expand(root)

        # 高亮所有匹配节点
        for iid in matched:
            self._tree.item(iid, tags=("search_hit",))

    # ── 状态切换 ──

    def _set_node_status(self, status: NodeStatus):
        """右键菜单 → 更改选中节点的状态"""
        selection = self._tree.selection()
        if not selection:
            return
        node_id = selection[0]
        node = self._project_service.get_node(node_id)
        if node is None:
            return
        self._project_service.update_node(node_id, status=status)
        self._refresh_tree()
        # 同步标题栏状态图标
        status_chars = {NodeStatus.TODO: "○", NodeStatus.IN_PROGRESS: "●",
                        NodeStatus.COMPLETED: "✓", NodeStatus.IGNORED: "⊘"}
        self._status_var.set(status_chars.get(status, "○"))
        self._logger.log(
            f"节点「{node.title}」状态 → {status.value}", "OutlinePanel", "INFO",
        )

    # ── 编辑器标题栏操作 ──

    def _on_title_changed(self):
        """标题编辑框失去焦点/回车 → 保存标题"""
        new_title = self._title_var.get().strip()
        if not new_title or not self._current_node_id:
            return
        node = self._project_service.get_node(self._current_node_id)
        if node is None or new_title == node.title:
            return
        self._project_service.update_node(self._current_node_id, title=new_title)
        self._refresh_tree()
        self._logger.log(f"节点标题 → {new_title}", "OutlinePanel", "INFO")

    def _cycle_status(self):
        """点击状态按钮 → 循环切换 TODO→进行中→已完成→忽略→TODO"""
        if not self._current_node_id:
            return
        node = self._project_service.get_node(self._current_node_id)
        if node is None:
            return
        order = [NodeStatus.TODO, NodeStatus.IN_PROGRESS,
                 NodeStatus.COMPLETED, NodeStatus.IGNORED]
        idx = order.index(node.status) if node.status in order else 0
        next_status = order[(idx + 1) % len(order)]
        self._set_node_status(next_status)

    # ── 统计面板 ──

    def _toggle_stats(self):
        """展开/折叠统计面板"""
        if self._stats_body.winfo_ismapped():
            self._stats_body.pack_forget()
            self._stats_toggle_btn.config(text="📊 大纲统计 ▶")
            self._stats_frame.config(height=20)
        else:
            self._stats_body.pack(fill="both", expand=True)
            self._stats_toggle_btn.config(text="📊 大纲统计 ▲")
            self._stats_frame.config(height=80)
            self._update_stats()

    def _update_stats(self):
        """刷新统计面板数据"""
        # 清除旧标签
        for w in self._stats_body.winfo_children():
            w.destroy()
        self._stats_labels.clear()

        nodes = self._project_service.get_outline_tree()
        if not nodes:
            tk.Label(self._stats_body, text="  无数据", font=("Microsoft YaHei", 8),
                     fg="#999", bg="#f5f5f5", anchor="w").pack(fill="x", padx=6)
            return

        total = len(nodes)
        level_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        status_counts = {s: 0 for s in NodeStatus}
        for n in nodes:
            level_counts[n.level.value] = level_counts.get(n.level.value, 0) + 1
            status_counts[n.status] = status_counts.get(n.status, 0) + 1

        done = status_counts.get(NodeStatus.COMPLETED, 0)
        pct = f"{done / total * 100:.0f}%" if total > 0 else "0%"

        lines = [
            f"  总节点: {total}    完成率: {pct}",
            f"  L1: {level_counts[1]}  L2: {level_counts[2]}  L3: {level_counts[3]}  L4: {level_counts[4]}  L5: {level_counts[5]}",
        ]
        for line in lines:
            tk.Label(self._stats_body, text=line, font=("Microsoft YaHei", 8),
                     fg="#555", bg="#f5f5f5", anchor="w").pack(fill="x", padx=6)

    # ── AI 辅助生成 ──

    def _ai_generate(self):
        """AI 辅助：为选中节点自动生成子节点"""
        if not self._ai_client:
            messagebox.showinfo("提示", "AI 客户端未初始化，无法使用 AI 生成功能")
            return
        if not self._current_node_id:
            messagebox.showinfo("提示", "请先在左侧大纲树中选中一个节点")
            return
        node = self._project_service.get_node(self._current_node_id)
        if node is None:
            return

        # 弹出设置对话框
        dialog = tk.Toplevel(self.frame)
        dialog.title("AI 辅助生成子节点")
        dialog.geometry("380x280")
        dialog.transient(self.frame)

        tk.Label(dialog, text=f"为「{node.title}」生成子节点",
                 font=("Microsoft YaHei", 10, "bold")).pack(padx=16, pady=(12, 8))

        # 生成数量
        f1 = tk.Frame(dialog)
        f1.pack(fill="x", padx=16, pady=2)
        tk.Label(f1, text="生成数量:", font=("Microsoft YaHei", 9)).pack(side="left")
        count_var = tk.IntVar(value=3)
        tk.Spinbox(f1, from_=1, to=10, textvariable=count_var,
                   width=5, font=("Microsoft YaHei", 9)).pack(side="left", padx=4)

        # 风格提示
        f2 = tk.Frame(dialog)
        f2.pack(fill="x", padx=16, pady=2)
        tk.Label(f2, text="风格提示:", font=("Microsoft YaHei", 9)).pack(anchor="w")
        style_text = tk.Text(f2, height=3, font=("Microsoft YaHei", 9),
                              relief="solid", borderwidth=1, wrap="word")
        style_text.pack(fill="x", pady=2)
        style_text.insert("1.0", "请生成具体的子节点，每个节点一行标题。")

        # 进度条
        self._ai_progress = ttk.Progressbar(dialog, mode="indeterminate")

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(fill="x", padx=16, pady=(8, 12))

        def do_generate():
            count = count_var.get()
            style = style_text.get("1.0", "end-1c").strip()
            self._auto_save_if_dirty()

            # 构造提示词
            level_names = {1: "大纲", 2: "卷纲", 3: "简纲", 4: "章纲", 5: "正文"}
            current_level = level_names.get(node.level.value, "节点")
            child_level = level_names.get(min(node.level.value + 1, 5), "子节点")

            prompt = (
                f"当前{current_level}「{node.title}」的内容如下:\n"
                f"{node.content[:2000]}\n\n"
                f"请为以上内容生成 {count} 个{child_level}级别的子节点。"
                f"每个子节点单独一行，格式严格为:\n"
                f"### 子节点标题\n子节点简要描述（1-2句）\n"
                f"风格要求: {style}"
            )

            self._ai_progress.pack(fill="x", padx=16, pady=4)
            self._ai_progress.start()
            dialog.update()

            try:
                # 调用 AI 非流式
                from src.services.ai_client import ChatMessage
                msgs = [ChatMessage(role="user", content=prompt)]
                response = self._ai_client.chat(msgs)
                text = response.content

                # 解析子节点
                blocks = text.strip().split("### ")
                created = 0
                for block in blocks:
                    block = block.strip()
                    if not block:
                        continue
                    lines = block.split("\n", 1)
                    title = lines[0].strip()
                    desc = lines[1].strip() if len(lines) > 1 else ""
                    content = f"# {title}\n\n{desc}" if desc else f"# {title}\n"
                    child = self._project_service.create_node(
                        self._current_node_id, title,
                        OutlineLevel(min(node.level.value + 1, 5)),
                        content,
                    )
                    created += 1
                    if created >= count:
                        break

                self._logger.log(
                    f"AI 生成: 为「{node.title}」创建了 {created} 个子节点",
                    "OutlinePanel", "INFO",
                )
                self._refresh_tree()
                dialog.destroy()
                if created == 0:
                    messagebox.showwarning("提示", "AI 未能生成有效的子节点，请重试。")

            except Exception as e:
                dialog.destroy()
                messagebox.showerror("AI 生成失败",
                                     f"错误: {str(e)}\n请检查 AI 配置和网络连接。")
                self._logger.log(f"AI 生成失败: {e}", "OutlinePanel", "ERROR")

        tk.Button(btn_frame, text="开始生成", command=do_generate,
                  font=("Microsoft YaHei", 10), bg="#0078d4", fg="#ffffff",
                  relief="flat", padx=12).pack(side="right", padx=4)
        tk.Button(btn_frame, text="取消", command=dialog.destroy,
                  font=("Microsoft YaHei", 10)).pack(side="right", padx=4)

    # ── 升级 / 降级 ──

    def _promote_node(self):
        """将选中节点提升一级（移至父节点的父节点下）"""
        selection = self._tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先在左侧大纲树中选中一个节点")
            return
        node_id = selection[0]
        node = self._project_service.get_node(node_id)
        if node is None:
            return
        if node.parent_id is None:
            messagebox.showinfo("提示", "根节点无法再升级")
            return
        if node.level == OutlineLevel.OUTLINE:
            messagebox.showinfo("提示", "L1 大纲已是最高层级，无法升级")
            return
        parent = self._project_service.get_node(node.parent_id)
        if parent is None or parent.parent_id is None:
            messagebox.showinfo("提示", "父节点之上没有可挂载的位置")
            return
        self._auto_save_if_dirty()
        grandparent = self._project_service.get_node(parent.parent_id)
        new_order = len(self._project_service.get_children(grandparent.node_id))
        try:
            self._project_service.move_node(node_id, grandparent.node_id, new_order)
            self._refresh_tree()
            self._logger.log(f"节点「{node.title}」已升级", "OutlinePanel", "INFO")
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def _demote_node(self):
        """将选中节点降低一级（移至前一个兄弟节点的最后一个子节点下）"""
        selection = self._tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先在左侧大纲树中选中一个节点")
            return
        node_id = selection[0]
        node = self._project_service.get_node(node_id)
        if node is None:
            return
        if node.parent_id is None:
            messagebox.showinfo("提示", "根节点无法降级")
            return
        if node.level == OutlineLevel.CONTENT:
            messagebox.showinfo("提示", "L5 正文已是底层，无法降级")
            return
        # 找到前一个兄弟节点，将自己挂到它的最末尾
        siblings = self._project_service.get_children(node.parent_id)
        my_idx = next((i for i, s in enumerate(siblings) if s.node_id == node_id), -1)
        if my_idx <= 0:
            messagebox.showinfo("提示", "没有前序兄弟节点可用于降级")
            return
        prev_sibling = siblings[my_idx - 1]
        self._auto_save_if_dirty()
        new_order = len(self._project_service.get_children(prev_sibling.node_id))
        try:
            self._project_service.move_node(node_id, prev_sibling.node_id, new_order)
            self._refresh_tree()
            self._logger.log(f"节点「{node.title}」已降级到「{prev_sibling.title}」下",
                             "OutlinePanel", "INFO")
        except Exception as e:
            messagebox.showerror("错误", str(e))

    # ── 重命名 ──

    def _on_node_rename(self, event):
        """双击大纲树节点 → 弹出重命名对话框"""
        from tkinter import simpledialog
        selection = self._tree.selection()
        if not selection:
            return
        node_id = selection[0]
        node = self._project_service.get_node(node_id)
        if node is None:
            return
        new_title = simpledialog.askstring("重命名", "输入新名称:",
                                           initialvalue=node.title, parent=self.frame)
        if new_title and new_title.strip() and new_title.strip() != node.title:
            self._project_service.update_node(node_id, title=new_title.strip())
            self._refresh_tree()

    def _rename_selected(self):
        """按钮触发的重命名（大纲树选中节点）"""
        selection = self._tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先在左侧大纲树中选择一个节点")
            return
        self._on_node_rename(None)

    # ── 排序（上下移动子节点） ──

    def _move_child_up(self):
        """将子节点列表中选中的节点上移一位"""
        if not self._current_node_id:
            return
        selected = self._child_list.curselection()
        if not selected or len(selected) != 1:
            return
        idx = selected[0]
        if idx == 0:
            return
        self._auto_save_if_dirty()
        children = self._project_service.get_children(self._current_node_id)
        # 交换顺序
        children[idx], children[idx - 1] = children[idx - 1], children[idx]
        ordered_ids = [c.node_id for c in children]
        self._project_service.reorder_siblings(self._current_node_id, ordered_ids)
        self._refresh_children()
        self._child_list.selection_set(idx - 1)

    def _move_child_down(self):
        """将子节点列表中选中的节点下移一位"""
        if not self._current_node_id:
            return
        selected = self._child_list.curselection()
        if not selected or len(selected) != 1:
            return
        idx = selected[0]
        children = self._project_service.get_children(self._current_node_id)
        if idx >= len(children) - 1:
            return
        self._auto_save_if_dirty()
        # 交换顺序
        children[idx], children[idx + 1] = children[idx + 1], children[idx]
        ordered_ids = [c.node_id for c in children]
        self._project_service.reorder_siblings(self._current_node_id, ordered_ids)
        self._refresh_children()
        self._child_list.selection_set(idx + 1)

    def _refresh_children(self):
        """刷新子节点列表（不刷新整棵树）"""
        self._child_list.delete(0, "end")
        if not self._current_node_id:
            return
        children = self._project_service.get_children(self._current_node_id)
        for child in children:
            self._child_list.insert("end", child.title)


# ==================== SettingsPanel ====================

class SettingsPanel(BasePanel):
    """设定管理面板 — 用户自由定义分类，每类下自由管理 Markdown 文档"""

    def __init__(self, parent, event_bus, logger, project_service: ProjectService):
        self._project_service = project_service
        self._current_cat: Optional[str] = None
        self._current_doc: Optional[str] = None
        self._content_modified = False
        super().__init__(parent, event_bus, logger)

    def _setup_ui(self):
        paned = tk.PanedWindow(self.frame, orient="horizontal", bg="#e0e0e0")
        paned.pack(fill="both", expand=True)

        # 左栏：分类 + 文档列表
        left_frame = tk.Frame(paned, bg="#fafafa", width=220)
        paned.add(left_frame)

        tk.Label(left_frame, text="分类", font=("Microsoft YaHei", 10, "bold"),
                 bg="#fafafa").pack(anchor="w", padx=8, pady=4)
        self._cat_list = tk.Listbox(left_frame, font=("Microsoft YaHei", 10))
        self._cat_list.pack(fill="x", padx=4, pady=2)
        self._cat_list.bind("<<ListboxSelect>>", self._on_cat_selected)
        self._cat_list.bind("<Double-1>", self._on_cat_rename)

        cat_btn = tk.Frame(left_frame, bg="#fafafa")
        cat_btn.pack(fill="x", padx=4, pady=2)
        tk.Button(cat_btn, text="▲", command=self._move_cat_up,
                  font=("Microsoft YaHei", 8), padx=3).pack(side="left", padx=1)
        tk.Button(cat_btn, text="▼", command=self._move_cat_down,
                  font=("Microsoft YaHei", 8), padx=3).pack(side="left", padx=1)
        tk.Button(cat_btn, text="✏", command=self._rename_cat_btn,
                  font=("Microsoft YaHei", 8), padx=3).pack(side="left", padx=1)
        tk.Button(cat_btn, text="+分类", command=self._create_category,
                  font=("Microsoft YaHei", 9)).pack(side="left", padx=2)
        tk.Button(cat_btn, text="🗑", command=self._delete_category,
                  font=("Microsoft YaHei", 9)).pack(side="left", padx=2)

        tk.Label(left_frame, text="文档", font=("Microsoft YaHei", 10, "bold"),
                 bg="#fafafa").pack(anchor="w", padx=8, pady=(12, 4))
        self._doc_list = tk.Listbox(left_frame, font=("Microsoft YaHei", 10))
        self._doc_list.pack(fill="both", expand=True, padx=4, pady=2)
        self._doc_list.bind("<<ListboxSelect>>", self._on_doc_selected)
        self._doc_list.bind("<Double-1>", self._on_doc_rename)

        doc_btn = tk.Frame(left_frame, bg="#fafafa")
        doc_btn.pack(fill="x", padx=4, pady=2)
        tk.Button(doc_btn, text="▲", command=self._move_doc_up,
                  font=("Microsoft YaHei", 8), padx=3).pack(side="left", padx=1)
        tk.Button(doc_btn, text="▼", command=self._move_doc_down,
                  font=("Microsoft YaHei", 8), padx=3).pack(side="left", padx=1)
        tk.Button(doc_btn, text="✏", command=self._rename_doc_btn,
                  font=("Microsoft YaHei", 8), padx=3).pack(side="left", padx=1)
        tk.Button(doc_btn, text="+文档", command=self._create_doc,
                  font=("Microsoft YaHei", 9)).pack(side="left", padx=2)
        tk.Button(doc_btn, text="🗑", command=self._delete_doc,
                  font=("Microsoft YaHei", 9)).pack(side="left", padx=2)

        # 右栏：编辑器
        right_frame = tk.Frame(paned, bg="#ffffff")
        paned.add(right_frame)

        self._editor = tk.Text(right_frame, wrap="word", font=("Microsoft YaHei", 11),
                                bg="#ffffff", padx=12, pady=8, borderwidth=0)
        self._editor.pack(fill="both", expand=True)
        self._editor.bind("<KeyRelease>", lambda e: self._mark_modified())

        save_frame = tk.Frame(right_frame, bg="#f5f5f5", height=32)
        save_frame.pack(fill="x", side="bottom")
        tk.Button(save_frame, text="💾 保存", command=self._save_doc,
                  bg="#0078d4", fg="#ffffff", relief="flat", padx=12,
                  font=("Microsoft YaHei", 10)).pack(side="right", padx=4, pady=2)
        tk.Button(save_frame, text="📤 导出全部", command=self._export_all,
                  font=("Microsoft YaHei", 10)).pack(side="right", padx=4, pady=2)

    def _subscribe_events(self):
        self._event_bus.subscribe("project:switched", lambda e: self._on_project_switched())

    def _on_project_switched(self):
        self._auto_save_if_dirty()
        self._current_cat = None
        self._current_doc = None
        self._editor.delete("1.0", "end")
        self._content_modified = False

    def on_show(self):
        self._refresh_cats()

    def on_close(self):
        self._auto_save_if_dirty()

    def _refresh_cats(self):
        self._cat_list.delete(0, "end")
        if not self._project_service.get_current_project():
            return
        for c in self._project_service.list_categories():
            self._cat_list.insert("end", c)

    def _refresh_docs(self):
        self._doc_list.delete(0, "end")
        if self._current_cat:
            for d in self._project_service.list_docs(self._current_cat):
                self._doc_list.insert("end", d)

    def _on_cat_selected(self, event):
        sel = self._cat_list.curselection()
        if not sel:
            return
        self._auto_save_if_dirty()
        self._current_cat = self._cat_list.get(sel[0])
        self._current_doc = None
        self._refresh_docs()

    def _on_doc_selected(self, event):
        sel = self._doc_list.curselection()
        if not sel or not self._current_cat:
            return
        self._auto_save_if_dirty()
        self._current_doc = self._doc_list.get(sel[0])
        content = self._project_service.get_setting(self._current_cat, self._current_doc) or ""
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", content)
        self._content_modified = False

    def _create_category(self):
        dialog = tk.Toplevel(self.frame)
        dialog.title("新建分类")
        dialog.geometry("300x120")
        dialog.transient(self.frame)
        tk.Label(dialog, text="分类名称（如：力量体系、角色、地理）:").pack(padx=16, pady=4)
        entry = tk.Entry(dialog, width=30)
        entry.pack(padx=16, pady=4)
        entry.focus()

        def create():
            name = entry.get().strip()
            if name:
                self._project_service.save_setting(name, "_placeholder", "")
                dialog.destroy()
                self._refresh_cats()

        tk.Button(dialog, text="创建", command=create).pack(pady=8)
        entry.bind("<Return>", lambda e: create())

    def _delete_category(self):
        self._auto_save_if_dirty()
        if self._current_cat and messagebox.askyesno("确认", f"删除分类「{self._current_cat}」及其所有文档？"):
            self._project_service.delete_category(self._current_cat)
            self._current_cat = None
            self._current_doc = None
            self._editor.delete("1.0", "end")
            self._refresh_cats()
            self._doc_list.delete(0, "end")

    def _create_doc(self):
        if not self._current_cat:
            messagebox.showinfo("提示", "请先选择一个分类")
            return
        dialog = tk.Toplevel(self.frame)
        dialog.title("新建文档")
        dialog.geometry("300x120")
        dialog.transient(self.frame)
        tk.Label(dialog, text="文档名称:").pack(padx=16, pady=4)
        entry = tk.Entry(dialog, width=30)
        entry.pack(padx=16, pady=4)
        entry.focus()

        def create():
            name = entry.get().strip()
            if name:
                self._project_service.save_setting(self._current_cat, name, "# " + name)
                dialog.destroy()
                self._refresh_docs()

        tk.Button(dialog, text="创建", command=create).pack(pady=8)
        entry.bind("<Return>", lambda e: create())

    def _save_doc(self):
        if self._current_cat and self._current_doc:
            content = self._editor.get("1.0", "end-1c")
            self._project_service.save_setting(self._current_cat, self._current_doc, content)
            self._content_modified = False

    def _mark_modified(self):
        self._content_modified = True

    def _auto_save_if_dirty(self) -> None:
        """离开当前编辑上下文时自动保存（若内容已修改）"""
        if self._content_modified:
            self._save_doc()

    def _delete_doc(self):
        self._auto_save_if_dirty()
        if self._current_cat and self._current_doc:
            if messagebox.askyesno("确认", f"删除文档「{self._current_doc}」？"):
                self._project_service.delete_setting(self._current_cat, self._current_doc)
                self._current_doc = None
                self._editor.delete("1.0", "end")
                self._refresh_docs()

    # ── 分类重命名与排序 ──

    def _on_cat_rename(self, event):
        """双击分类 → 弹出重命名对话框"""
        sel = self._cat_list.curselection()
        if not sel:
            return
        from tkinter import simpledialog
        old_name = self._cat_list.get(sel[0])
        new_name = simpledialog.askstring("重命名分类", "输入新名称:",
                                           initialvalue=old_name, parent=self.frame)
        if new_name and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            self._project_service.rename_category(old_name, new_name)
            self._current_cat = new_name if self._current_cat == old_name else self._current_cat
            self._refresh_cats()

    def _rename_cat_btn(self):
        """按钮触发分类重命名"""
        sel = self._cat_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先选择一个分类")
            return
        self._on_cat_rename(None)

    def _move_cat_up(self):
        """分类上移一位"""
        sel = self._cat_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx == 0:
            return
        cats = list(self._cat_list.get(0, "end"))
        cats[idx], cats[idx - 1] = cats[idx - 1], cats[idx]
        self._project_service.reorder_categories(cats)
        self._refresh_cats()
        self._cat_list.selection_set(idx - 1)

    def _move_cat_down(self):
        """分类下移一位"""
        sel = self._cat_list.curselection()
        if not sel:
            return
        idx = sel[0]
        cats = list(self._cat_list.get(0, "end"))
        if idx >= len(cats) - 1:
            return
        cats[idx], cats[idx + 1] = cats[idx + 1], cats[idx]
        self._project_service.reorder_categories(cats)
        self._refresh_cats()
        self._cat_list.selection_set(idx + 1)

    # ── 文档重命名与排序 ──

    def _on_doc_rename(self, event):
        """双击文档 → 弹出重命名对话框"""
        sel = self._doc_list.curselection()
        if not sel or not self._current_cat:
            return
        from tkinter import simpledialog
        old_name = self._doc_list.get(sel[0])
        new_name = simpledialog.askstring("重命名文档", "输入新名称:",
                                           initialvalue=old_name, parent=self.frame)
        if new_name and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            self._project_service.rename_setting(self._current_cat, old_name, new_name)
            self._current_doc = new_name if self._current_doc == old_name else self._current_doc
            self._refresh_docs()

    def _rename_doc_btn(self):
        """按钮触发文档重命名"""
        sel = self._doc_list.curselection()
        if not sel or not self._current_cat:
            messagebox.showinfo("提示", "请先选择一个文档")
            return
        self._on_doc_rename(None)

    def _move_doc_up(self):
        """文档上移一位"""
        sel = self._doc_list.curselection()
        if not sel or not self._current_cat:
            return
        idx = sel[0]
        if idx == 0:
            return
        docs = list(self._doc_list.get(0, "end"))
        docs[idx], docs[idx - 1] = docs[idx - 1], docs[idx]
        self._project_service.reorder_docs(self._current_cat, docs)
        self._refresh_docs()
        self._doc_list.selection_set(idx - 1)

    def _move_doc_down(self):
        """文档下移一位"""
        sel = self._doc_list.curselection()
        if not sel or not self._current_cat:
            return
        idx = sel[0]
        docs = list(self._doc_list.get(0, "end"))
        if idx >= len(docs) - 1:
            return
        docs[idx], docs[idx + 1] = docs[idx + 1], docs[idx]
        self._project_service.reorder_docs(self._current_cat, docs)
        self._refresh_docs()
        self._doc_list.selection_set(idx + 1)

    def _export_all(self):
        out_dir = filedialog.askdirectory(title="选择导出目录")
        if out_dir:
            self._project_service.export_settings(output_dir=out_dir, merge=True)
            messagebox.showinfo("导出完成", f"已导出到 {out_dir}")


# ==================== ConfigPanel ====================

class ConfigPanel(BasePanel):
    """配置管理面板"""

    def __init__(self, parent, event_bus, logger,
                 config_manager: ConfigManager, ai_client: AIClient):
        self._config_manager = config_manager
        self._ai_client = ai_client
        self._current_source_name: Optional[str] = None
        super().__init__(parent, event_bus, logger)

    def _setup_ui(self):
        self._notebook = ttk.Notebook(self.frame)
        self._notebook.pack(fill="both", expand=True)

        # ── Tab 1: AI 源配置 ──
        ai_tab = tk.Frame(self._notebook, bg="#fafafa")
        self._notebook.add(ai_tab, text="AI 源配置")

        paned = tk.PanedWindow(ai_tab, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # 左侧 AI 源列表
        left_frame = tk.Frame(paned, bg="#fafafa", width=180)
        paned.add(left_frame)

        tk.Label(left_frame, text="AI 源", font=("Microsoft YaHei", 10, "bold"),
                 bg="#fafafa").pack(anchor="w", padx=8, pady=4)
        self._source_list = tk.Listbox(left_frame, font=("Microsoft YaHei", 10))
        self._source_list.pack(fill="both", expand=True, padx=4, pady=4)
        self._source_list.bind("<<ListboxSelect>>", self._on_source_selected)

        btn_frame = tk.Frame(left_frame, bg="#fafafa")
        btn_frame.pack(fill="x", padx=4, pady=4)
        tk.Button(btn_frame, text="+新增", command=self._add_source, font=("Microsoft YaHei", 9)).pack(side="left", padx=2)
        tk.Button(btn_frame, text="🗑删除", command=self._delete_source, font=("Microsoft YaHei", 9)).pack(side="left", padx=2)

        # 右侧表单
        right_frame = tk.Frame(paned, bg="#ffffff")
        paned.add(right_frame)

        fields = [
            ("名称", "name"), ("Base URL", "base_url"),
            ("API Key", "api_key"), ("模型", "model"),
            ("备用模型", "model_minor"),
        ]
        self._entries = {}
        for i, (label, key) in enumerate(fields):
            tk.Label(right_frame, text=label + ":", anchor="w",
                     bg="#ffffff", font=("Microsoft YaHei", 10)).pack(fill="x", padx=16, pady=(8, 2))
            entry = tk.Entry(right_frame, width=50, font=("Microsoft YaHei", 10))
            entry.pack(fill="x", padx=16)
            if key == "api_key":
                entry.config(show="*")
            self._entries[key] = entry

        # Temperature
        self._temp_label = tk.Label(right_frame, text="Temperature: 1.00", anchor="w",
                 bg="#ffffff", font=("Microsoft YaHei", 10))
        self._temp_label.pack(fill="x", padx=16, pady=(8, 2))
        self._temp_scale = tk.Scale(right_frame, from_=0, to=200, orient="horizontal",
                                    command=lambda v: self._update_temp_label(float(v) / 100))
        self._temp_scale.set(100)
        self._temp_scale.pack(fill="x", padx=16)

        # Top-P
        self._top_p_label = tk.Label(right_frame, text="Top-P: 0.90", anchor="w",
                 bg="#ffffff", font=("Microsoft YaHei", 10))
        self._top_p_label.pack(fill="x", padx=16, pady=(8, 2))
        self._top_p_scale = tk.Scale(right_frame, from_=0, to=100, orient="horizontal",
                                     command=lambda v: self._update_top_p_label(float(v) / 100))
        self._top_p_scale.set(90)
        self._top_p_scale.pack(fill="x", padx=16)

        # Max Tokens
        self._max_tokens_label = tk.Label(right_frame, text="Max Tokens: 2048", anchor="w",
                 bg="#ffffff", font=("Microsoft YaHei", 10))
        self._max_tokens_label.pack(fill="x", padx=16, pady=(8, 2))
        self._max_tokens_scale = tk.Scale(right_frame, from_=256, to=32768, resolution=256,
                                          orient="horizontal",
                                          command=lambda v: self._update_max_tokens_label(int(v)))
        self._max_tokens_scale.set(2048)
        self._max_tokens_scale.pack(fill="x", padx=16)

        # 按钮
        btn_frame_r = tk.Frame(right_frame, bg="#ffffff")
        btn_frame_r.pack(fill="x", padx=16, pady=12)
        tk.Button(btn_frame_r, text="🔌 测试连接", command=self._test_connection,
                  font=("Microsoft YaHei", 10), padx=12).pack(side="left", padx=4)
        tk.Button(btn_frame_r, text="✅ 设为当前", command=self._set_as_current,
                  bg="#ff8c00", fg="#ffffff", font=("Microsoft YaHei", 10),
                  relief="flat", padx=12).pack(side="right", padx=4)
        tk.Button(btn_frame_r, text="💾 保存", command=self._save_source,
                  bg="#0078d4", fg="#ffffff", font=("Microsoft YaHei", 10),
                  relief="flat", padx=12).pack(side="right", padx=4)

        # ── Tab 2: 全局设置 ──
        global_tab = tk.Frame(self._notebook, bg="#ffffff")
        self._notebook.add(global_tab, text="全局设置")

        cfg = self._config_manager.load_app_config()

        self._global_tool_enabled = tk.BooleanVar(value=cfg.tool_enabled)
        tk.Checkbutton(global_tab, text="默认启用 AI 工具调用（对话页面的 🔧AI工具 开关默认开启）",
                       variable=self._global_tool_enabled, bg="#ffffff",
                       font=("Microsoft YaHei", 11),
                       command=self._save_global_settings).pack(anchor="w", padx=24, pady=12)

        tk.Label(global_tab, text="工具调用最大轮数（AI 连续调用工具的上限）:", bg="#ffffff",
                 font=("Microsoft YaHei", 10)).pack(anchor="w", padx=24, pady=(4, 2))
        self._global_max_rounds = tk.StringVar(value=str(cfg.max_tool_rounds))
        tk.Spinbox(global_tab, from_=1, to=20, textvariable=self._global_max_rounds,
                   width=5, font=("Microsoft YaHei", 10),
                   command=self._save_global_settings).pack(anchor="w", padx=24)

        ttk.Separator(global_tab, orient="horizontal").pack(fill="x", padx=24, pady=8)

        tk.Label(global_tab, text="日志级别:", bg="#ffffff",
                 font=("Microsoft YaHei", 10)).pack(anchor="w", padx=24, pady=(8, 2))
        self._global_log_level = tk.StringVar(value=cfg.log_level)
        ttk.OptionMenu(global_tab, self._global_log_level, cfg.log_level,
                       "DEBUG", "INFO", "WARNING", "ERROR",
                       command=lambda v: self._save_global_settings()).pack(anchor="w", padx=24)

        tk.Label(global_tab, text="日志保留天数:", bg="#ffffff",
                 font=("Microsoft YaHei", 10)).pack(anchor="w", padx=24, pady=(12, 2))
        self._global_log_days = tk.StringVar(value=str(cfg.log_retention_days))
        tk.Spinbox(global_tab, from_=1, to=90, textvariable=self._global_log_days,
                   width=5, font=("Microsoft YaHei", 10),
                   command=self._save_global_settings).pack(anchor="w", padx=24)

    def on_show(self):
        self._refresh_source_list()

    def _refresh_source_list(self):
        self._source_list.delete(0, "end")
        for s in self._config_manager.list_ai_sources():
            self._source_list.insert("end", s.name)

    def _on_source_selected(self, event):
        sel = self._source_list.curselection()
        if not sel:
            return
        name = self._source_list.get(sel[0])
        source = self._config_manager.get_ai_source(name)
        if source is None:
            return
        self._current_source_name = name
        self._entries["name"].delete(0, "end")
        self._entries["name"].insert(0, source.name)
        self._entries["base_url"].delete(0, "end")
        self._entries["base_url"].insert(0, source.base_url)
        self._entries["api_key"].delete(0, "end")
        key = self._config_manager.get_api_key(name) or ""
        self._entries["api_key"].insert(0, key)
        self._entries["model"].delete(0, "end")
        self._entries["model"].insert(0, source.model)
        self._entries["model_minor"].delete(0, "end")
        self._entries["model_minor"].insert(0, source.model_minor)
        self._temp_scale.set(int(source.temperature * 100))
        self._top_p_scale.set(int(source.top_p * 100))
        self._max_tokens_scale.set(source.max_tokens)

    def _add_source(self):
        self._clear_form()
        self._current_source_name = None
        self._entries["name"].focus()

    def _save_source(self):
        """保存 AI 源配置（不切换当前使用的源）"""
        name = self._entries["name"].get().strip()
        base_url = self._entries["base_url"].get().strip()
        api_key = self._entries["api_key"].get().strip()
        if not name or not base_url:
            messagebox.showwarning("提示", "名称和 Base URL 为必填项")
            return

        source = AISourceConfig(
            name=name,
            base_url=base_url.rstrip("/"),
            model=self._entries["model"].get().strip(),
            model_minor=self._entries["model_minor"].get().strip(),
            temperature=self._temp_scale.get() / 100,
            top_p=self._top_p_scale.get() / 100,
            max_tokens=int(self._max_tokens_scale.get()),
        )

        if self._current_source_name and self._current_source_name != name:
            self._config_manager.remove_ai_source(self._current_source_name)

        self._config_manager.add_ai_source(source)
        self._config_manager.set_api_key(name, api_key)
        self._current_source_name = name
        self._refresh_source_list()
        messagebox.showinfo("保存成功", f"AI 源「{name}」已保存")

    def _set_as_current(self):
        """将当前选中的 AI 源设为激活状态"""
        name = self._entries["name"].get().strip()
        if not name:
            messagebox.showwarning("提示", "请先选择或输入一个 AI 源名称")
            return
        source = self._config_manager.get_ai_source(name)
        if source is None:
            messagebox.showwarning("提示", f"AI 源「{name}」不存在，请先保存")
            return
        api_key = self._config_manager.get_api_key(name) or ""
        self._config_manager.set_current_ai_source(name)
        self._ai_client.configure(
            base_url=source.base_url,
            api_key=api_key,
            model=source.model,
            model_minor=source.model_minor,
            temperature=source.temperature,
            top_p=source.top_p,
            max_tokens=source.max_tokens,
        )
        messagebox.showinfo("切换成功", f"当前 AI 源已切换为「{name}」")

    def _delete_source(self):
        if self._current_source_name and messagebox.askyesno("确认", f"删除 AI 源「{self._current_source_name}」？"):
            self._config_manager.remove_ai_source(self._current_source_name)
            self._clear_form()
            self._current_source_name = None
            self._refresh_source_list()

    def _test_connection(self):
        name = self._entries["name"].get().strip()
        base_url = self._entries["base_url"].get().strip()
        api_key = self._entries["api_key"].get().strip()
        if not base_url:
            messagebox.showwarning("提示", "请填写 Base URL")
            return

        self._ai_client.configure(base_url=base_url, api_key=api_key)
        result = self._ai_client.test_connection()
        if result["success"]:
            models_str = "\n".join(result.get("models", [])[:10])
            messagebox.showinfo("连接成功", f"连接成功！\n可用模型（前10个）:\n{models_str}")
        else:
            messagebox.showerror("连接失败", result.get("error", "未知错误"))

    def _clear_form(self):
        for entry in self._entries.values():
            entry.delete(0, "end")
        self._temp_scale.set(100)
        self._top_p_scale.set(90)
        self._max_tokens_scale.set(2048)

    def _save_global_settings(self):
        cfg = self._config_manager.load_app_config()
        cfg.tool_enabled = self._global_tool_enabled.get()
        cfg.log_level = self._global_log_level.get()
        try:
            cfg.log_retention_days = int(self._global_log_days.get())
        except ValueError:
            pass
        try:
            cfg.max_tool_rounds = int(self._global_max_rounds.get())
        except ValueError:
            pass
        self._config_manager.save_app_config(cfg)

    def _update_temp_label(self, val):
        self._temp_label.config(text=f"Temperature: {val:.2f}")

    def _update_top_p_label(self, val):
        self._top_p_label.config(text=f"Top-P: {val:.2f}")

    def _update_max_tokens_label(self, val):
        self._max_tokens_label.config(text=f"Max Tokens: {val}")


# ==================== CharacterPanel ★ v2.0 ====================

class CharacterPanel(BasePanel):
    """角色管理面板 — 以角色为唯一个体，固定字段 + MD 简介 + 多阵营标签"""

    def __init__(self, parent, event_bus, logger, project_service: ProjectService):
        self._project_service = project_service
        self._current_char_id: str | None = None
        self._bio_dirty = False
        self._fields_dirty = False
        super().__init__(parent, event_bus, logger)

    def _setup_ui(self):
        """三栏布局：角色列表 | 固定字段 | MD 编辑器"""
        # 顶部工具栏
        toolbar = tk.Frame(self.frame, bg="#f0f0f0", height=36)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)
        tk.Label(toolbar, text="角色管理", bg="#f0f0f0",
                 font=("Microsoft YaHei", 11, "bold")).pack(side="left", padx=8, pady=4)
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *a: self._refresh_character_list())
        tk.Entry(toolbar, textvariable=self._search_var, font=("Microsoft YaHei", 9),
                 width=20).pack(side="left", padx=4)
        tk.Button(toolbar, text="+ 新角色", command=self._create_character,
                  font=("Microsoft YaHei", 9), bg="#0078d4", fg="white").pack(side="left", padx=4)
        tk.Button(toolbar, text="🏷 管理阵营", command=self._show_camp_dialog,
                  font=("Microsoft YaHei", 9)).pack(side="right", padx=4)

        # 主区域
        paned = tk.PanedWindow(self.frame, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # 左：角色列表
        left_frame = tk.Frame(paned, width=200)
        paned.add(left_frame)
        self._char_listbox = tk.Listbox(left_frame, font=("Microsoft YaHei", 10),
                                         exportselection=False)
        self._char_listbox.pack(side="left", fill="both", expand=True)
        self._char_listbox.bind("<<ListboxSelect>>", self._on_character_selected)
        scroll_l = tk.Scrollbar(left_frame, orient="vertical", command=self._char_listbox.yview)
        scroll_l.pack(side="right", fill="y")
        self._char_listbox.configure(yscrollcommand=scroll_l.set)

        # 中：固定字段
        mid_frame = tk.Frame(paned, width=250)
        paned.add(mid_frame)
        tk.Label(mid_frame, text="基本信息", font=("Microsoft YaHei", 10, "bold"),
                 anchor="w").pack(fill="x", padx=8, pady=4)

        fields = [
            ("名称:", "name"),
            ("性别:", "gender"),
            ("生日:", "birthday"),
            ("年龄:", "age"),
        ]
        self._field_vars: dict[str, tk.StringVar] = {}
        for label_text, key in fields:
            row = tk.Frame(mid_frame)
            row.pack(fill="x", padx=8, pady=3)
            tk.Label(row, text=label_text, font=("Microsoft YaHei", 9),
                     width=6, anchor="e").pack(side="left")
            var = tk.StringVar()
            var.trace_add("write", self._on_field_changed)
            self._field_vars[key] = var
            tk.Entry(row, textvariable=var, font=("Microsoft YaHei", 9)).pack(side="left", fill="x", expand=True)

        # 阵营标签
        tk.Label(mid_frame, text="阵营标签:", font=("Microsoft YaHei", 9),
                 anchor="w").pack(fill="x", padx=8, pady=(8, 2))
        self._camp_tags_frame = tk.Frame(mid_frame)
        self._camp_tags_frame.pack(fill="x", padx=8)

        tk.Button(mid_frame, text="💾 保存", command=self._save_fields,
                  font=("Microsoft YaHei", 9), bg="#0078d4", fg="white").pack(pady=8)
        tk.Button(mid_frame, text="🗑 删除角色", command=self._delete_character,
                  font=("Microsoft YaHei", 9), fg="#d32f2f").pack()

        # 右：MD 简介编辑器
        right_frame = tk.Frame(paned, width=500)
        paned.add(right_frame)
        tk.Label(right_frame, text="角色简介 (Markdown)", font=("Microsoft YaHei", 10, "bold"),
                 anchor="w").pack(fill="x", padx=8, pady=4)
        self._bio_text = tk.Text(right_frame, font=("Microsoft YaHei", 10),
                                  wrap="word", undo=True)
        self._bio_text.pack(fill="both", expand=True, padx=8, pady=4)
        self._bio_text.bind("<<Modified>>", self._on_bio_modified)
        tk.Button(right_frame, text="💾 保存简介", command=self._save_bio,
                  font=("Microsoft YaHei", 9), bg="#0078d4", fg="white").pack(pady=4, padx=8, anchor="e")

    def _subscribe_events(self):
        self._event_bus.subscribe("character:created", lambda e: self._refresh_character_list())
        self._event_bus.subscribe("character:updated", lambda e: self._refresh_character_list())
        self._event_bus.subscribe("character:deleted", lambda e: self._on_character_deleted(e))
        # ★ 阵营变更同时刷新标签和角色列表（角色列表显示阵营名称）
        self._event_bus.subscribe("camp:created", lambda e: (self._refresh_camp_tags(), self._refresh_character_list()))
        self._event_bus.subscribe("camp:updated", lambda e: (self._refresh_camp_tags(), self._refresh_character_list()))
        self._event_bus.subscribe("camp:deleted", lambda e: (self._refresh_camp_tags(), self._refresh_character_list()))

    def on_show(self):
        self._refresh_character_list()

    def on_close(self):
        """面板关闭时自动保存"""
        self._auto_save_if_dirty()

    def _auto_save_if_dirty(self):
        """切换角色或离开面板时自动保存未保存的更改"""
        if not self._current_char_id:
            return
        if self._bio_dirty:
            self._save_bio()
        if self._fields_dirty:
            self._save_fields()

    # ── 角色列表 ──
    def _refresh_character_list(self):
        self._char_listbox.delete(0, "end")
        try:
            cs = self._project_service.character_service
            keyword = self._search_var.get().strip()
            chars = cs.search_characters(keyword) if keyword else cs.list_characters()
            # ★ 按第一阵营排序：先获取阵营顺序
            camps = cs.list_camps()
            camp_order = {c.camp_id: i for i, c in enumerate(camps)}
            def _sort_key(ch):
                if not ch.camp_ids:
                    return (9999, ch.name)
                return (camp_order.get(ch.camp_ids[0], 9998), ch.name)
            chars.sort(key=_sort_key)
            for c in chars:
                camps_str = ""
                if c.camp_ids:
                    camp_names = []
                    for cid in c.camp_ids:
                        camp = cs.get_camp(cid)
                        if camp:
                            camp_names.append(camp.name)
                    if camp_names:
                        camps_str = f"  [{', '.join(camp_names)}]"
                self._char_listbox.insert("end", f"{c.name}{camps_str}")
        except Exception:
            pass

    def _on_character_selected(self, event):
        sel = self._char_listbox.curselection()
        if not sel:
            return
        self._auto_save_if_dirty()
        try:
            cs = self._project_service.character_service
            keyword = self._search_var.get().strip()
            chars = cs.search_characters(keyword) if keyword else cs.list_characters()
            # ★ 排序必须与 _refresh_character_list 完全一致，否则索引错位
            camps = cs.list_camps()
            camp_order = {c.camp_id: i for i, c in enumerate(camps)}
            def _sort_key(ch):
                if not ch.camp_ids:
                    return (9999, ch.name)
                return (camp_order.get(ch.camp_ids[0], 9998), ch.name)
            chars.sort(key=_sort_key)
            idx = sel[0]
            if idx < len(chars):
                char = chars[idx]
                self._current_char_id = char.char_id
                full = cs.get_character(char.char_id)
                if full:
                    self._field_vars["name"].set(full.name)
                    self._field_vars["gender"].set(full.gender)
                    self._field_vars["birthday"].set(full.birthday)
                    self._field_vars["age"].set(full.age)
                    self._bio_text.delete("1.0", "end")
                    self._bio_text.insert("1.0", full.bio)
                    self._bio_text.edit_modified(False)
                    self._bio_dirty = False
                    self._fields_dirty = False
                    self._refresh_camp_tags()
        except Exception:
            pass

    def _create_character(self):
        dialog = tk.Toplevel(self.frame)
        dialog.title("创建角色")
        dialog.geometry("300x120")
        dialog.transient(self.frame)
        dialog.grab_set()
        tk.Label(dialog, text="角色名称:", font=("Microsoft YaHei", 10)).pack(padx=16, pady=8)
        name_var = tk.StringVar()
        entry = tk.Entry(dialog, textvariable=name_var, font=("Microsoft YaHei", 10))
        entry.pack(padx=16, fill="x")
        entry.focus_set()

        def _do_create():
            name = name_var.get().strip()
            if name:
                try:
                    self._project_service.character_service.create_character(name)
                    self._refresh_character_list()
                except ValueError as e:
                    messagebox.showerror("错误", str(e))
            dialog.destroy()

        entry.bind("<Return>", lambda e: _do_create())
        tk.Button(dialog, text="创建", command=_do_create,
                  font=("Microsoft YaHei", 10), bg="#0078d4", fg="white").pack(pady=8)

    def _delete_character(self):
        if not self._current_char_id:
            return
        cs = self._project_service.character_service
        char = cs.get_character(self._current_char_id)
        if char and messagebox.askyesno("确认删除", f"确定要删除角色「{char.name}」吗？"):
            cs.delete_character(self._current_char_id)
            self._current_char_id = None
            self._refresh_character_list()

    def _on_character_deleted(self, event):
        if event.data.get("char_id") == self._current_char_id:
            self._current_char_id = None
            self._refresh_character_list()

    # ── 固定字段 ──
    def _on_field_changed(self, *args):
        self._fields_dirty = True

    def _save_fields(self):
        if not self._current_char_id:
            return
        try:
            cs = self._project_service.character_service
            cs.update_character(
                self._current_char_id,
                name=self._field_vars["name"].get(),
                gender=self._field_vars["gender"].get(),
                birthday=self._field_vars["birthday"].get(),
                age=self._field_vars["age"].get(),
            )
            self._fields_dirty = False
            self._refresh_character_list()
        except ValueError as e:
            messagebox.showerror("错误", str(e))

    # ── 阵营标签 ──
    def _refresh_camp_tags(self):
        for w in self._camp_tags_frame.winfo_children():
            w.destroy()
        if not self._current_char_id:
            return
        cs = self._project_service.character_service
        char = cs.get_character(self._current_char_id)
        if not char:
            return
        for camp_id in char.camp_ids:
            camp = cs.get_camp(camp_id)
            if camp:
                tag_frame = tk.Frame(self._camp_tags_frame, bg="#0078d4")
                tag_frame.pack(side="left", padx=2)
                tk.Label(tag_frame, text=camp.name, bg="#0078d4", fg="white",
                         font=("Microsoft YaHei", 8), padx=4, pady=1).pack(side="left")
                tk.Label(tag_frame, text="×", bg="#0078d4", fg="white",
                         font=("Microsoft YaHei", 9, "bold"), cursor="hand2",
                         padx=2).pack(side="left")
                # click to remove
                tag_frame.bind("<Button-1>", lambda e, cid=camp_id: self._remove_camp(cid))
                for child in tag_frame.winfo_children():
                    child.bind("<Button-1>", lambda e, cid=camp_id: self._remove_camp(cid))

        # add camp button
        tk.Button(self._camp_tags_frame, text="+", font=("Microsoft YaHei", 8),
                  padx=4, command=self._show_camp_dialog).pack(side="left", padx=2)

    def _remove_camp(self, camp_id):
        if not self._current_char_id:
            return
        cs = self._project_service.character_service
        char = cs.get_character(self._current_char_id)
        if char:
            new_ids = [cid for cid in char.camp_ids if cid != camp_id]
            cs.update_character(self._current_char_id, camp_ids=new_ids)
            self._refresh_camp_tags()

    def _show_camp_dialog(self):
        """阵营管理对话框 — ★ 支持上下调整顺序"""
        dialog = tk.Toplevel(self.frame)
        dialog.title("管理阵营")
        dialog.geometry("480x400")
        dialog.transient(self.frame)
        dialog.grab_set()

        cs = self._project_service.character_service

        # 列表区（含排序按钮）
        list_outer = tk.Frame(dialog)
        list_outer.pack(fill="both", expand=True, padx=8, pady=8)
        camp_list = tk.Listbox(list_outer, font=("Microsoft YaHei", 10))
        camp_list.pack(side="left", fill="both", expand=True)
        sl = tk.Scrollbar(list_outer, orient="vertical", command=camp_list.yview)
        sl.pack(side="left", fill="y")
        camp_list.configure(yscrollcommand=sl.set)

        # ★ 排序按钮
        order_frame = tk.Frame(list_outer)
        order_frame.pack(side="left", fill="y", padx=4)
        tk.Button(order_frame, text="▲", font=("Microsoft YaHei", 10),
                  width=3, command=lambda: _move_camp(-1)).pack(pady=2)
        tk.Button(order_frame, text="▼", font=("Microsoft YaHei", 10),
                  width=3, command=lambda: _move_camp(1)).pack(pady=2)

        def _get_camp_ids_display_order():
            """获取当前列表框中显示的 camp_id 顺序"""
            ids = []
            for i in range(camp_list.size()):
                text = camp_list.get(i)
                # text format: "青云宗  — 简介..."
                name = text.split("  —")[0].strip()
                for c in cs.list_camps():
                    if c.name == name:
                        ids.append(c.camp_id)
                        break
            return ids

        def _move_camp(delta: int):
            """移动选中阵营的显示顺序"""
            sel = camp_list.curselection()
            if not sel:
                return
            idx = sel[0]
            new_idx = idx + delta
            if new_idx < 0 or new_idx >= camp_list.size():
                return
            # 获取完整顺序ID列表
            ids = _get_camp_ids_display_order()
            # 交换
            ids[idx], ids[new_idx] = ids[new_idx], ids[idx]
            # 保存
            cs.reorder_camps(ids)
            _refresh_camp_list()
            # 保持选中
            camp_list.selection_set(new_idx)
            self._refresh_camp_tags()
            self._refresh_character_list()

        def _refresh_camp_list():
            camp_list.delete(0, "end")
            for c in cs.list_camps():
                desc_preview = c.description[:30] + "..." if len(c.description) > 30 else c.description
                camp_list.insert("end", f"{c.name}  — {desc_preview}")

        _refresh_camp_list()

        # 编辑区
        edit_frame = tk.Frame(dialog)
        edit_frame.pack(fill="x", padx=8, pady=4)
        tk.Label(edit_frame, text="名称:", font=("Microsoft YaHei", 9)).grid(row=0, column=0, sticky="e", padx=4)
        name_var = tk.StringVar()
        tk.Entry(edit_frame, textvariable=name_var, font=("Microsoft YaHei", 9)).grid(row=0, column=1, sticky="ew", pady=2)
        tk.Label(edit_frame, text="简介:", font=("Microsoft YaHei", 9)).grid(row=1, column=0, sticky="e", padx=4)
        desc_var = tk.StringVar()
        tk.Entry(edit_frame, textvariable=desc_var, font=("Microsoft YaHei", 9)).grid(row=1, column=1, sticky="ew", pady=2)
        edit_frame.columnconfigure(1, weight=1)

        def _on_camp_select(event):
            sel = camp_list.curselection()
            if sel:
                camps = cs.list_camps()
                if sel[0] < len(camps):
                    c = camps[sel[0]]
                    name_var.set(c.name)
                    desc_var.set(c.description)

        camp_list.bind("<<ListboxSelect>>", _on_camp_select)

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(fill="x", padx=8, pady=8)

        def _save_camp():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("提示", "请输入阵营名称")
                return
            try:
                sel = camp_list.curselection()
                camps = cs.list_camps()
                if sel and sel[0] < len(camps):
                    cs.update_camp(camps[sel[0]].camp_id, name=name, description=desc_var.get())
                else:
                    cs.create_camp(name, desc_var.get())
                _refresh_camp_list()
                name_var.set("")
                desc_var.set("")
                self._refresh_camp_tags()
                self._refresh_character_list()
                # ★ auto-add new camp to current character
                if self._current_char_id:
                    char = cs.get_character(self._current_char_id)
                    if char:
                        new_camps = cs.list_camps()
                        for camp in new_camps:
                            if camp.name == name and camp.camp_id not in char.camp_ids:
                                cs.update_character(self._current_char_id,
                                    camp_ids=char.camp_ids + [camp.camp_id])
                                self._refresh_camp_tags()
                                break
            except Exception as e:
                messagebox.showerror("错误", f"保存阵营失败: {e}")

        def _delete_camp():
            sel = camp_list.curselection()
            if sel:
                camps = cs.list_camps()
                if sel[0] < len(camps) and messagebox.askyesno("确认删除", f"确定要删除阵营「{camps[sel[0]].name}」吗？"):
                    try:
                        cs.delete_camp(camps[sel[0]].camp_id)
                        _refresh_camp_list()
                        self._refresh_camp_tags()
                        self._refresh_character_list()
                    except Exception as e:
                        messagebox.showerror("错误", f"删除阵营失败: {e}")

        tk.Button(btn_frame, text="新建/更新", command=_save_camp,
                  font=("Microsoft YaHei", 9), bg="#0078d4", fg="white").pack(side="left", padx=4)
        tk.Button(btn_frame, text="删除选中", command=_delete_camp,
                  font=("Microsoft YaHei", 9), fg="#d32f2f").pack(side="left", padx=4)
        tk.Button(btn_frame, text="关闭", command=dialog.destroy,
                  font=("Microsoft YaHei", 9)).pack(side="right", padx=4)

    # ── 简介编辑器 ──
    def _on_bio_modified(self, event=None):
        if self._bio_text.edit_modified():
            self._bio_dirty = True
            self._bio_text.edit_modified(False)

    def _save_bio(self):
        if not self._current_char_id or not self._bio_dirty:
            return
        try:
            cs = self._project_service.character_service
            bio = self._bio_text.get("1.0", "end-1c")
            cs.update_character(self._current_char_id, bio=bio)
            self._bio_dirty = False
        except ValueError as e:
            messagebox.showerror("错误", str(e))


# ==================== ForeshadowPanel ★ v2.0 ====================

class ForeshadowPanel(BasePanel):
    """伏笔管理面板 — 条目式 CRUD，支持隐藏/显示，系统自动排序"""

    def __init__(self, parent, event_bus, logger, project_service: ProjectService):
        self._project_service = project_service
        self._show_hidden = tk.BooleanVar(value=True)
        super().__init__(parent, event_bus, logger)

    def _setup_ui(self):
        """工具栏 + 伏笔列表 + 底部输入区"""
        # 工具栏
        toolbar = tk.Frame(self.frame, bg="#f0f0f0", height=36)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)
        tk.Label(toolbar, text="伏笔管理", bg="#f0f0f0",
                 font=("Microsoft YaHei", 11, "bold")).pack(side="left", padx=8, pady=4)
        tk.Checkbutton(toolbar, text="显示已隐藏", variable=self._show_hidden,
                       bg="#f0f0f0", font=("Microsoft YaHei", 9),
                       command=self._refresh_list).pack(side="right", padx=8)

        # 列表区
        list_frame = tk.Frame(self.frame)
        list_frame.pack(fill="both", expand=True)
        self._foreshadow_tree = ttk.Treeview(list_frame,
            columns=("content", "status"), show="headings", selectmode="browse")
        self._foreshadow_tree.heading("content", text="伏笔内容")
        self._foreshadow_tree.heading("status", text="状态")
        self._foreshadow_tree.column("content", width=600)
        self._foreshadow_tree.column("status", width=80, anchor="center")
        scroll_f = tk.Scrollbar(list_frame, orient="vertical", command=self._foreshadow_tree.yview)
        self._foreshadow_tree.configure(yscrollcommand=scroll_f.set)
        self._foreshadow_tree.pack(side="left", fill="both", expand=True)
        scroll_f.pack(side="right", fill="y")
        self._foreshadow_tree.bind("<Double-1>", self._on_edit)
        self._foreshadow_tree.bind("<Button-3>", self._on_right_click)

        # 底部输入栏
        input_frame = tk.Frame(self.frame, bg="#f0f0f0", height=40)
        input_frame.pack(fill="x", side="bottom")
        input_frame.pack_propagate(False)
        self._foreshadow_input = tk.Text(input_frame, font=("Microsoft YaHei", 10),
                                          height=2, wrap="word")
        self._foreshadow_input.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        tk.Button(input_frame, text="+ 添加", command=self._add_foreshadow,
                  font=("Microsoft YaHei", 9), bg="#0078d4", fg="white").pack(side="right", padx=4, pady=4)

        # 右键菜单
        self._context_menu = tk.Menu(self.frame, tearoff=0)
        self._context_menu.add_command(label="编辑内容", command=self._on_edit)
        self._context_menu.add_command(label="隐藏/显示", command=self._on_toggle)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="删除", command=self._on_delete)

    def _subscribe_events(self):
        self._event_bus.subscribe("foreshadow:created", lambda e: self._refresh_list())
        self._event_bus.subscribe("foreshadow:updated", lambda e: self._refresh_list())
        self._event_bus.subscribe("foreshadow:deleted", lambda e: self._refresh_list())
        self._event_bus.subscribe("foreshadow:toggled", lambda e: self._refresh_list())

    def on_show(self):
        self._refresh_list()

    def _refresh_list(self):
        self._foreshadow_tree.delete(*self._foreshadow_tree.get_children())
        try:
            fs = self._project_service.foreshadow_service
            include = self._show_hidden.get()
            items = fs.list_foreshadows(include_hidden=include)
            for f in items:
                status = "👁 隐藏" if f.hidden else "◎"
                tags = ("hidden",) if f.hidden else ()
                self._foreshadow_tree.insert("", "end", values=(f.content, status), tags=tags)
            self._foreshadow_tree.tag_configure("hidden", foreground="gray")
            # 更新计数
            count = len(items)
            hidden_count = sum(1 for f_ in items if f_.hidden)
            self.logger.log(f"伏笔: {count} 条 (含 {hidden_count} 隐藏)", "ForeshadowPanel", "INFO")
        except Exception:
            pass

    def _add_foreshadow(self):
        content = self._foreshadow_input.get("1.0", "end-1c").strip()
        if not content:
            return
        try:
            self._project_service.foreshadow_service.add_foreshadow(content)
            self._foreshadow_input.delete("1.0", "end")
            self._refresh_list()
        except ValueError as e:
            messagebox.showerror("错误", str(e))

    def _get_selected_id(self):
        sel = self._foreshadow_tree.selection()
        if not sel:
            return None
        fs = self._project_service.foreshadow_service
        include = self._show_hidden.get()
        items = fs.list_foreshadows(include_hidden=include)
        idx = self._foreshadow_tree.index(sel[0])
        if idx < len(items):
            return items[idx].foreshadow_id
        return None

    def _on_edit(self, event=None):
        fid = self._get_selected_id()
        if not fid:
            return
        fs = self._project_service.foreshadow_service
        f = fs.get_foreshadow(fid)
        if not f:
            return
        dialog = tk.Toplevel(self.frame)
        dialog.title("编辑伏笔")
        dialog.geometry("400x200")
        dialog.transient(self.frame)
        dialog.grab_set()
        text = tk.Text(dialog, font=("Microsoft YaHei", 10), wrap="word")
        text.insert("1.0", f.content)
        text.pack(fill="both", expand=True, padx=8, pady=8)

        def _save():
            new_content = text.get("1.0", "end-1c").strip()
            if new_content:
                fs.update_foreshadow(fid, new_content)
                self._refresh_list()
            dialog.destroy()

        tk.Button(dialog, text="保存", command=_save,
                  font=("Microsoft YaHei", 10), bg="#0078d4", fg="white").pack(pady=4)

    def _on_toggle(self, event=None):
        fid = self._get_selected_id()
        if fid:
            self._project_service.foreshadow_service.toggle_hidden(fid)
            self._refresh_list()

    def _on_delete(self, event=None):
        fid = self._get_selected_id()
        if fid and messagebox.askyesno("确认删除", "确定要删除这条伏笔吗？"):
            try:
                self._project_service.foreshadow_service.delete_foreshadow(fid)
                self._refresh_list()
            except ValueError as e:
                messagebox.showerror("错误", str(e))

    def _on_right_click(self, event):
        sel = self._foreshadow_tree.identify_row(event.y)
        if sel:
            self._foreshadow_tree.selection_set(sel)
            self._context_menu.post(event.x_root, event.y_root)

    def get_ai_context(self) -> str:
        """供 ChatPanel 调用：获取未隐藏伏笔的格式化文本"""
        try:
            return self._project_service.foreshadow_service.get_ai_context()
        except Exception:
            return ""


# ==================== StatusPanel ★ v2.0 ====================

class StatusPanel(BasePanel):
    """创作状态面板 — 展示进度 + AI 一键生成"""

    def __init__(self, parent, event_bus, logger, project_service: ProjectService):
        self._project_service = project_service
        self._ai_client: "AIClient | None" = None
        self._config_manager: "ConfigManager | None" = None
        self._status_content = tk.StringVar(value="（暂无数据，请选择项目后使用 AI 生成）")
        super().__init__(parent, event_bus, logger)

    def set_ai_client(self, ai_client: "AIClient"):
        self._ai_client = ai_client

    def set_config_manager(self, config_manager: "ConfigManager"):
        """注入配置管理器（用于自动加载 AI 配置）"""
        self._config_manager = config_manager

    def _setup_ui(self):
        """状态展示 + AI 生成按钮"""
        # 标题
        tk.Label(self.frame, text="创作状态", font=("Microsoft YaHei", 12, "bold"),
                 anchor="w").pack(fill="x", padx=12, pady=8)

        # Token 警告提示
        warning_frame = tk.Frame(self.frame, bg="#fff3cd", bd=1, relief="solid")
        warning_frame.pack(fill="x", padx=12, pady=4)
        tk.Label(warning_frame, text="⚠️ 提示：AI 生成功能需要将大量创作数据(大纲 L1~L4 + 角色 + 设定 + 伏笔)"
                 "上传至 AI 进行分析，预计消耗较多 token。请确认后使用。",
                 bg="#fff3cd", fg="#856404", font=("Microsoft YaHei", 9),
                 wraplength=700, justify="left").pack(padx=8, pady=6)

        # 状态内容区
        content_frame = tk.Frame(self.frame)
        content_frame.pack(fill="both", expand=True, padx=12, pady=4)
        self._status_display = tk.Text(content_frame, font=("Microsoft YaHei", 10),
                                        wrap="word", state="disabled")
        self._status_display.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(content_frame, orient="vertical", command=self._status_display.yview)
        scroll.pack(side="right", fill="y")
        self._status_display.configure(yscrollcommand=scroll.set)

        # AI 生成按钮
        btn_frame = tk.Frame(self.frame)
        btn_frame.pack(fill="x", padx=12, pady=8)
        tk.Button(btn_frame, text="🤖 AI 一键生成创作状态",
                  font=("Microsoft YaHei", 11), bg="#0078d4", fg="white",
                  padx=16, pady=6, command=self._on_ai_generate).pack()

    def on_show(self):
        # ★ 加载已保存的创作状态（持久化）
        self._load_saved_status()

    def _load_saved_status(self):
        """从项目目录加载已保存的创作状态"""
        try:
            ps = self._project_service
            if not ps.get_current_project():
                self._set_status_content("（暂无项目，请先在大纲面板创建或选择项目）")
                return
            proj_dir = ps._get_project_dir()
            if proj_dir:
                status_file = proj_dir / "status.md"
                if status_file.exists():
                    with open(status_file, "r", encoding="utf-8") as f:
                        self._set_status_content(f.read())
                else:
                    self._show_basic_stats()
        except Exception:
            self._show_basic_stats()

    def _save_status(self, content: str):
        """保存创作状态到项目目录"""
        try:
            ps = self._project_service
            proj_dir = ps._get_project_dir()
            if proj_dir:
                (proj_dir / "status.md").write_text(content, encoding="utf-8")
        except Exception:
            pass

    def _show_basic_stats(self):
        """显示基础统计"""
        try:
            ps = self._project_service
            if not ps.get_current_project():
                return
            tree = ps.get_outline_tree()
            total_nodes = len(tree)
            completed = sum(1 for n in tree if n.status.value == "completed")
            l5_nodes = [n for n in tree if n.level.value == 5]
            total_words = sum(n.word_count for n in l5_nodes)
            lines = [
                f"📊 {ps.get_current_project()} — 大纲节点 {total_nodes} | 完成 {completed} | 正文 {len(l5_nodes)}章 | {total_words:,}字\n",
                "💡 点击下方按钮，让 AI 分析进度并给出创作建议。",
            ]
            self._set_status_content("\n".join(lines))
        except Exception:
            pass

    def _refresh_basic_status(self):
        self._load_saved_status()

    def _set_status_content(self, text: str):
        self._status_display.config(state="normal")
        self._status_display.delete("1.0", "end")
        self._status_display.insert("1.0", text)
        self._status_display.config(state="disabled")

    def _on_ai_generate(self):
        if not self._ai_client:
            messagebox.showwarning("未配置 AI", "请先在配置面板中配置并测试 AI 连接。")
            return

        # ★ 自动从 ConfigManager 加载已保存的 AI 源配置
        if not self._ai_client.is_configured and self._config_manager:
            source = self._config_manager.get_current_ai_source()
            if source:
                api_key = self._config_manager.get_api_key(source.name) or ""
                self._ai_client.configure(
                    base_url=source.base_url,
                    api_key=api_key,
                    model=source.model,
                    model_minor=source.model_minor,
                    temperature=source.temperature,
                    top_p=source.top_p,
                    max_tokens=source.max_tokens,
                )

        if not self._ai_client.is_configured:
            messagebox.showwarning("AI 未配置", "请先在配置面板中配置 AI 源，并点击「✅ 设为当前」激活。")
            return

        # Token 消耗确认
        if not messagebox.askyesno(
            "Token 消耗提醒",
            "此功能需要将大量创作数据上传至 AI 进行分析，预计消耗较多 token。\n\n"
            "上传内容：\n"
            "• 大纲 L1~L4（不含正文 L5）\n"
            "• 角色栏所有角色信息\n"
            "• 设定栏所有设定信息\n"
            "• 伏笔栏未隐藏的伏笔信息\n\n"
            "是否继续？"
        ):
            return

        # 收集数据：项目概况 → 大纲树 → 角色 → 设定 → 伏笔
        try:
            ps = self._project_service
            data_parts = []

            all_nodes = ps.get_outline_tree()
            l1_l4 = [n for n in all_nodes if n.level.value <= 4]
            l5_nodes = [n for n in all_nodes if n.level.value == 5]
            completed_l5 = sum(1 for n in l5_nodes if n.status.value == "completed")
            total_words = sum(n.word_count for n in l5_nodes)
            completed_nodes = sum(1 for n in all_nodes if n.status.value == "completed")

            # 1. 项目概况
            data_parts.append(f"【项目概况】\n项目: {ps.get_current_project()}\n"
                f"大纲节点: {len(l1_l4)} (已完成 {completed_nodes})\n"
                f"正文: {len(l5_nodes)} 章 ({completed_l5} 已完成) | 总字数: {total_words:,}")

            # 2. 大纲 L1~L4（含完整内容）
            if l1_l4:
                data_parts.append("\n【大纲树】")
                for n in sorted(l1_l4, key=lambda x: (x.level.value, x.order)):
                    level_name = {1: "L1-大纲", 2: "L2-卷纲", 3: "L3-简纲", 4: "L4-章纲"}.get(n.level.value, "")
                    status_icon = {"completed": "✓", "in_progress": "●", "todo": "○", "ignored": "⊘"}.get(n.status.value, "○")
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

            # ★ 精简提示词：针对作者给出方向性建议，限制字数
            prompt = (
                "你是一位资深网文编辑。以下是作者当前的创作进度，请用 200~400 字给出针对性建议：\n\n"
                f"项目: {ps.get_current_project()}\n"
                f"大纲节点: {len(l1_l4)} | 正文: {len(l5_nodes)}章 ({completed_l5}完成) | 总字数: {total_words:,}\n\n"
                f"{full_data}\n\n"
                "请直接输出（不要客套、不要标题、不要列表格式）：\n"
                "1. 当前进度的卡点或薄弱环节是什么？\n"
                "2. 下一步最应该做什么（给 1~2 个具体方向）？\n"
                "3. 用一句话总结当前创作状态。"
            )

            messages = [ChatMessage("user", prompt)]

            # 调用 AI
            self._set_status_content("⏳ AI 正在分析创作数据...")

            import threading
            result_buffer = []

            def _generate():
                try:
                    for chunk in self._ai_client.chat_stream(messages):
                        if chunk:
                            result_buffer.append(chunk)
                    full_text = "".join(result_buffer)
                    # ★ 持久化保存，并添加时间戳
                    from datetime import datetime
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                    saved = f"🕐 {ts} | 共 {total_words:,} 字\n\n{full_text}"
                    self.frame.after(0, lambda: self._set_status_content(saved))
                    self._save_status(saved)
                except Exception as e:
                    self.frame.after(0, lambda: self._set_status_content(f"❌ 生成失败: {e}"))

            threading.Thread(target=_generate, daemon=True).start()

        except Exception as e:
            self._set_status_content(f"❌ 数据收集失败: {e}")

    def get_ai_context(self) -> str:
        """供 ChatPanel 调用：获取当前状态摘要"""
        try:
            content = self._status_display.get("1.0", "end-1c")
            if content and "暂无数据" not in content and "AI 正在分析" not in content:
                return f"【当前创作状态】\n{content}"
        except Exception:
            pass
        return ""


# ==================== LogPanel ★ v2.0 修复 ====================

class LogPanel(BasePanel):
    """日志查看面板 — v2.0: 加载历史日志 + 移除显示上限"""

    def __init__(self, parent, event_bus, logger, config_manager: ConfigManager):
        self._config_manager = config_manager
        self._log_entries = []  # [(timestamp, level, module, message)]
        self._log_loaded = False  # ★ 历史日志是否已加载
        super().__init__(parent, event_bus, logger)

    def _setup_ui(self):
        # 过滤器栏
        filter_frame = tk.Frame(self.frame, bg="#f0f0f0", height=36)
        filter_frame.pack(fill="x")
        filter_frame.pack_propagate(False)

        tk.Label(filter_frame, text="级别:", bg="#f0f0f0", font=("Microsoft YaHei", 9)).pack(side="left", padx=4, pady=4)
        self._level_var = tk.StringVar(value="全部")
        ttk.OptionMenu(filter_frame, self._level_var, "全部", "全部", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL",
                       command=lambda v: self._apply_filter()).pack(side="left", padx=4)

        tk.Button(filter_frame, text="🔄 刷新", command=self._apply_filter,
                  font=("Microsoft YaHei", 9)).pack(side="left", padx=4)
        tk.Button(filter_frame, text="📤 导出", command=self._export_logs,
                  font=("Microsoft YaHei", 9)).pack(side="right", padx=4)

        # 日志列表
        columns = ("time", "level", "module", "message")
        self._log_tree = ttk.Treeview(self.frame, columns=columns, show="headings",
                                      selectmode="browse")
        self._log_tree.heading("time", text="时间")
        self._log_tree.heading("level", text="级别")
        self._log_tree.heading("module", text="模块")
        self._log_tree.heading("message", text="消息")

        self._log_tree.column("time", width=140)
        self._log_tree.column("level", width=70)
        self._log_tree.column("module", width=120)
        self._log_tree.column("message", width=500)

        scrollbar = tk.Scrollbar(self.frame, orient="vertical", command=self._log_tree.yview)
        self._log_tree.configure(yscrollcommand=scrollbar.set)

        self._log_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _subscribe_events(self):
        self._event_bus.subscribe("log:new", self._on_new_log)

    # ★ v2.0: 切换到日志面板时加载历史日志
    def on_show(self):
        if not self._log_loaded:
            try:
                self._load_history_logs()
            except Exception:
                pass  # 加载失败不影响面板切换
            self._log_loaded = True

    def _load_history_logs(self):
        """★ v2.0: 从磁盘加载当日历史日志"""
        import glob
        try:
            work_dir = self._config_manager.get_work_dir()
            today = datetime.now().strftime("%Y-%m-%d")
            log_dir = os.path.join(work_dir, "logs", today)
            if not os.path.exists(log_dir):
                return
            count = 0
            for log_file in sorted(glob.glob(os.path.join(log_dir, "*.log"))):
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            # 解析格式: [2025-07-20 14:30:01] [INFO] [Module] message
                            try:
                                parts = line.split("] ", 3)
                                if len(parts) >= 4:
                                    ts = parts[0].lstrip("[")
                                    level = parts[1].lstrip("[")
                                    module = parts[2].lstrip("[")
                                    message = parts[3]
                                    self._log_entries.append((ts, level, module, message))
                                    count += 1
                            except (IndexError, ValueError):
                                pass
                except Exception:
                    pass
            if count > 0:
                self._apply_filter()
        except Exception:
            pass  # 静默失败，不影响面板使用

    def _on_new_log(self, event):
        """收到新日志"""
        d = event.data
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_entries.append((ts, d.get("level", "INFO"), d.get("module_id", ""), d.get("message", "")))
        # ★ v2.0: 内存上限 500 → 2000
        if len(self._log_entries) > 2000:
            self._log_entries = self._log_entries[-2000:]
        self._apply_filter()

    def _apply_filter(self):
        """★ v2.0: 移除 [-200:] 截断，完整展示"""
        level_filter = self._level_var.get()
        self._log_tree.delete(*self._log_tree.get_children())
        for entry in reversed(self._log_entries):
            if level_filter != "全部" and entry[1] != level_filter:
                continue
            self._log_tree.insert("", "end", values=entry)

    def _export_logs(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                for entry in self._log_entries:
                    f.write(" ".join(entry) + "\n")
            messagebox.showinfo("导出完成", f"已导出到 {file_path}")
