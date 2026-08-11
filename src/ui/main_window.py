"""主窗口 — QMainWindow + 左侧导航 + QStackedWidget 面板切换（v3.0）"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget,
    QLabel, QStatusBar, QSplitter,
)
from PySide6.QtCore import Qt

from src.core.event_bus import EventBus
from src.core.logger import Logger
from src.core.config_manager import ConfigManager
from src.services.ai_client import AIClient
from src.services.session_manager import SessionManager
from src.services.project_service import ProjectService


class MainWindow(QMainWindow):
    """应用程序主窗口

    结构:
      ┌──────────┬──────────────────────────┐
      │ 侧边导航  │  QStackedWidget（面板区） │
      │ (QList)  │                          │
      │  💬 对话  │  ← 当前面板               │
      │  📖 大纲  │                          │
      │  👤 角色  │                          │
      │  ...     │                          │
      └──────────┴──────────────────────────┘
    """

    NAV_ITEMS = [
        ("💬 对话",    "chat"),
        ("📖 大纲",    "outline"),
        ("👤 角色",    "characters"),
        ("🔮 伏笔",    "foreshadow"),
        ("⚙ 设定",     "settings"),
        ("📊 状态",    "status"),
        ("🔧 配置",    "config"),
        ("📋 日志",    "log"),
    ]

    SIDEBAR_WIDTH = 160
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
        super().__init__()
        self._config_manager = config_manager
        self._event_bus = event_bus
        self._logger = logger
        self._ai_client = ai_client
        self._session_manager = session_manager
        self._project_service = project_service

        self.setWindowTitle("小说创作助手 v3.0")
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)

        # Window size from config
        app_config = config_manager.load_app_config()
        self.resize(app_config.window_width, app_config.window_height)

        # Panel registry
        self._panels: dict[str, object] = {}  # panel_id → BasePanel
        self._current_panel_id: str = ""

        # Status bar
        self._status_label = QLabel("就绪")
        self.statusBar().addWidget(self._status_label)

        self._setup_ui()
        self._subscribe_events()

    def _setup_ui(self):
        """构建主窗口布局"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── 左侧导航 ──
        self._nav_list = QListWidget()
        self._nav_list.setObjectName("navList")  # ★ v3 全局主题生效
        self._nav_list.setFixedWidth(self.SIDEBAR_WIDTH)
        for label, pid in self.NAV_ITEMS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            self._nav_list.addItem(item)
        self._nav_list.currentRowChanged.connect(self._on_nav_changed)
        main_layout.addWidget(self._nav_list)

        # ── 右侧面板区 ──
        self._stack = QStackedWidget()
        for _label, _pid in self.NAV_ITEMS:
            placeholder = QLabel("面板加载中...")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #777; font-size: 16px;")
            self._stack.addWidget(placeholder)
        main_layout.addWidget(self._stack, 1)

    def _subscribe_events(self):
        pass  # Reserved for project-wide events

    def _on_nav_changed(self, row: int):
        """导航切换"""
        if row < 0 or row >= len(self.NAV_ITEMS):
            return
        pid = self.NAV_ITEMS[row][1]
        if pid == self._current_panel_id:
            return
        # Auto-save previous panel
        if self._current_panel_id and self._current_panel_id in self._panels:
            try:
                self._panels[self._current_panel_id].on_close()
            except Exception:
                pass
        # Switch stack
        self._stack.setCurrentIndex(row)
        # Call on_show on target panel if implemented
        if pid in self._panels:
            try:
                self._panels[pid].on_show()
            except Exception:
                pass
        self._current_panel_id = pid

    def register_panel(self, panel_id: str, panel):
        """注册面板 — 替换占位符

        Args:
            panel_id: 面板标识（chat/outline/...）
            panel: BasePanel 实例（本身是 QWidget，直接嵌入 QStackedWidget）
        """
        self._panels[panel_id] = panel
        for i, (_label, pid) in enumerate(self.NAV_ITEMS):
            if pid == panel_id:
                old = self._stack.widget(i)
                self._stack.insertWidget(i, panel)  # panel IS the widget
                if old:
                    old.deleteLater()
                if not self._current_panel_id:
                    self._nav_list.setCurrentRow(i)
                break

    def run(self):
        """显示窗口并启动事件循环"""
        # ★ v3修复: 恢复上次打开的面板
        try:
            cfg = self._config_manager.load_app_config()
            last_pid = getattr(cfg, "last_panel_id", "") or ""
            if last_pid:
                for i, (_label, pid) in enumerate(self.NAV_ITEMS):
                    if pid == last_pid:
                        self._nav_list.setCurrentRow(i)
                        break
        except Exception:
            pass
        self.show()
        self._logger.log("MainWindow 启动完成", "MainWindow", "INFO")

    def set_status(self, text: str):
        self._status_label.setText(text)

    def closeEvent(self, event):
        """窗口关闭 — 保存配置 + 通知所有面板"""
        try:
            cfg = self._config_manager.load_app_config()
            cfg.window_width = self.width()
            cfg.window_height = self.height()
            cfg.last_panel_id = self._current_panel_id  # ★ v3修复
            self._config_manager.save_app_config(cfg)
        except Exception:
            pass
        for panel in self._panels.values():
            try:
                panel.on_close()
            except Exception:
                pass
        event.accept()
