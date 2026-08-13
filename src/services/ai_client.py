"""
AI 客户端 — OpenAI 兼容 API 封装

特性:
  - 连接测试（列出可用模型）
  - 同步（非流式）对话
  - 流式对话（逐 token 回调 + EventBus 发布）
  - 自动重试（网络错误 1 次）
  - 模型备用切换（主模型不可用时自动尝试备用模型）

当模型不支持原生 Function Calling 协议，而在 content 文本中
以 XML/JSON 格式模拟工具调用时，自动解析并执行，确保对话不中断。

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
import re
import time
from typing import Any, Generator, Optional
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
    reasoning: str = ""  # ★ v2.2.2 深度思考内容（assistant 消息可选）


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

        # ★ 深度思考开关：False 时不读取/不显示 reasoning_content
        self._deep_thinking: bool = True

        # ★ 深度思考续写开关：思考被截断时把已产生的思考内容一起上传继续
        self._deep_continue: bool = False

        # ★ 自动续写：当输出达到 max_tokens 上限被截断时，自动让 AI 续写剩余内容
        self._auto_continue: bool = False
        self._max_continue_rounds: int = 3

        # OpenAI 客户端（延迟创建）
        self._client: Optional[OpenAI] = None

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
        enable_deep_thinking: Optional[bool] = None,
        enable_deep_continue: Optional[bool] = None,
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
        if enable_deep_thinking is not None:
            self._deep_thinking = bool(enable_deep_thinking)
        if enable_deep_continue is not None:
            self._deep_continue = bool(enable_deep_continue)
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
        client = self._get_or_create_client()
        model = self._model or self._model_minor
        if not model:
            raise AIClientError("模型名未设置，请在配置中填写模型名称", "not_configured")
        full_messages = self._build_messages(messages, system_prompt)
        try:
            full_text = ""
            full_reasoning = ""
            truncated = False  # ★ 最终是否仍处于截断状态
            continue_count = 0  # ★ 已续写轮数

            while True:
                stream = client.chat.completions.create(
                    model=model,
                    messages=full_messages,
                    temperature=self._temperature,
                    top_p=self._top_p,
                    max_tokens=self._max_tokens,
                    stream=True,
                )

                round_text = ""
                round_reasoning = ""           # ★ 本轮产生的思考内容（用于深度思考续写）
                round_truncated = False
                for chunk in stream:
                    delta = chunk.choices[0].delta
                    # ★ v2.2.2: 深度思考内容（如 DeepSeek R1 / o1 的 reasoning_content）
                    # 仅当开启深度思考时才读取，避免普通模型的无意义思考内容累积
                    reasoning = getattr(delta, "reasoning_content", None) if self._deep_thinking else None
                    if reasoning:
                        full_reasoning += reasoning
                        round_reasoning += reasoning
                        self._event_bus.publish(
                            "ai:reasoning_chunk",
                            {"text": reasoning},
                            "AIClient",
                        )
                    if delta.content:
                        text = delta.content
                        round_text += text
                        full_text += text
                        self._event_bus.publish(
                            "ai:response_chunk",
                            {"text": text},
                            "AIClient",
                        )
                        yield text
                    # ★ 检测服务端停止原因：达到 max_tokens 上限会被标记为 "length"
                    fr = getattr(chunk.choices[0], "finish_reason", None)
                    if fr == "length":
                        round_truncated = True

                # 截断且开启自动续写且还有额度 → 续写
                if (
                    round_truncated
                    and self._auto_continue
                    and continue_count < self._max_continue_rounds
                    and round_text
                ):
                    continue_count += 1
                    self._logger.log(
                        f"输出被截断，自动续写第 {continue_count}/{self._max_continue_rounds} 轮",
                        "AIClient", "INFO",
                    )
                    self._event_bus.publish(
                        "ai:continue",
                        {"round": continue_count, "max_rounds": self._max_continue_rounds},
                        "AIClient",
                    )
                    full_messages.append({"role": "assistant", "content": round_text})
                    full_messages.append({
                        "role": "user",
                        "content": (
                            "【自动续写】你刚才的回复因达到单次输出上限而被截断了，"
                            f"截止到上一条消息末尾已生成：\n{round_text}\n\n"
                            "请直接从上一条消息的末尾无缝继续，不要重复已输出的内容，"
                            "也不要打招呼或说明，直接继续正文输出。"
                        ),
                    })
                    continue  # 进入下一轮续写

                # ★ 深度思考续写：正文还没开始但思考被截断时，把思考内容一起上传继续
                if (
                    round_truncated
                    and not round_text
                    and round_reasoning
                    and self._deep_continue
                    and continue_count < self._max_continue_rounds
                ):
                    continue_count += 1
                    self._logger.log(
                        f"深度思考阶段被截断，深度思考续写第 {continue_count}/"
                        f"{self._max_continue_rounds} 轮（已上传 {len(round_reasoning)} 字符思考内容）",
                        "AIClient", "INFO",
                    )
                    self._event_bus.publish(
                        "ai:continue",
                        {"round": continue_count, "max_rounds": self._max_continue_rounds},
                        "AIClient",
                    )
                    full_messages.append({
                        "role": "user",
                        "content": (
                            "【自动续写】你刚才的深度思考因达到单次输出上限而被截断了，"
                            f"截止到上一条消息末尾你的思考过程如下：\n{round_reasoning}\n\n"
                            "请基于以上思考过程继续你的深度思考，并最终给出正文回答。"
                            "不要重复已思考的内容，也不要打招呼或说明，直接继续。"
                        ),
                    })
                    continue  # 进入下一轮续写

                # 续写耗尽或未截断 → 结束
                truncated = round_truncated
                break

            if full_reasoning:
                self._event_bus.publish(
                    "ai:reasoning_end",
                    {"full_reasoning": full_reasoning},
                    "AIClient",
                )
            if truncated:
                self._logger.log(
                    f"输出达到 max_tokens 上限({self._max_tokens})被截断，"
                    f"已输出 {len(full_text)} 字符"
                    + (f"（已自动续写 {continue_count} 轮）" if continue_count else ""),
                    "AIClient", "WARNING",
                )
            self._event_bus.publish(
                "ai:response_end",
                {"full_text": full_text, "model": self._model,
                 "reasoning": full_reasoning, "truncated": truncated,
                 "max_tokens": self._max_tokens,
                 "continued_rounds": continue_count},
                "AIClient",
            )
            if truncated:
                self._event_bus.publish(
                    "ai:truncated",
                    {"max_tokens": self._max_tokens, "length": len(full_text),
                     "continued_rounds": continue_count},
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
        """
        每轮 AI 可能返回 tool_calls，执行后继续对话直到纯文本回复。
        当模型不支持原生 Function Calling 而在文本中模拟工具调用时，
        自动检测 XML/JSON 格式的工具调用并执行（方案A，默认开启）。

        Yields:
            dict: {"type": "chunk", "content": str} |
                  {"type": "tool_call", "tool": str, "args": dict, "hallucinated"?: bool} |
                  {"type": "tool_result", "tool": str, "result": str, "hallucinated"?: bool} |
                  {"type": "hallucination_detected", "patterns_found": int} |
                  {"type": "done", "full_text": str} |
                  {"type": "error", "error": str}
        """
        client = self._get_or_create_client()
        # 过滤历史消息中的 tool/function 角色，防止跨轮次/跨会话的
        # 旧工具调用信息污染新的 AI 请求。
        # 当前轮次产生的 tool 交互通过 full_messages 内部管理，不受影响。
        clean_messages = [
            m for m in messages
            if m.role in ("user", "assistant", "system")
        ]
        full_messages = self._build_messages(clean_messages, system_prompt)
        tools = tool_registry.get_tool_schemas()
        model = self._model or self._model_minor
        if not model:
            raise AIClientError("模型名未设置，请在配置中填写模型名称", "not_configured")

        final_answer = ""  # ★ 只记录最终纯文本回复；工具调用轮次的文本不进入最终答案
        full_reasoning = ""  # ★ v2.2.2 深度思考内容（跨轮次累积）
        # ★ 自动续写状态：截断后自动续写剩余内容
        continuation_mode = False  # 当前轮是否为续写轮（续写轮不再调用工具）
        continue_count = 0         # 已续写轮数
        for _round in range(max_rounds):
            # 每轮都发送 tool definitions。
            # DeepSeek V4 的 DSML tool-calling 是有状态的：
            # 一旦触发工具调用，后续轮次若收不到 tools 定义，
            # 模型不会自动退出 tool-calling 模式，反而会基于
            # 上下文"脑补"不存在的工具名。
            # ★ 续写轮次强制不传 tools，确保模型只做纯文本续写。
            round_tools = (None if continuation_mode else (tools if tools else None))
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
                round_reasoning = ""           # ★ 本轮产生的思考内容（用于深度思考续写）
                round_truncated = False  # ★ 本轮是否因达到输出上限被截断
                for chunk in response:
                    delta = chunk.choices[0].delta

                    # ★ v2.2.2: 深度思考内容
                    reasoning = getattr(delta, "reasoning_content", None) if self._deep_thinking else None
                    if reasoning:
                        full_reasoning += reasoning
                        round_reasoning += reasoning
                        self._event_bus.publish(
                            "ai:reasoning_chunk",
                            {"text": reasoning},
                            "AIClient",
                        )

                    # 文本块 —— ★ 只缓冲不实时流式；等整轮确认是纯文本回复后再统一展示，
                    # 避免工具调用轮次的中间文本被当作独立消息显示（产生"两次消息"问题）
                    if delta.content:
                        round_text += delta.content

                    # ★ 检测服务端停止原因：达到 max_tokens 上限会被标记为 "length"
                    fr = getattr(chunk.choices[0], "finish_reason", None)
                    if fr == "length":
                        round_truncated = True

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

                # 如果有工具调用（原生 API）
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

                        full_messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": str(result),
                        })

                    # ★ 工具调用轮被截断时也要触发自动续写
                    # 原实现在 tool_calls 分支直接 continue，从不检查 round_truncated，
                    # 导致本轮带着不完整工具参数被截断时 auto-continue 永远不生效，
                    # UI 仍提示"ued_rounds=0。输出达到上限"且 contin
                    if (
                        round_truncated
                        and round_text
                        and self._auto_continue
                        and continue_count < self._max_continue_rounds
                    ):
                        continue_count += 1
                        self._logger.log(
                            f"工具调用轮输出被截断，自动续写第 {continue_count}/"
                            f"{self._max_continue_rounds} 轮（已拼 {len(round_text)} 字符）",
                            "AIClient", "INFO",
                        )
                        self._event_bus.publish(
                            "ai:continue",
                            {"round": continue_count, "max_rounds": self._max_continue_rounds},
                            "AIClient",
                        )
                        # 注：assistant(tool_calls) + tool(result)*N 已经按 OpenAI
                        # 协议追加到了 full_messages，此处只追加续写指令，不再重复
                        # assistant 消息（否则会导致协议顺序错乱被 API 400 拒绝）。
                        full_messages.append({
                            "role": "user",
                            "content": (
                                "【自动续写】你刚才的回复（含工具调用）因达到单次输出上限而被截断了，"
                                f"截止到上一条消息末尾已生成：\n{round_text}\n\n"
                                "请从工具调用/正文末尾无缝继续，不要重复已输出的内容，"
                                "也不要打招呼或说明，直接继续输出。"
                            ),
                        })
                        continuation_mode = False
                        continue  # 进入续写轮

                    continue  # 继续下一轮对话

                # 纯文本回复（含自动续写拼接）
                if final_answer:
                    # ★ 续写轮：无缝拼接，避免产生第二条独立助手消息
                    final_answer += round_text
                else:
                    final_answer = round_text

                # 流式发布本轮新增内容
                if round_text:
                    self._event_bus.publish(
                        "ai:response_chunk", {"text": round_text}, "AIClient"
                    )
                    yield {"type": "chunk", "content": round_text}

                # ★ 截断处理：若开启自动续写且还有额度，则让 AI 续写剩余内容
                # 深度思考续写由下方独立分支处理，此处仅记录正文续写未触发的原因
                if (
                    round_truncated
                    and not round_text
                    and not (self._deep_continue and round_reasoning
                             and continue_count < self._max_continue_rounds)
                ):
                    # 截断但正文续写/深度续写均未触发，记录原因方便排查
                    reasons = []
                    if not round_text and not round_reasoning:
                        reasons.append("round_text与思考内容均为空")
                    if not self._auto_continue:
                        reasons.append("auto_continue未启用")
                    if not self._deep_continue:
                        reasons.append("深度思考续写未启用")
                    if continue_count >= self._max_continue_rounds:
                        reasons.append(
                            f"已耗尽续写额度({continue_count}/{self._max_continue_rounds})"
                        )
                    self._logger.log(
                        "输出被截断但未触发续写: " + "，".join(reasons or ["未知原因"]),
                        "AIClient", "WARNING",
                    )

                if (
                    round_truncated
                    and round_text
                    and self._auto_continue
                    and continue_count < self._max_continue_rounds
                ):
                    continue_count += 1
                    self._logger.log(
                        f"输出被截断，自动续写第 {continue_count}/{self._max_continue_rounds} 轮"
                        f"（已拼 {len(final_answer)} 字符）",
                        "AIClient", "INFO",
                    )
                    self._event_bus.publish(
                        "ai:continue",
                        {"round": continue_count, "max_rounds": self._max_continue_rounds},
                        "AIClient",
                    )
                    # 把已生成部分作为 assistant 消息追加，保证上下文连贯
                    if round_text:
                        full_messages.append({
                            "role": "assistant",
                            "content": round_text,
                        })
                    # 追加续写指令
                    full_messages.append({
                        "role": "user",
                        "content": (
                            "【自动续写】你刚才的回复因达到单次输出上限而被截断了，"
                            f"截止到上一条消息末尾已生成：\n{round_text}\n\n"
                            "请直接从上一条消息的末尾无缝继续，不要重复已输出的内容，"
                            "也不要打招呼或说明，直接继续正文输出。"
                        ),
                    })
                    continuation_mode = True  # 续写轮不再触发工具调用
                    continue  # 继续下一轮

                # ★ 深度思考续写：正文还没开始（round_text 为空），但思考内容被截断
                # 时，把本次已产生的思考内容一起上传，让模型继续思考/继续输出，
                # 避免思考内容白白丢失、正文一个字都没生成。
                if (
                    round_truncated
                    and not round_text
                    and round_reasoning
                    and self._deep_continue
                    and continue_count < self._max_continue_rounds
                ):
                    continue_count += 1
                    self._logger.log(
                        f"深度思考阶段被截断，深度思考续写第 {continue_count}/"
                        f"{self._max_continue_rounds} 轮（已上传 {len(round_reasoning)} 字符思考内容）",
                        "AIClient", "INFO",
                    )
                    self._event_bus.publish(
                        "ai:continue",
                        {"round": continue_count, "max_rounds": self._max_continue_rounds},
                        "AIClient",
                    )
                    # 把已产生的思考内容作为上下文一起上传。
                    # 注意：不走非标准的 assistant.reasoning_content 字段（多数 OpenAI 兼容
                    # API 会因未知字段拒绝 400），而是把思考内容嵌入用户续写指令里一并上传，
                    # 保证兼容性。模型能据此接着思考/接着输出。
                    full_messages.append({
                        "role": "user",
                        "content": (
                            "【自动续写】你刚才的深度思考因达到单次输出上限而被截断了，"
                            f"截止到上一条消息末尾你的思考过程如下：\n{round_reasoning}\n\n"
                            "请基于以上思考过程继续你的深度思考，并最终给出正文回答。"
                            "不要重复已思考的内容，也不要打招呼或说明，直接继续。"
                        ),
                    })
                    continuation_mode = False
                    continue  # 继续下一轮

                # 正常结束（或已耗尽续写额度）
                if round_truncated:
                    self._logger.log(
                        f"输出达到 max_tokens 上限({self._max_tokens})被截断，"
                        f"已输出 {len(final_answer)} 字符"
                        + (f"（已自动续写 {continue_count} 轮）" if continue_count else ""),
                        "AIClient", "WARNING",
                    )
                if full_reasoning:
                    self._event_bus.publish(
                        "ai:reasoning_end",
                        {"full_reasoning": full_reasoning},
                        "AIClient",
                    )
                self._event_bus.publish(
                    "ai:response_end",
                    {"full_text": final_answer, "model": self._model,
                     "reasoning": full_reasoning, "truncated": round_truncated,
                     "max_tokens": self._max_tokens,
                     "continued_rounds": continue_count},
                    "AIClient",
                )
                if round_truncated:
                    self._event_bus.publish(
                        "ai:truncated",
                        {"max_tokens": self._max_tokens, "length": len(final_answer),
                         "continued_rounds": continue_count},
                        "AIClient",
                    )
                yield {"type": "done", "full_text": final_answer}
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
        # ★ v5修复: 补发 ai:response_end，避免 UI 停在中途、没有结束信号
        if full_reasoning:
            self._event_bus.publish(
                "ai:reasoning_end",
                {"full_reasoning": full_reasoning},
                "AIClient",
            )
        self._event_bus.publish(
            "ai:response_end",
            {"full_text": final_answer, "model": self._model, "reasoning": full_reasoning},
            "AIClient",
        )
        self._event_bus.publish("ai:response_error", {"error": f"工具调用超过最大轮数 ({max_rounds})"}, "AIClient")
        yield {"type": "error", "error": f"工具调用超过最大轮数 ({max_rounds})", "full_text": final_answer}

    # ==================== 状态查询 ====================

    def set_deep_thinking(self, enabled: bool) -> None:
        """启用/禁用深度思考。

        禁用后不读取/不显示 reasoning_content，适用于不输出
        思考内容的普通模型，避免日志/上下文无意义累积。

        Args:
            enabled: True 启用深度思考（默认）
        """
        self._deep_thinking = bool(enabled)

    @property
    def deep_thinking_enabled(self) -> bool:
        """深度思考是否启用。"""
        return self._deep_thinking

    def set_deep_continue(self, enabled: bool) -> None:
        """启用/禁用深度思考续写。

        当 AI 在深度思考阶段（尚未产出正文）就被 max_tokens 截断时，
        把本次已产生的思考内容一起作为上下文上传，让模型继续思考/继续输出，
        避免思考内容白白丢失、正文一个字都没生成。

        Args:
            enabled: True 启用深度思考续写（默认关闭）
        """
        self._deep_continue = bool(enabled)

    @property
    def deep_continue_enabled(self) -> bool:
        """深度思考续写是否启用。"""
        return self._deep_continue

    def set_auto_continue(self, enabled: bool, max_rounds: int = 3) -> None:
        """启用/禁用输出截断后的自动续写。

        Args:
            enabled: True 启用自动续写（默认关闭）
            max_rounds: 单次对话最多自动续写轮数
        """
        self._auto_continue = bool(enabled)
        self._max_continue_rounds = max(1, int(max_rounds))

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
