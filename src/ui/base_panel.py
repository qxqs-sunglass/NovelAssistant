"""面板基类 — PySide6 适配（v3.0）"""
from PySide6.QtWidgets import QWidget
from src.core.event_bus import EventBus
from src.core.logger import Logger


class BasePanel(QWidget):
    """面板基类 — 统一生命周期接口

    所有 8 个面板（对话/大纲/角色/伏笔/设定/状态/配置/日志）继承此类。
    面板本身是 QWidget，可直接嵌入 QStackedWidget。
    """

    def __init__(self, event_bus: EventBus, logger: Logger, parent=None):
        super().__init__(parent)
        self._event_bus = event_bus
        self._logger = logger
        self._setup_ui()
        self._subscribe_events()

    def _setup_ui(self) -> None:
        """Build the panel's UI (override in subclasses)"""
        raise NotImplementedError

    def _subscribe_events(self) -> None:
        """Subscribe to EventBus events (override if needed)"""
        pass

    def on_show(self) -> None:
        """Called when panel becomes visible (switch to this tab)"""
        pass

    def on_close(self) -> None:
        """Called when panel is hidden or app is closing"""
        pass
