"""
事件总线 — 发布-订阅模式的模块间通信机制

特性:
  - 事件驱动解耦，模块间零直接依赖
  - 支持通配符订阅（"chapter:*" 匹配所有 chapter 前缀）
  - 订阅者异常隔离（单个 handler 崩溃不影响其他）
  - 同步/异步双模式发布
  - 线程安全

用法:
    from src.core.event_bus import EventBus, Event

    bus = EventBus()
    bus.subscribe("chapter:saved", lambda e: print(f"Saved: {e.data}"))
    bus.publish("chapter:saved", {"chapter_id": "123"}, source="ProjectService")
"""

import threading
from typing import Callable, Any, Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from enum import Enum, auto
from collections import defaultdict


class EventPriority(Enum):
    """事件处理优先级"""
    HIGH = auto()
    NORMAL = auto()
    LOW = auto()


@dataclass
class Event:
    """事件数据结构"""
    name: str                      # 事件名，如 "chapter:saved"
    data: Any = None               # 事件携带的数据
    source: str = ""               # 事件来源模块 ID


EventHandler = Callable[[Event], None]


class EventBus:
    """全局事件总线（单例模式）

    典型用法:
        bus = EventBus()
        bus.subscribe("outline:tree_changed", my_handler)
        bus.publish("outline:tree_changed", {"project": "novel"}, source="ProjectService")
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._subscribers: Dict[str, List[Tuple[EventPriority, EventHandler]]] = \
            defaultdict(list)
        self._id_counter = 0

    def subscribe(
        self,
        event_name: str,
        handler: EventHandler,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> None:
        """订阅事件

        Args:
            event_name: 事件名称。支持通配符:
                        - "*" 订阅所有事件
                        - "chapter:*" 订阅所有 chapter 前缀的事件
            handler: 回调函数，签名为 (Event) -> None
            priority: 处理优先级，同优先级按订阅先后顺序

        Raises:
            ValueError: 已订阅相同 handler（重复订阅）
        """
        with self._lock:
            subs = self._subscribers[event_name]
            # 幂等检查：防止重复订阅
            for _, existing in subs:
                if existing is handler:
                    return
            subs.append((priority, handler))
            subs.sort(key=lambda x: x[0].value)  # 按优先级排序

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        """取消订阅"""
        with self._lock:
            self._subscribers[event_name] = [
                (p, h) for p, h in self._subscribers[event_name] if h is not handler
            ]
            if not self._subscribers[event_name]:
                del self._subscribers[event_name]

    def publish(self, event_name: str, data: Any = None, source: str = "") -> None:
        """同步发布事件（依次调用所有匹配的订阅者）

        Args:
            event_name: 事件名称
            data: 事件数据
            source: 发布者模块 ID
        """
        event = Event(name=event_name, data=data, source=source)

        with self._lock:
            # 收集所有匹配的 handler（结构 snapshot）
            handlers: List[Tuple[EventPriority, EventHandler]] = []
            for pattern, subs in self._subscribers.items():
                if self._match_pattern(pattern, event_name):
                    handlers.extend(subs)
            # 去重并按优先级排序
            seen = set()
            unique_handlers = []
            for p, h in sorted(handlers, key=lambda x: x[0].value):
                if h not in seen:
                    seen.add(h)
                    unique_handlers.append((p, h))

        # 在锁外执行 handler，避免死锁
        for _, handler in unique_handlers:
            try:
                handler(event)
            except Exception as e:
                # 异常隔离：单个 handler 崩溃不影响其他
                print(f"[EventBus] 事件处理异常 [{event_name}]: {e}")

    def publish_async(self, event_name: str, data: Any = None, source: str = "") -> None:
        """异步发布事件（在新线程中调用订阅者，不阻塞发布者）"""
        thread = threading.Thread(
            target=self.publish,
            args=(event_name, data, source),
            daemon=True,
        )
        thread.start()

    # ==================== 内部方法 ====================

    @staticmethod
    def _match_pattern(pattern: str, event_name: str) -> bool:
        """检查事件名是否匹配订阅模式

        Args:
            pattern: 订阅模式（可能含 *）
            event_name: 发布的事件名

        Returns:
            True 表示匹配
        """
        if pattern == "*":
            return True
        if pattern == event_name:
            return True
        if pattern.endswith(":*"):
            prefix = pattern[:-2]
            return event_name.startswith(prefix + ":")
        return False


# ==================== 全局单例 ====================

_event_bus_instance: Optional[EventBus] = None
_event_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """获取全局 EventBus 单例"""
    global _event_bus_instance
    if _event_bus_instance is None:
        with _event_bus_lock:
            if _event_bus_instance is None:
                _event_bus_instance = EventBus()
    return _event_bus_instance
