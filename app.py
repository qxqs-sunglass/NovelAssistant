"""
小说创作助手 — 应用入口

组装所有模块依赖并启动应用窗口。
"""

import sys
import os

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.logger import get_logger
from src.core.event_bus import get_event_bus
from src.core.config_manager import ConfigManager
from src.services.ai_client import AIClient
from src.services.session_manager import SessionManager
from src.services.project_service import ProjectService
from src.ui.main_window import MainWindow


def main():
    """应用入口函数"""
    # 1. 基础设施层
    logger = get_logger(log_dir="workspace/logs", min_level="INFO")
    event_bus = get_event_bus()
    config_manager = ConfigManager(config_dir="workspace/config")

    # 2. 业务逻辑层
    ai_client = AIClient(event_bus=event_bus, logger=logger)
    session_manager = SessionManager(
        sessions_dir="workspace/sessions",
        event_bus=event_bus,
        logger=logger,
    )
    project_service = ProjectService(
        workspace_dir="workspace",
        event_bus=event_bus,
        logger=logger,
    )

    # 3. 加载配置并应用
    app_config = config_manager.load_app_config()
    logger.set_min_level(app_config.log_level)

    # 自动恢复上次项目
    if app_config.last_project:
        try:
            # 检查项目是否还存在
            projects = project_service.list_projects()
            if any(p.name == app_config.last_project for p in projects):
                project_service.switch_project(app_config.last_project)
                logger.log(f"已恢复上次项目: {app_config.last_project}", "App", "INFO")
        except Exception:
            pass

    # 如果有已配置的 AI 源，自动加载
    current_source = config_manager.get_current_ai_source()
    if current_source:
        api_key = config_manager.get_api_key(current_source.name) or ""
        ai_client.configure(
            base_url=current_source.base_url,
            api_key=api_key,
            model=current_source.model,
            model_minor=current_source.model_minor,
            temperature=current_source.temperature,
            top_p=current_source.top_p,
            max_tokens=current_source.max_tokens,
        )
        logger.log(f"已加载 AI 源: {current_source.name}", "App", "INFO")

    # 4. 启动 UI
    window = MainWindow(
        config_manager=config_manager,
        event_bus=event_bus,
        logger=logger,
        ai_client=ai_client,
        session_manager=session_manager,
        project_service=project_service,
    )
    window.run()


if __name__ == "__main__":
    main()
