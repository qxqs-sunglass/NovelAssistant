"""
AI 客户端 — OpenAI 兼容 API 封装

特性:
  - 连接测试（列出可用模型）
  - 同步（非流式）对话
  - 流式对话（逐 token 回调 + EventBus 发布）
  - 自动重试（网络错误 1 次）
  - 模型备用切换（主模型不可用时自动尝试备用模型）

用法:
    from src.core.ai_client import AIClient, ChatMessage

    client = AIClient(event_bus, logger)
    client.configure(base_url="...", api_key="sk-...", model="gpt-4o-mini")
    result = client.test_connection()

    for chunk in client.chat_stream([ChatMessage("user", "你好")]):
        print(chunk, end="")
"""

from __future__ import annotations

import json
import time
from typing import Generator, Optional
from dataclasses import dataclass
from openai import OpenAI, APIError, APIConnectionError, AuthenticationError, \
    NotFoundError, RateLimitError, APITimeoutError

from src.core.event_bus import EventBus
from src.core.logger import Logger


# ==================== 数据类 ====================

@dataclass
class ChatMessage:
    """对话消息"""
    role: str           # "system" | "user" | "assistant"
    content: str        # 消息内容


@dataclass
class ChatResponse:
    """对话响应（非流式）"""
    content: str        # 完整回复内容
    model: str          # 实际使用的模型
    usage: dict         # token 用量


class AIClientError(Exception):
    """AI 客户端自定义异常"""

    def __init__(self, message: str, error_type: str = "unknown"):
        super().__init__(message)
        self.error_type = error_type  # "connection" | "auth" | "timeout" | "api" | "not_configured"


# ==================== AI 客户端 ====================

class AIClient:
    """OpenAI 兼容 API 客户端"""

    def __init__(self, event_bus: EventBus, logger: Logger):
        """
        Args:
            event_bus: 事件总线
            logger: 日志系统
        """
        self._event_bus = event_bus
        self._logger = logger

        # 配置参数
        self._base_url: str = ""
        self._api_key: str = ""
        self._model: str = ""
        self._model_minor: str = ""
        self._temperature: float = 1.0
        self._top_p: float = 0.9
        self._max_tokens: int = 2048
        self._extra_headers: dict = {}

        # OpenAI 客户端（延迟创建）
        self._client: Optional[OpenAI] = None
        self._cancelled = False

    def cancel(self):
        """取消当前流式请求"""
        self._cancelled = True

    # ==================== 连接管理 ====================

    def configure(
        self,
        base_url: str,
        api_key: str,
        model: str = "",
        model_minor: str = "",
        temperature: float = 1.0,
        top_p: float = 0.9,
        max_tokens: int = 2048,
        extra_headers: Optional[dict] = None,
    ) -> None:
        """配置 AI 客户端参数"""
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model or model_minor       # 主模型为空时用备用模型
        self._model_minor = model_minor or model  # 双向兜底
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._extra_headers = extra_headers or {}
        self._client = None  # 强制重建

    def test_connection(self) -> dict:
        """测试连接：尝试列出可用模型

        Returns:
            {"success": True, "models": ["model1", ...]}
            {"success": False, "error": "错误描述"}
        """
        try:
            client = self._get_or_create_client()
            models = client.models.list()
            model_names = [m.id for m in models]
            self._logger.log(
                f"连接测试成功，可用模型: {len(model_names)} 个",
                "AIClient", "INFO",
            )
            return {"success": True, "models": model_names}
        except AuthenticationError as e:
            return {"success": False, "error": f"认证失败: {e}"}
        except APIConnectionError as e:
            return {"success": False, "error": f"连接失败: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_models(self) -> list[str]:
        """获取可用模型列表"""
        try:
            client = self._get_or_create_client()
            models = client.models.list()
            return [m.id for m in models]
        except Exception as e:
            self._logger.log(f"获取模型列表失败: {e}", "AIClient", "ERROR")
            return []

    # ==================== 对话 ====================

    def chat(
        self,
        messages: list[ChatMessage],
        system_prompt: str = "",
    ) -> ChatResponse:
        """同步（非流式）对话

        Args:
            messages: 对话历史
            system_prompt: 系统提示词

        Returns:
            ChatResponse

        Raises:
            AIClientError: 连接/认证/超时等错误
        """
        client = self._get_or_create_client()
        full_messages = self._build_messages(messages, system_prompt)

        for attempt in range(2):  # 最多重试 1 次
            try:
                model = self._model if attempt == 0 else (self._model_minor or self._model)
                self._logger.log(f"发送对话请求, 模型: {model}", "AIClient", "DEBUG")

                response = client.chat.completions.create(
                    model=model,
                    messages=full_messages,
                    temperature=self._temperature,
                    top_p=self._top_p,
                    max_tokens=self._max_tokens,
                )
                choice = response.choices[0]
                return ChatResponse(
                    content=choice.message.content or "",
                    model=response.model,
                    usage={
                        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    },
                )
            except APITimeoutError as e:
                if attempt == 0:
                    self._logger.log("请求超时，正在重试...", "AIClient", "WARNING")
                    time.sleep(1)
                    continue
                raise AIClientError(f"请求超时: {e}", "timeout")
            except APIConnectionError as e:
                if attempt == 0:
                    self._logger.log("连接失败，正在重试...", "AIClient", "WARNING")
                    time.sleep(1)
                    continue
                raise AIClientError(f"连接失败: {e}", "connection")
            except AuthenticationError as e:
                raise AIClientError(f"认证失败，请检查 API Key: {e}", "auth")
            except NotFoundError as e:
                if attempt == 0 and self._model_minor:
                    self._logger.log(f"模型 {model} 不可用，切换到备用模型", "AIClient", "WARNING")
                    continue
                raise AIClientError(f"模型不可用: {e}", "api")
            except RateLimitError as e:
                if attempt == 0:
                    self._logger.log("速率限制，等待后重试...", "AIClient", "WARNING")
                    time.sleep(3)
                    continue
                raise AIClientError(f"速率限制: {e}", "api")
            except APIError as e:
                raise AIClientError(f"API 错误: {e}", "api")

        raise AIClientError("未知错误", "unknown")

    def chat_stream(
        self,
        messages: list[ChatMessage],
        system_prompt: str = "",
    ) -> Generator[str, None, None]:
        """流式对话生成器

        每 yield 一段增量文本，同时通过 EventBus 发布事件。

        Yields:
            str: 增量文本片段

        Raises:
            AIClientError
        """
        self._cancelled = False
        client = self._get_or_create_client()
        model = self._model or self._model_minor
        if not model:
            raise AIClientError("模型名未设置，请在配置中填写模型名称", "not_configured")
        full_messages = self._build_messages(messages, system_prompt)
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=full_messages,
                temperature=self._temperature,
                top_p=self._top_p,
                max_tokens=self._max_tokens,
                stream=True,
            )

            full_text = ""
            for chunk in stream:
                if self._cancelled:
                    stream.close()
                    break
                delta = chunk.choices[0].delta
                if delta.content:
                    text = delta.content
                    full_text += text
                    self._event_bus.publish(
                        "ai:response_chunk",
                        {"text": text},
                        "AIClient",
                    )
                    yield text

            # 流结束
            self._event_bus.publish(
                "ai:response_end",
                {"full_text": full_text, "model": self._model},
                "AIClient",
            )

        except APIConnectionError as e:
            self._event_bus.publish("ai:response_error", {"error": str(e)}, "AIClient")
            raise AIClientError(f"连接失败: {e}", "connection")
        except AuthenticationError as e:
            self._event_bus.publish("ai:response_error", {"error": str(e)}, "AIClient")
            raise AIClientError(f"认证失败: {e}", "auth")
        except Exception as e:
            self._event_bus.publish("ai:response_error", {"error": str(e)}, "AIClient")
            raise AIClientError(f"流式请求失败: {e}", "api")

    # ==================== 工具调用对话 ====================

    def chat_with_tools(
        self,
        messages: list[ChatMessage],
        tool_registry,  # ToolRegistry
        system_prompt: str = "",
        max_rounds: int = 5,
    ) -> Generator[dict, None, None]:
        """带工具调用的流式对话生成器

        每轮 AI 可能返回 tool_calls，执行后继续对话直到纯文本回复。

        Yields:
            dict: {"type": "chunk", "content": str} |
                  {"type": "tool_call", "tool": str, "args": dict} |
                  {"type": "tool_result", "tool": str, "result": str} |
                  {"type": "done", "full_text": str}
        """
        client = self._get_or_create_client()
        full_messages = self._build_messages(messages, system_prompt)
        tools = tool_registry.get_tool_schemas()
        model = self._model or self._model_minor
        if not model:
            raise AIClientError("模型名未设置，请在配置中填写模型名称", "not_configured")

        full_text = ""
        for _round in range(max_rounds):
            if self._cancelled:
                break
            # 仅首轮发送 tool definitions，后续轮次 AI 已知工具集
            round_tools = tools if _round == 0 and tools else None
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=full_messages,
                    tools=round_tools,
                    temperature=self._temperature,
                    top_p=self._top_p,
                    max_tokens=self._max_tokens,
                    stream=True,
                )

                # 收集完整响应
                tool_calls_data = []
                round_text = ""
                for chunk in response:
                    delta = chunk.choices[0].delta

                    # 文本块
                    if delta.content:
                        round_text += delta.content
                        full_text += delta.content
                        self._event_bus.publish(
                            "ai:response_chunk", {"text": delta.content}, "AIClient"
                        )
                        yield {"type": "chunk", "content": delta.content}

                    # 工具调用块
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            if tc.index >= len(tool_calls_data):
                                tool_calls_data.append({
                                    "id": tc.id or "",
                                    "type": "function",
                                    "function": {"name": tc.function.name or "", "arguments": ""}
                                })
                            if tc.function.arguments:
                                tool_calls_data[tc.index]["function"]["arguments"] += tc.function.arguments

                # 如果有工具调用
                if tool_calls_data:
                    # 追加 assistant 消息
                    full_messages.append({
                        "role": "assistant",
                        "content": round_text or None,
                        "tool_calls": tool_calls_data,
                    })

                    # 执行每个工具
                    for tc in tool_calls_data:
                        tool_name = tc["function"]["name"]
                        try:
                            args = json.loads(tc["function"]["arguments"])
                        except json.JSONDecodeError:
                            args = {}

                        self._event_bus.publish(
                            "ai:tool_call",
                            {"tool": tool_name, "args": args},
                            "AIClient",
                        )
                        yield {"type": "tool_call", "tool": tool_name, "args": args}

                        try:
                            result = tool_registry.execute(tool_name, args)
                        except Exception as e:
                            result = json.dumps({"error": str(e)})

                        self._event_bus.publish(
                            "ai:tool_result",
                            {"tool": tool_name, "result": result},
                            "AIClient",
                        )
                        yield {"type": "tool_result", "tool": tool_name, "result": result}

                        # 工具结果截断（≤3000字符）+ 去重旧结果
                        MAX_TOOL_RESULT = 3000
                        truncated = str(result)
                        if len(truncated) > MAX_TOOL_RESULT:
                            truncated = truncated[:MAX_TOOL_RESULT] + "\n...(内容已截断)"

                        # 移除之前同工具的历史结果，避免累积膨胀
                        full_messages = [
                            m for m in full_messages
                            if m.get("role") != "tool" or m.get("tool_call_id") != tc["id"]
                        ]
                        full_messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": truncated,
                        })

                    continue  # 继续下一轮对话

                # 纯文本回复，完成
                self._event_bus.publish(
                    "ai:response_end",
                    {"full_text": full_text, "model": self._model},
                    "AIClient",
                )
                yield {"type": "done", "full_text": full_text}
                return

            except APIConnectionError as e:
                self._event_bus.publish("ai:response_error", {"error": str(e)}, "AIClient")
                raise AIClientError(f"连接失败: {e}", "connection")
            except AuthenticationError as e:
                self._event_bus.publish("ai:response_error", {"error": str(e)}, "AIClient")
                raise AIClientError(f"认证失败: {e}", "auth")
            except Exception as e:
                self._event_bus.publish("ai:response_error", {"error": str(e)}, "AIClient")
                raise AIClientError(f"工具调用请求失败: {e}", "api")

        # 超过最大轮数
        self._event_bus.publish("ai:response_error", {"error": f"工具调用超过最大轮数 ({max_rounds})"}, "AIClient")
        yield {"type": "error", "error": f"工具调用超过最大轮数 ({max_rounds})"}

    # ==================== 状态查询 ====================

    @property
    def is_configured(self) -> bool:
        """是否已配置"""
        return bool(self._base_url and self._api_key)

    @property
    def current_model(self) -> str:
        """当前使用的模型"""
        return self._model or self._model_minor or "未配置"

    # ==================== 内部实现 ====================

    def _get_or_create_client(self) -> OpenAI:
        """延迟创建 OpenAI 客户端"""
        if self._client is None:
            if not self._base_url or not self._api_key:
                raise AIClientError("AI 客户端未配置", "not_configured")
            self._client = OpenAI(
                base_url=self._base_url,
                api_key=self._api_key,
                timeout=60.0,
                max_retries=0,  # 自己管理重试逻辑
                default_headers=self._extra_headers if self._extra_headers else None,
            )
            self._logger.log("OpenAI 客户端已创建", "AIClient", "INFO")
        return self._client

    @staticmethod
    def _build_messages(messages: list[ChatMessage], system_prompt: str) -> list[dict]:
        """构建发送给 API 的消息列表"""
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        for m in messages:
            result.append({"role": m.role, "content": m.content})
        return result
