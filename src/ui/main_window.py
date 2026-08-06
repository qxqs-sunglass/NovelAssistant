"""
主窗口 — tkinter 根窗口 + 侧边栏导航 + 内容区面板切换

布局:
    ┌──────────────────────────────────────────┐
    │  小说创作助手                    [_][□][×] │
    ├────────┬─────────────────────────────────┤
    │ 💬 对话 │         内容面板区域              │
    │ 📖 大纲 │       (动态切换显示)             │
    │ ⚙ 设定 │                                  │
    │ 🔧 配置 │                                  │
    │ 📋 日志 │                                  │
    ├────────┴─────────────────────────────────┤
    │ 状态栏: 项目: - | AI: 未配置                │
    └──────────────────────────────────────────┘
"""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

from src.core.config_manager import ConfigManager
from src.core.event_bus import EventBus
from src.core.logger import Logger
from src.services.ai_client import AIClient
from src.services.session_manager import SessionManager
from src.services.project_service import ProjectService
from src.ui.panels import (
    BasePanel, ChatPanel, OutlinePanel, SettingsPanel, ConfigPanel, LogPanel,
    CharacterPanel, ForeshadowPanel, StatusPanel,
)
from src.services.tool_registry import create_tools


class MainWindow:
    """应用程序主窗口"""

    NAV_ITEMS = [
        ("chat",        "💬 对话",    "ChatPanel"),
        ("outline",     "📖 大纲",    "OutlinePanel"),
        ("characters",  "👤 角色",    "CharacterPanel"),
        ("foreshadow",  "🔮 伏笔",    "ForeshadowPanel"),
        ("settings",    "⚙ 设定",     "SettingsPanel"),
        ("status",      "📊 状态",    "StatusPanel"),
        ("config",      "🔧 配置",    "ConfigPanel"),
        ("log",         "📋 日志",    "LogPanel"),
    ]

    SIDEBAR_WIDTH = 170
    MIN_WIDTH = 900
    MIN_HEIGHT = 500

    def __init__(
        self,
        config_manager: ConfigManager,
        event_bus: EventBus,
        logger: Logger,
        ai_client: AIClient,
        session_manager: SessionManager,
        project_service: ProjectService,
    ):
        self._config_manager = config_manager
        self._event_bus = event_bus
        self._logger = logger
        self._ai_client = ai_client
        self._session_manager = session_manager
        self._project_service = project_service

        # 窗口
        self._root = tk.Tk()
        self._root.title("小说创作助手")
        self._root.minsize(self.MIN_WIDTH, self.MIN_HEIGHT)

        # 从配置加载窗口尺寸
        app_config = config_manager.load_app_config()
        w = app_config.window_width
        h = app_config.window_height
        self._root.geometry(f"{w}x{h}")

        # 关闭协议
        self._root.protocol("WM_DELETE_WINDOW", self.shutdown)

        # 导航按钮引用
        self._nav_buttons: dict[str, tk.Button] = {}
        self._current_panel_id: str = ""
        self._panels: dict[str, BasePanel] = {}

        # 状态栏
        self._status_var = tk.StringVar(value="就绪")

    def run(self) -> None:
        """初始化布局并启动主循环"""
        self._setup_layout()
        self._subscribe_events()
        # 默认显示对话面板
        self.switch_panel("chat")
        self._root.mainloop()

    def shutdown(self) -> None:
        """安全关闭"""
        for panel in self._panels.values():
            try:
                panel.on_close()
            except Exception:
                pass
        self._root.destroy()

    def switch_panel(self, panel_id: str) -> None:
        """切换右侧内容区面板"""
        if panel_id == self._current_panel_id:
            return

        # 自动保存当前面板的编辑内容
        if self._current_panel_id and self._current_panel_id in self._panels:
            current_panel = self._panels[self._current_panel_id]
            if hasattr(current_panel, "_auto_save_if_dirty"):
                current_panel._auto_save_if_dirty()

        # 隐藏当前面板
        if self._current_panel_id and self._current_panel_id in self._panels:
            self._panels[self._current_panel_id].frame.pack_forget()

        # 显示目标面板
        if panel_id in self._panels:
            panel = self._panels[panel_id]
            panel.frame.pack(side="right", fill="both", expand=True)
            panel.on_show()
            self._current_panel_id = panel_id

        # 更新导航高亮
        self._highlight_nav(panel_id)

    def _setup_layout(self) -> None:
        """构建布局结构"""
        # 侧边栏
        sidebar = tk.Frame(self._root, width=self.SIDEBAR_WIDTH, bg="#2c2c2c")
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # 侧边栏标题
        title_label = tk.Label(
            sidebar, text="导航", font=("Microsoft YaHei", 12, "bold"),
            bg="#2c2c2c", fg="#ffffff", anchor="w", padx=16, pady=12,
        )
        title_label.pack(fill="x")

        # 导航按钮
        for panel_id, label_text, _ in self.NAV_ITEMS:
            btn = tk.Button(
                sidebar, text=label_text, anchor="w", padx=16, pady=8,
                relief="flat", font=("Microsoft YaHei", 11),
                bg="#2c2c2c", fg="#cccccc", activebackground="#3c3c3c",
                activeforeground="#ffffff", borderwidth=0,
                command=lambda pid=panel_id: self.switch_panel(pid),
            )
            btn.pack(fill="x", ipady=4)
            self._nav_buttons[panel_id] = btn

        # 内容区容器
        self._content_frame = tk.Frame(self._root, bg="#ffffff")
        self._content_frame.pack(side="right", fill="both", expand=True)

        # 状态栏
        status_bar = tk.Frame(self._root, height=24, bg="#e0e0e0")
        status_bar.pack(side="bottom", fill="x")
        status_label = tk.Label(
            status_bar, textvariable=self._status_var, anchor="w", padx=8,
            bg="#e0e0e0", fg="#333333", font=("Microsoft YaHei", 9),
        )
        status_label.pack(fill="x")

        # 创建工具注册表
        tool_registry = create_tools(self._project_service)

        # 创建面板
        chat_panel = ChatPanel(
            self._content_frame, self._event_bus, self._logger,
            self._ai_client, self._session_manager, self._project_service,
        )
        chat_panel.set_tool_registry(tool_registry)
        chat_panel.set_config_manager(self._config_manager)
        self._panels["chat"] = chat_panel
        self._panels["outline"] = OutlinePanel(
            self._content_frame, self._event_bus, self._logger,
            self._project_service,
        )
        self._panels["outline"].set_ai_client(self._ai_client)
        self._panels["outline"].set_config_manager(self._config_manager)
        # ★ v2.0: 新增面板
        self._panels["characters"] = CharacterPanel(
            self._content_frame, self._event_bus, self._logger,
            self._project_service,
        )
        self._panels["foreshadow"] = ForeshadowPanel(
            self._content_frame, self._event_bus, self._logger,
            self._project_service,
        )
        self._panels["settings"] = SettingsPanel(
            self._content_frame, self._event_bus, self._logger,
            self._project_service,
        )
        self._panels["config"] = ConfigPanel(
            self._content_frame, self._event_bus, self._logger,
            self._config_manager, self._ai_client,
        )
        self._panels["log"] = LogPanel(
            self._content_frame, self._event_bus, self._logger,
            self._config_manager,
        )
        # ★ v2.0
        self._panels["status"] = StatusPanel(
            self._content_frame, self._event_bus, self._logger,
            self._project_service,
        )
        self._panels["status"].set_ai_client(self._ai_client)
        self._panels["status"].set_config_manager(self._config_manager)

    def _subscribe_events(self) -> None:
        """订阅全局事件"""
        self._event_bus.subscribe("project:switched", self._on_project_switched)
        self._event_bus.subscribe("config:changed", self._on_config_changed)

    def _on_project_switched(self, event) -> None:
        """项目切换时更新状态栏并保存到配置"""
        name = event.data.get("project_name", "-")
        self._status_var.set(f"项目: {name} | AI: {self._ai_client.current_model or '未配置'}")
        # 仅更新 last_project（有 AI 源时才保存，防止空配置覆盖有效配置）
        try:
            cfg = self._config_manager.load_app_config()
            if cfg.ai_sources or cfg.current_ai_source:
                cfg.last_project = name
                self._config_manager.save_app_config(cfg)
        except Exception:
            pass

    def _on_config_changed(self, event) -> None:
        """配置变更时更新状态栏"""
        self._status_var.set(f"项目: {self._project_service.get_current_project() or '-'} | AI: {self._ai_client.current_model or '未配置'}")

    def _highlight_nav(self, panel_id: str) -> None:
        """高亮当前导航按钮"""
        for pid, btn in self._nav_buttons.items():
            if pid == panel_id:
                btn.config(bg="#0078d4", fg="#ffffff")
            else:
                btn.config(bg="#2c2c2c", fg="#cccccc")
