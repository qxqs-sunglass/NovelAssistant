"""
配置管理器 — 应用配置读写 + API 密钥 AES-256 加密存储

特性:
  - JSON 应用配置文件
  - AI 源多配置管理（增删改查切换）
  - API 密钥 AES-256-GCM 加密存储到 SQLite
  - 密钥派生: PBKDF2 + 机器标识（Windows WMI）
  - 降级方案: 硬件标识获取失败时使用用户主密码

用法:
    from src.core.config_manager import ConfigManager, AppConfig

    mgr = ConfigManager(config_dir="workspace/config")
    config = mgr.load_app_config()
    mgr.set_api_key("OpenAI", "sk-xxx")
    key = mgr.get_api_key("OpenAI")  # 自动解密
"""

from __future__ import annotations

import json
import os
import sqlite3
import base64
import hashlib
import threading
import subprocess
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

from src.core.event_bus import EventBus, get_event_bus
from src.core.logger import Logger, get_logger


# ==================== 数据类 ====================

@dataclass
class AISourceConfig:
    """单个 AI 源的配置"""
    name: str = ""                          # 配置名称
    base_url: str = ""                      # API endpoint
    model: str = ""                         # 默认模型
    model_minor: str = ""                   # 备用模型
    temperature: float = 1.0
    top_p: float = 0.9
    max_tokens: int = 2048
    extra_headers: dict = field(default_factory=dict)
    supports_reasoning: bool = False        # ★ v2.2.2 是否支持深度思考（reasoning_content）
    enable_deep_thinking: bool = True       # ★ 是否启用深度思考（运行时开关，默认开启）
    enable_deep_continue: bool = False      # ★ 深度思考续写：思考被截断时把已产生的思考内容一起上传继续

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "model_minor": self.model_minor,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "supports_reasoning": self.supports_reasoning,
            "enable_deep_thinking": self.enable_deep_thinking,
            "enable_deep_continue": self.enable_deep_continue,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AISourceConfig":
        return cls(
            name=d.get("name", ""),
            base_url=d.get("base_url", ""),
            model=d.get("model", ""),
            model_minor=d.get("model_minor", ""),
            temperature=d.get("temperature", 1.0),
            top_p=d.get("top_p", 0.9),
            max_tokens=d.get("max_tokens", 2048),
            supports_reasoning=d.get("supports_reasoning", False),
            enable_deep_thinking=d.get("enable_deep_thinking", True),
            enable_deep_continue=d.get("enable_deep_continue", False),
        )


@dataclass
class AppConfig:
    """应用全局配置"""
    version: str = "3.0.0"
    language: str = "zh-CN"
    window_width: int = 1200
    window_height: int = 700
    log_level: str = "INFO"
    log_retention_days: int = 30
    current_ai_source: str = ""
    last_project: str = ""
    tool_enabled: bool = False
    max_tool_rounds: int = 5          # AI 工具调用最大轮数
    # ★ 提示词状态（名称 + 内容，内容支持手动输入）
    last_sys_prompt_name: str = ""
    last_sys_prompt_content: str = ""
    last_add_prompt_name: str = ""
    last_add_prompt_content: str = ""
    last_add_enabled: bool = False
    # ★ v2.2.1 可配置模板
    status_prompt_template: str = (
        "你是一位资深网文编辑。以下是作者当前的创作进度，请用 200~400 字给出针对性建议：\n\n"
        "项目: {project}\n"
        "大纲节点: {node_count} | 正文: {chapter_count}章 ({completed_count}完成) | 总字数: {word_count:,}\n\n"
        "{data}\n\n"
        "请直接输出（不要客套、不要标题、不要列表格式）：\n"
        "1. 当前进度的卡点或薄弱环节是什么？\n"
        "2. 下一步最应该做什么（给 1~2 个具体方向）？\n"
        "3. 用一句话总结当前创作状态。"
    )
    chat_skill_text: str = (
        "【工作流】全局工作流程\n"
        "【数据来源】当前项目数据库\n"
        "【执行步骤】\n"
        "1. 分析用户指令，将需要用到的大纲文档、设定信息、角色信息、伏笔信息，利用统一读取工具一口气批量读取。\n"
        "2. 执行用户指令进行内容生成。\n"
        "3. 分析伏笔条目，锁定可能需要删除的伏笔，利用读取工具进行读取。\n"
        "4. 使用伏笔工具，对已有的伏笔进行增删。"
    )
    # ★ v3.1修复: 全局默认采样参数（配置面板「功能提示词配置」页保存）
    temperature: float = 1.0
    top_p: float = 0.9
    max_tokens: int = 2048
    # ★ 输出截断自动续写
    auto_continue: bool = True
    max_continue_rounds: int = 3
    outline_expanded_ids: list[str] = field(default_factory=list)  # ★ 大纲展开节点
    # ★ v3修复: UI 状态保存
    last_panel_id: str = ""           # 上次打开的导航面板
    last_session_id: str = ""         # 上次打开的对话会话
    ai_sources: list[AISourceConfig] = field(default_factory=list)

    @staticmethod
    def _to_dict(source: AISourceConfig) -> dict:
        return source.to_dict()

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "language": self.language,
            "window_width": self.window_width,
            "window_height": self.window_height,
            "log_level": self.log_level,
            "log_retention_days": self.log_retention_days,
            "current_ai_source": self.current_ai_source,
            "last_project": self.last_project,
            "tool_enabled": self.tool_enabled,
            "max_tool_rounds": self.max_tool_rounds,
            "last_sys_prompt_name": self.last_sys_prompt_name,
            "last_sys_prompt_content": self.last_sys_prompt_content,
            "last_add_prompt_name": self.last_add_prompt_name,
            "last_add_prompt_content": self.last_add_prompt_content,
            "last_add_enabled": self.last_add_enabled,
            "status_prompt_template": self.status_prompt_template,
            "chat_skill_text": self.chat_skill_text,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "auto_continue": self.auto_continue,
            "max_continue_rounds": self.max_continue_rounds,
            "outline_expanded_ids": self.outline_expanded_ids,
            "last_panel_id": self.last_panel_id,
            "last_session_id": self.last_session_id,
            "ai_sources": [s.to_dict() for s in self.ai_sources],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AppConfig":
        sources = [AISourceConfig.from_dict(s) for s in d.get("ai_sources", [])]
        return cls(
            version=d.get("version", "0.2.0"),
            language=d.get("language", "zh-CN"),
            window_width=d.get("window_width", 1200),
            window_height=d.get("window_height", 700),
            log_level=d.get("log_level", "INFO"),
            log_retention_days=d.get("log_retention_days", 30),
            current_ai_source=d.get("current_ai_source", ""),
            last_project=d.get("last_project", ""),
            tool_enabled=d.get("tool_enabled", False),
            max_tool_rounds=d.get("max_tool_rounds", 5),
            last_sys_prompt_name=d.get("last_sys_prompt_name", ""),
            last_sys_prompt_content=d.get("last_sys_prompt_content", ""),
            last_add_prompt_name=d.get("last_add_prompt_name", ""),
            last_add_prompt_content=d.get("last_add_prompt_content", ""),
            last_add_enabled=d.get("last_add_enabled", False),
            status_prompt_template=d.get("status_prompt_template", AppConfig.status_prompt_template),
            chat_skill_text=d.get("chat_skill_text", AppConfig.chat_skill_text),
            temperature=d.get("temperature", 1.0),
            top_p=d.get("top_p", 0.9),
            max_tokens=d.get("max_tokens", 2048),
            auto_continue=d.get("auto_continue", True),
            max_continue_rounds=d.get("max_continue_rounds", 3),
            outline_expanded_ids=d.get("outline_expanded_ids", []),
            last_panel_id=d.get("last_panel_id", ""),
            last_session_id=d.get("last_session_id", ""),
            ai_sources=sources,
        )


# ==================== 配置管理器 ====================

class ConfigManager:
    """配置管理器（单例）"""

    # 加密参数
    PBKDF2_ITERATIONS = 100_000
    SALT_LENGTH = 16
    NONCE_LENGTH = 12

    def __init__(self, config_dir: str = "workspace/config"):
        """
        Args:
            config_dir: 配置目录路径
        """
        self._config_dir = Path(config_dir)
        self._config_dir.mkdir(parents=True, exist_ok=True)

        self._app_config_path = self._config_dir / "app_config.json"
        self._key_db_path = self._config_dir / "key_store.db"

        self._lock = threading.RLock()
        self._event_bus: EventBus = get_event_bus()
        self._logger: Logger = get_logger()

        # 加密密钥（延迟派生，首次派生后缓存，避免每次解密都重新计算）
        self._encryption_key: Optional[bytes] = None
        self._prewarm_started = False

    def prewarm_key(self) -> None:
        """预热加密密钥（后台调用）。

        ★ v3性能优化: 密钥派生会触发 wmic 机器标识查询（在部分 Windows
        上可能耗时数秒），若等到用户第一次打开配置/解密时才执行，会造成
        明显卡顿。因此应用启动时在后台线程预热，缓存密钥，后续使用即时返回。
        """
        if self._prewarm_started:
            return
        self._prewarm_started = True
        try:
            self._derive_encryption_key()
            self._logger.log("加密密钥已预热", "ConfigManager", "DEBUG")
        except Exception as e:
            self._logger.log(f"密钥预热失败: {e}", "ConfigManager", "WARNING")

    # ==================== 应用配置 ====================

    def load_app_config(self) -> AppConfig:
        """加载应用配置，不存在则创建默认配置（不会覆盖磁盘文件）"""
        if self._app_config_path.exists():
            try:
                with open(self._app_config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cfg = AppConfig.from_dict(data)
                # 验证：至少应该有基本结构（防止空/损坏的 JSON 被当做合法配置）
                if not isinstance(data.get("ai_sources"), list):
                    raise ValueError("ai_sources 字段缺失或格式错误")
                return cfg
            except Exception as e:
                self._logger.log(
                    f"配置文件解析失败，使用内存默认配置（磁盘文件未修改）: {e}",
                    "ConfigManager", "ERROR",
                )
        # 文件不存在或损坏 → 只在内存中返回默认，不写入磁盘
        return AppConfig()

    def save_app_config(self, config: AppConfig) -> None:
        """保存应用配置到磁盘"""
        with self._lock:
            try:
                with open(self._app_config_path, "w", encoding="utf-8") as f:
                    json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)
                self._logger.log("应用配置已保存", "ConfigManager", "INFO")
            except OSError as e:
                self._logger.log(f"保存配置失败: {e}", "ConfigManager", "CRITICAL")
                raise

    # ==================== AI 源管理 ====================

    def list_ai_sources(self) -> list[AISourceConfig]:
        """列出所有 AI 源配置"""
        config = self.load_app_config()
        return config.ai_sources

    def get_ai_source(self, name: str) -> Optional[AISourceConfig]:
        """按名称获取 AI 源配置"""
        for s in self.list_ai_sources():
            if s.name == name:
                return s
        return None

    def add_ai_source(self, source: AISourceConfig) -> None:
        """添加 AI 源"""
        config = self.load_app_config()
        # 同名覆盖
        config.ai_sources = [s for s in config.ai_sources if s.name != source.name]
        config.ai_sources.append(source)
        self.save_app_config(config)
        self._event_bus.publish("config:changed", {"action": "add", "name": source.name}, "ConfigManager")

    def update_ai_source(self, name: str, source: AISourceConfig) -> None:
        """更新 AI 源配置"""
        config = self.load_app_config()
        # 移除旧条目（不管是按原名还是新名）
        config.ai_sources = [s for s in config.ai_sources if s.name != name]
        if source.name != name:
            config.ai_sources = [s for s in config.ai_sources if s.name != source.name]
        config.ai_sources.append(source)
        self.save_app_config(config)
        self._event_bus.publish("config:changed", {"action": "update", "name": source.name}, "ConfigManager")

    def remove_ai_source(self, name: str) -> None:
        """删除 AI 源"""
        config = self.load_app_config()
        config.ai_sources = [s for s in config.ai_sources if s.name != name]
        if config.current_ai_source == name:
            config.current_ai_source = ""
        self.save_app_config(config)
        self.delete_api_key(name)
        self._event_bus.publish("config:changed", {"action": "remove", "name": name}, "ConfigManager")

    def set_current_ai_source(self, name: str) -> None:
        """设置当前使用的 AI 源"""
        config = self.load_app_config()
        if any(s.name == name for s in config.ai_sources):
            config.current_ai_source = name
            self.save_app_config(config)
            self._event_bus.publish("config:changed", {"action": "switch", "name": name}, "ConfigManager")

    def get_current_ai_source(self) -> Optional[AISourceConfig]:
        """获取当前 AI 源配置"""
        config = self.load_app_config()
        name = config.current_ai_source
        if name:
            return self.get_ai_source(name)
        sources = config.ai_sources
        return sources[0] if sources else None

    # ==================== 密钥管理（加密存储） ====================

    def set_api_key(self, source_name: str, api_key: str) -> None:
        """加密存储 API 密钥

        Args:
            source_name: AI 源名称
            api_key: API 密钥明文
        """
        if not api_key:
            return

        encryption_key = self._derive_encryption_key()
        salt = os.urandom(self.SALT_LENGTH)
        nonce = os.urandom(self.NONCE_LENGTH)

        aesgcm = AESGCM(encryption_key)
        ciphertext = aesgcm.encrypt(nonce, api_key.encode("utf-8"), None)

        blob = base64.b64encode(salt + nonce + ciphertext).decode("ascii")

        with self._lock:
            conn = self._get_key_db()
            conn.execute(
                "INSERT OR REPLACE INTO keys (source_name, encrypted_blob, updated_at) VALUES (?, ?, datetime('now'))",
                (source_name, blob),
            )
            conn.commit()
            conn.close()

        self._logger.log(f"密钥已加密存储: {source_name}", "ConfigManager", "INFO")

    def get_api_key(self, source_name: str) -> Optional[str]:
        """解密获取 API 密钥

        Returns:
            明文 API 密钥，或 None（不存在/解密失败）
        """
        with self._lock:
            conn = self._get_key_db()
            row = conn.execute(
                "SELECT encrypted_blob FROM keys WHERE source_name = ?",
                (source_name,),
            ).fetchone()
            conn.close()

        if not row:
            return None

        try:
            blob = base64.b64decode(row[0])
            salt = blob[:self.SALT_LENGTH]
            nonce = blob[self.SALT_LENGTH:self.SALT_LENGTH + self.NONCE_LENGTH]
            ciphertext = blob[self.SALT_LENGTH + self.NONCE_LENGTH:]

            encryption_key = self._derive_encryption_key()
            aesgcm = AESGCM(encryption_key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode("utf-8")
        except Exception as e:
            self._logger.log(f"密钥解密失败 [{source_name}]: {e}", "ConfigManager", "ERROR")
            return None

    def delete_api_key(self, source_name: str) -> None:
        """删除 API 密钥"""
        with self._lock:
            conn = self._get_key_db()
            conn.execute("DELETE FROM keys WHERE source_name = ?", (source_name,))
            conn.commit()
            conn.close()

    # ==================== 提示词模板 ====================

    def list_prompts(self, prompt_type: str | None = None) -> list[dict]:
        """列出保存的提示词模板（可按类型过滤）。

        Args:
            prompt_type: 可选过滤类型 — "system"（系统提示词）/"additional"（附加提示词）
                         传 None 则返回全部。
        """
        path = self._config_dir / "prompts.json"
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                prompts = json.load(f)
        except Exception:
            return []
        # 向后兼容：旧数据没有 type 字段，默认视为 "system"
        for p in prompts:
            if "type" not in p:
                p["type"] = "system"
        if prompt_type is not None:
            prompts = [p for p in prompts if p.get("type") == prompt_type]
        return prompts

    def save_prompt(self, name: str, content: str, prompt_type: str = "system") -> None:
        """保存提示词模板（同名覆盖）。

        Args:
            name: 提示词名称
            content: 提示词文本内容
            prompt_type: "system"（系统提示词）或 "additional"（附加提示词）
        """
        prompts = self.list_prompts()
        prompts = [p for p in prompts if p.get("name") != name]
        prompts.append({"name": name, "content": content, "type": prompt_type})
        with open(self._config_dir / "prompts.json", "w", encoding="utf-8") as f:
            json.dump(prompts, f, ensure_ascii=False, indent=2)

    def delete_prompt(self, name: str) -> None:
        """删除提示词模板"""
        prompts = [p for p in self.list_prompts() if p.get("name") != name]
        with open(self._config_dir / "prompts.json", "w", encoding="utf-8") as f:
            json.dump(prompts, f, ensure_ascii=False, indent=2)

    # ==================== 杂项 ====================

    def get_work_dir(self) -> str:
        """获取工作目录"""
        return str(self._config_dir.parent)

    # ==================== 内部实现 ====================

    def _get_key_db(self) -> sqlite3.Connection:
        """获取密钥数据库连接（自动建表）"""
        conn = sqlite3.connect(str(self._key_db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                source_name TEXT PRIMARY KEY,
                encrypted_blob TEXT NOT NULL,
                updated_at TEXT
            )
        """)
        return conn

    def _derive_encryption_key(self) -> bytes:
        """派生 AES-256 加密密钥（结果缓存，进程内只派生一次）"""
        if self._encryption_key is not None:
            return self._encryption_key

        # 加锁防止预热线程与 UI 线程同时派生（结果一致，仅避免重复计算）
        with self._lock:
            if self._encryption_key is not None:
                return self._encryption_key
            machine_id = self._get_machine_id()
            # 使用机器标识作为 PBKDF2 的输入
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,  # AES-256
                salt=b"novel_assistant_fixed_salt",  # 固定 salt，密钥不跨机器共享
                iterations=self.PBKDF2_ITERATIONS,
                backend=default_backend(),
            )
            self._encryption_key = kdf.derive(machine_id.encode("utf-8"))
            return self._encryption_key

    # 机器标识缓存（进程内只计算一次；wmic 在部分 Windows 上可能很慢或挂起）
    _MACHINE_ID_CACHE: Optional[str] = None
    _MACHINE_ID_LOCK = threading.Lock()

    @classmethod
    def _get_machine_id(cls) -> str:
        """获取机器标识（缓存结果，避免每次解密都触发慢速 wmic 子进程）

        ★ v3性能优化:
          - wmic 已被微软弃用，在较新的 Windows 上可能不存在或挂起，原实现
            每次派生密钥都会执行 2 个子进程（各最多 5s），导致打开配置界面时
            明显卡顿。改为结果缓存 + 更短的超时 + 首选环境变量。
          - 由于加密密钥在本进程内仅派生一次并缓存，此处也只需计算一次。

        Returns:
            机器标识字符串，获取失败返回 "novel_assistant_default"
        """
        if cls._MACHINE_ID_CACHE is not None:
            return cls._MACHINE_ID_CACHE

        with cls._MACHINE_ID_LOCK:
            if cls._MACHINE_ID_CACHE is not None:
                return cls._MACHINE_ID_CACHE
            cls._MACHINE_ID_CACHE = cls._compute_machine_id()
            return cls._MACHINE_ID_CACHE

    @staticmethod
    def _compute_machine_id() -> str:
        """实际计算机器标识（只在第一次调用时执行）"""
        # 首选：wmic CPU + 主板序列号（缩短超时，避免长时间阻塞）
        try:
            cpu_result = subprocess.run(
                ["wmic", "cpu", "get", "ProcessorId"],
                capture_output=True, text=True, timeout=2,
            )
            cpu_id = cpu_result.stdout.strip().split("\n")[-1].strip()

            board_result = subprocess.run(
                ["wmic", "baseboard", "get", "SerialNumber"],
                capture_output=True, text=True, timeout=2,
            )
            board_sn = board_result.stdout.strip().split("\n")[-1].strip()

            combined = f"{cpu_id}_{board_sn}".strip("_")
            if combined:
                return hashlib.sha256(combined.encode()).hexdigest()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # 降级：使用主机名 + 用户名
        try:
            hostname = os.environ.get("COMPUTERNAME", "unknown")
            username = os.environ.get("USERNAME", "unknown")
            fallback = f"{hostname}_{username}"
            return hashlib.sha256(fallback.encode()).hexdigest()
        except Exception:
            return "novel_assistant_default_v2"
