"""
对话会话管理 — 多轮 AI 对话的会话存储与检索

特性:
  - 会话 CRUD（创建、列表、删除、重命名）
  - 消息管理（增删查）
  - 上下文获取（最近 N 条消息 + 系统提示词）
  - JSON 文件存储，按会话分文件
  - 自动标题（取首条用户消息前 30 字符）

用法:
    from src.services.session_manager import SessionManager

    mgr = SessionManager(sessions_dir="workspace/sessions", event_bus=eb, logger=log)
    session = mgr.create_session(title="第一章讨论")
    mgr.add_message(session.session_id, "user", "帮我写一个开场")
    history = mgr.get_context_for_ai(session.session_id, max_messages=50)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

from src.core.event_bus import EventBus
from src.core.logger import Logger


# ==================== 数据类 ====================

@dataclass
class SessionMeta:
    """会话元数据（列表展示用，不含消息内容）"""
    session_id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str


@dataclass
class Session:
    """对话会话"""
    session_id: str
    title: str
    created_at: str
    updated_at: str
    system_prompt: str = ""
    messages: list[dict] = field(default_factory=list)
    # messages 结构: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]


# ==================== 会话管理器 ====================

class SessionManager:
    """对话会话管理器"""

    MAX_MESSAGES_DEFAULT = 1000      # 默认最大消息数
    AUTO_TITLE_LENGTH = 30           # 自动标题截取长度

    def __init__(self, sessions_dir: str, event_bus: EventBus, logger: Logger):
        """
        Args:
            sessions_dir: 会话存储根目录
            event_bus: 事件总线
            logger: 日志系统
        """
        self._sessions_dir = Path(sessions_dir)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._sessions_dir / "index.json"
        self._event_bus = event_bus
        self._logger = logger

        # 确保索引文件存在
        if not self._index_path.exists():
            self._save_index({})

    # ==================== 会话 CRUD ====================

    def create_session(self, title: str = "", system_prompt: str = "") -> Session:
        """创建新会话"""
        session_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        if not title:
            title = "新对话"

        session = Session(
            session_id=session_id,
            title=title,
            created_at=now,
            updated_at=now,
            system_prompt=system_prompt,
            messages=[],
        )

        self._save_session(session)
        self._update_index_entry(session_id, title, 0, now, now)

        self._logger.log(f"创建会话: {session_id[:8]}... ({title})", "SessionManager", "INFO")
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """获取完整会话（含消息历史）"""
        session_path = self._sessions_dir / session_id / "messages.json"
        if not session_path.exists():
            return None
        try:
            with open(session_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Session(
                session_id=data["session_id"],
                title=data.get("title", ""),
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
                system_prompt=data.get("system_prompt", ""),
                messages=data.get("messages", []),
            )
        except (json.JSONDecodeError, KeyError) as e:
            self._logger.log(f"会话文件损坏 [{session_id}]: {e}", "SessionManager", "ERROR")
            return None

    def list_sessions(self) -> list[SessionMeta]:
        """列出所有会话元数据（按更新时间倒序）"""
        index = self._load_index()
        metas = []
        for sid, info in index.items():
            metas.append(SessionMeta(
                session_id=sid,
                title=info.get("title", ""),
                message_count=info.get("message_count", 0),
                created_at=info.get("created_at", ""),
                updated_at=info.get("updated_at", ""),
            ))
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas

    def delete_session(self, session_id: str) -> None:
        """删除会话及其存储文件"""
        import shutil
        session_dir = self._sessions_dir / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir)

        index = self._load_index()
        if session_id in index:
            del index[session_id]
            self._save_index(index)

        self._logger.log(f"删除会话: {session_id[:8]}...", "SessionManager", "INFO")

    def rename_session(self, session_id: str, new_title: str) -> None:
        """重命名会话"""
        session = self.get_session(session_id)
        if session is None:
            self._logger.log(f"会话不存在: {session_id}", "SessionManager", "WARNING")
            return

        session.title = new_title
        self._save_session(session)
        self._update_index_entry(
            session_id, new_title,
            len(session.messages),
            session.created_at,
            datetime.now().isoformat(),
        )

    # ==================== 消息管理 ====================

    def add_message(self, session_id: str, role: str, content: str,
                     meta: Optional[dict] = None) -> None:
        """向会话添加一条消息

        Args:
            session_id: 会话 ID
            role: "user" | "assistant" | "system" | "tool"
            content: 消息内容
            meta: 可选元数据（如 refs 引用列表）
        """
        session = self.get_session(session_id)
        if session is None:
            self._logger.log(f"会话不存在: {session_id}", "SessionManager", "ERROR")
            return

        msg = {"role": role, "content": content}
        if meta:
            msg["meta"] = meta
        session.messages.append(msg)

        # 自动标题：取第一条用户消息的前 N 字符
        if role == "user" and session.title in ("", "新对话") and len(session.messages) <= 2:
            session.title = content[:self.AUTO_TITLE_LENGTH].replace("\n", " ")

        # 消息数量上限
        if len(session.messages) > self.MAX_MESSAGES_DEFAULT:
            session.messages = session.messages[-self.MAX_MESSAGES_DEFAULT:]

        session.updated_at = datetime.now().isoformat()
        self._save_session(session)
        self._update_index_entry(
            session_id, session.title, len(session.messages),
            session.created_at, session.updated_at,
        )

    def get_message_history(self, session_id: str, max_messages: int = 50) -> list[dict]:
        """获取最近 N 条消息"""
        session = self.get_session(session_id)
        if session is None:
            return []
        return session.messages[-max_messages:]

    def clear_messages(self, session_id: str) -> None:
        """清空会话消息（保留会话本身）"""
        session = self.get_session(session_id)
        if session is None:
            return
        session.messages = []
        session.updated_at = datetime.now().isoformat()
        self._save_session(session)
        self._update_index_entry(
            session_id, session.title, 0,
            session.created_at, session.updated_at,
        )

    # ==================== 上下文管理 ====================

    def set_system_prompt(self, session_id: str, prompt: str) -> None:
        """设置/更新系统提示词"""
        session = self.get_session(session_id)
        if session is None:
            return
        session.system_prompt = prompt
        self._save_session(session)

    def get_context_for_ai(self, session_id: str, max_messages: int = 50) -> list[dict]:
        """获取发送给 AI 的完整上下文（system_prompt + 最近 N 条消息）"""
        session = self.get_session(session_id)
        if session is None:
            return []

        context = []
        if session.system_prompt:
            context.append({"role": "system", "content": session.system_prompt})
        context.extend(session.messages[-max_messages:])
        return context

    # ==================== 内部实现 ====================

    def _save_session(self, session: Session) -> None:
        """保存会话到磁盘"""
        session_dir = self._sessions_dir / session.session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "session_id": session.session_id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "system_prompt": session.system_prompt,
            "messages": session.messages,
        }

        file_path = session_dir / "messages.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_index(self) -> dict:
        """加载会话索引"""
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                return json.load(f).get("sessions", {})
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save_index(self, index: dict) -> None:
        """保存会话索引"""
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump({"sessions": index}, f, ensure_ascii=False, indent=2)

    def _update_index_entry(
        self, session_id: str, title: str, message_count: int,
        created_at: str, updated_at: str,
    ) -> None:
        """更新索引中的会话条目"""
        index = self._load_index()
        index[session_id] = {
            "title": title,
            "message_count": message_count,
            "created_at": created_at,
            "updated_at": updated_at,
        }
        self._save_index(index)
