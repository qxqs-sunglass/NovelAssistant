"""
小说创作助手 v3.0 — 应用入口 (PySide6)

组装所有模块依赖并启动应用窗口。
★ v3修复: 数据根目录使用绝对路径（项目根/workspace），支持 NOVEL_WORKSPACE 环境变量覆盖，
  保证与 v2 数据互通且不依赖启动时的工作目录。
"""
import sys
import os
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


def get_workspace_root() -> Path:
    """数据根目录：NOVEL_WORKSPACE 环境变量优先，否则项目根/workspace"""
    env = os.environ.get("NOVEL_WORKSPACE", "").strip()
    if env:
        return Path(env).resolve()
    return PROJECT_ROOT / "workspace"

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from src.core.event_bus import EventBus
from src.core.logger import Logger
from src.core.config_manager import ConfigManager
from src.services.ai_client import AIClient
from src.services.session_manager import SessionManager
from src.services.project_service import ProjectService
from src.services.tool_registry import create_tools
from src.ui.main_window import MainWindow
from src.ui.theme import STYLE_SHEET


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("小说创作助手")
    app.setOrganizationName("NovelAssistant")
    app.setStyleSheet(STYLE_SHEET)  # ★ v3 全局主题

    # ── 基础设施层 ──
    ws = get_workspace_root()          # ★ v3修复: 绝对路径数据根
    logger = Logger(log_dir=str(ws / "logs"))
    config_manager = ConfigManager(config_dir=str(ws / "config"))
    logger.log(f"应用启动 v3.0 (PySide6)，数据根: {ws}", "App", "INFO")

    # ── 业务逻辑层 ──
    ai_client = AIClient(event_bus, logger)
    session_manager = SessionManager(str(ws / "sessions"), event_bus, logger)
    project_service = ProjectService(str(ws / "projects"), event_bus, logger)

    # Configure AI client from saved settings
    _configure_ai_client(ai_client, config_manager, logger)

    # ── 工具注册 ──
    tool_registry = create_tools(project_service)

    # ── 主窗口（注入所有依赖） ──
    window = MainWindow(
        config_manager=config_manager,
        event_bus=event_bus,
        logger=logger,
        ai_client=ai_client,
        session_manager=session_manager,
        project_service=project_service,
    )

    # ── 注册面板 ──
    _register_panels(window, event_bus, logger, ai_client,
                     session_manager, project_service,
                     config_manager, tool_registry)

    window.run()
    logger.log("事件循环启动", "App", "INFO")
    sys.exit(app.exec())


def _configure_ai_client(ai_client, config_manager, logger):
    """从配置加载当前 AI 源并配置 AIClient"""
    try:
        current_name = config_manager.get_current_ai_source()
        if current_name:
            source = config_manager.get_ai_source(current_name)
            if source:
                api_key = config_manager.get_api_key(current_name) or ""
                ai_client.configure(
                    base_url=source.base_url,
                    api_key=api_key,
                    model=source.model,
                    model_minor=source.model_minor,
                    temperature=source.temperature,
                    top_p=source.top_p,
                    max_tokens=source.max_tokens,
                )
                logger.log(f"AI 源已配置: {current_name}", "App", "INFO")
    except Exception as e:
        logger.log(f"AI 源配置失败: {e}", "App", "WARNING")


def _register_panels(window, event_bus, logger, ai_client,
                     session_manager, project_service,
                     config_manager, tool_registry):
    """注册所有面板（逐步替换占位符）"""
    # 有实现的 = 注册真实面板；未实现的 = 保留占位符
    from src.ui.config_panel import ConfigPanel
    from src.ui.foreshadow_panel import ForeshadowPanel
    from src.ui.chat_panel import ChatPanel
    from src.ui.outline_panel import OutlinePanel
    from src.ui.character_panel import CharacterPanel
    from src.ui.settings_panel import SettingsPanel
    from src.ui.status_panel import StatusPanel
    from src.ui.log_panel import LogPanel

    panels_map = [
        ("chat",     ChatPanel(event_bus, logger, ai_client, session_manager,
                                project_service, config_manager, tool_registry)),
        ("outline",  OutlinePanel(event_bus, logger, project_service, config_manager)),
        ("characters", CharacterPanel(event_bus, logger, project_service)),
        ("foreshadow", ForeshadowPanel(event_bus, logger, project_service)),
        ("settings", SettingsPanel(event_bus, logger, project_service)),
        ("status",   StatusPanel(event_bus, logger, ai_client, project_service, config_manager)),
        ("config",   ConfigPanel(event_bus, logger, config_manager, ai_client)),
        ("log",      LogPanel(event_bus, logger, config_manager)),
    ]
    for pid, panel in panels_map:
        window.register_panel(pid, panel)
        logger.log(f"面板已注册: {pid}", "App", "INFO")


if __name__ == "__main__":
    main()
