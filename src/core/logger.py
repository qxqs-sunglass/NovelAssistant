"""
日志系统 — 线程安全的异步日志记录器

特性:
  - 按模块 ID 分文件记录
  - 按日期自动创建子目录
  - 支持 DEBUG/INFO/WARNING/ERROR/CRITICAL 五级过滤
  - 生产者-消费者模式，通过消息队列异步写入
  - 超时未使用的文件句柄自动关闭
  - 线程安全，全局单例

用法:
    from src.core.logger import get_logger
    log = get_logger()
    log.log("消息内容", "MyModule", "INFO")
"""

import threading
import datetime
import queue
import time
import os
from typing import Optional, TextIO, Dict, Any


class Logger(threading.Thread):
    """线程化日志系统，通过消息队列异步写入"""

    def __init__(self, log_dir: str = "logs", min_level: str = "INFO"):
        """
        Args:
            log_dir: 日志根目录
            min_level: 最低记录级别
        """
        super().__init__()
        self.ID = "Logger"
        self.log_dir = log_dir
        self.start_time = time.time()

        # 日志级别
        self.LEVELS: Dict[str, int] = {
            "DEBUG": 0,
            "INFO": 1,
            "WARNING": 2,
            "ERROR": 3,
            "CRITICAL": 4,
        }
        self.min_level: int = self.LEVELS.get(min_level.upper(), 1)

        # 消息队列
        self._message_queue: queue.Queue = queue.Queue(maxsize=5000)

        # 文件句柄缓存: {module_id: {"path": str, "handle": TextIO, "last_write": float}}
        self._file_handles: Dict[str, Dict[str, Any]] = {}

        # 日期管理
        self._last_date_check = time.time()
        self._last_cleanup = time.time()
        self._current_date = datetime.datetime.now().strftime("%Y-%m-%d")

        # 状态标志
        self.active: bool = True
        self._exit = threading.Event()
        self.daemon = True  # 守护线程，随主程序退出

    # ==================== 公开 API ====================

    def log(
        self,
        message: str,
        module_id: str,
        level: str = "INFO",
        show_time: bool = True,
        show_level: bool = True,
    ) -> None:
        """异步记录一条日志（放入队列立即返回，不阻塞调用者）

        Args:
            message: 日志消息内容
            module_id: 来源模块标识，如 "AIClient"
            level: 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
            show_time: 输出时是否带时间戳
            show_level: 输出时是否带级别标签
        """
        if not self.active or not message:
            return

        try:
            self._message_queue.put(
                {
                    "message": str(message),
                    "module_id": module_id,
                    "level": level.upper(),
                    "show_time": show_time,
                    "show_level": show_level,
                },
                timeout=0.1,
            )
        except queue.Full:
            print(f"[Logger] 日志队列已满，丢弃消息: {message[:100]}")

    def set_min_level(self, level: str) -> None:
        """动态修改最低日志级别"""
        if level.upper() in self.LEVELS:
            self.min_level = self.LEVELS[level.upper()]
            self.log(f"最低日志级别已设为 {level.upper()}", self.ID, "INFO")

    def stop(self) -> None:
        """停止日志系统"""
        self.active = False
        self._exit.set()
        self.join(timeout=3.0)

    # ==================== 线程主循环 ====================

    def run(self) -> None:
        """日志处理主循环"""
        print(f"[{self.ID}] 日志系统启动，级别: {self._level_name()}")

        while not self._exit.is_set():
            try:
                self._process_queue()

                now = time.time()
                # 每 5 分钟清理闲置句柄
                if now - self._last_cleanup >= 300:
                    self._cleanup_idle_handles()
                    self._last_cleanup = now

                # 每小时检查日期变更
                if now - self._last_date_check >= 3600:
                    self._check_date_change()
                    self._last_date_check = now

                time.sleep(0.3)
            except Exception as e:
                print(f"[{self.ID}] 日志循环异常: {e}")
                time.sleep(0.5)

        self._close_all_handles()
        print(f"[{self.ID}] 日志系统已停止")

    # ==================== 内部实现 ====================

    def _process_queue(self) -> None:
        """批量处理队列中的日志消息"""
        batch_size = 100
        processed = 0
        while processed < batch_size:
            try:
                msg = self._message_queue.get_nowait()
                self._write_log(msg)
                processed += 1
            except queue.Empty:
                break

    def _write_log(self, msg: dict) -> None:
        """写入一条日志到文件"""
        try:
            module_id = msg["module_id"]
            level = msg["level"]

            # 级别过滤
            if self.LEVELS.get(level, 1) < self.min_level:
                return

            # 获取文件句柄
            handle = self._get_file_handle(module_id)
            if handle is None:
                return

            # 构建日志行
            parts = []
            if msg["show_time"]:
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                parts.append(f"[{ts}]")
            if msg["show_level"]:
                parts.append(f"[{level}]")
            parts.append(f"[{module_id}]")
            parts.append(msg["message"])

            line = " ".join(parts)
            handle.write(line + "\n")
            handle.flush()

            # 更新时间戳
            if module_id in self._file_handles:
                self._file_handles[module_id]["last_write"] = time.time()

            # ERROR/CRITICAL 级别同步输出到控制台
            if level in ("ERROR", "CRITICAL"):
                print(f"[{self.ID}] {line}")

        except Exception as e:
            print(f"[{self.ID}] 写入日志失败: {e}")

    def _get_file_handle(self, module_id: str) -> Optional[TextIO]:
        """获取或创建指定模块的日志文件句柄"""
        file_path = self._build_log_path(module_id)

        # 检查缓存
        if module_id in self._file_handles:
            cached = self._file_handles[module_id]
            if cached["path"] == file_path and cached["handle"] is not None:
                return cached["handle"]

        # 关闭旧句柄（路径已变）
        if module_id in self._file_handles:
            self._safe_close(self._file_handles[module_id].get("handle"))

        # 创建新句柄
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        try:
            handle = open(file_path, "a", encoding="utf-8")
            self._file_handles[module_id] = {
                "path": file_path,
                "handle": handle,
                "last_write": time.time(),
            }
            return handle
        except OSError as e:
            print(f"[{self.ID}] 无法打开日志文件 {file_path}: {e}")
            return None

    def _build_log_path(self, module_id: str) -> str:
        """构建日志文件完整路径"""
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        safe_id = module_id.replace("\\", "_").replace("/", "_")
        return os.path.join(self.log_dir, date_str, f"{safe_id}.log")

    def _check_date_change(self) -> None:
        """检查日期是否变更，若变更则关闭所有句柄"""
        new_date = datetime.datetime.now().strftime("%Y-%m-%d")
        if new_date != self._current_date:
            self._current_date = new_date
            print(f"[{self.ID}] 日期已切换至 {new_date}")
            self._close_all_handles()

    def _cleanup_idle_handles(self) -> None:
        """清理超过 30 分钟未使用的文件句柄"""
        now = time.time()
        to_remove = []
        for module_id, info in self._file_handles.items():
            if now - info["last_write"] > 1800:  # 30 分钟
                self._safe_close(info.get("handle"))
                to_remove.append(module_id)
        for mid in to_remove:
            del self._file_handles[mid]

    def _close_all_handles(self) -> None:
        """关闭所有文件句柄"""
        for info in self._file_handles.values():
            self._safe_close(info.get("handle"))
        self._file_handles.clear()

    @staticmethod
    def _safe_close(handle) -> None:
        """安全关闭文件句柄"""
        if handle:
            try:
                handle.close()
            except Exception:
                pass

    def _level_name(self) -> str:
        """获取当前最低级别的名称"""
        for name, val in self.LEVELS.items():
            if val == self.min_level:
                return name
        return "UNKNOWN"


# ==================== 全局单例 ====================

_logger_instance: Optional[Logger] = None
_logger_lock = threading.Lock()


def get_logger(log_dir: str = "logs", min_level: str = "INFO") -> Logger:
    """获取全局 Logger 单例（线程安全懒加载）

    Args:
        log_dir: 日志根目录（仅首次调用时生效）
        min_level: 最低日志级别（仅首次调用时生效）

    Returns:
        Logger 实例（已启动线程）
    """
    global _logger_instance
    if _logger_instance is None:
        with _logger_lock:
            if _logger_instance is None:
                _logger_instance = Logger(log_dir=log_dir, min_level=min_level)
                _logger_instance.start()
    return _logger_instance
