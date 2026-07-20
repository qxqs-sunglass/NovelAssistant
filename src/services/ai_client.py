"""
AI 客户端 — OpenAI 兼容 API 封装

特性:
  - 连接测试（列出可用模型）
  - 同步（非流式）对话
  - 流式对话（逐 token 回调 + EventBus 发布）
  - 自动重试（网络错误 1 次）
  - 模型备用切换（主模型不可用时自动尝试备用模型）
  - **方案A：幻觉工具调用自动检测与修复**（v0.3.1）

当模型不支持原生 Function Calling 协议，而在 content 文本中
以 XML/JSON 格式模拟工具调用时，自动解析并执行，确保对话不中断。

用法:
    from src.core.ai_client import AIClient, ChatMessage

    client = AIClient(event_bus, logger)
    client.configure(base_url="...", api_key="sk-...", model="gpt-4o-mini")
    client.set_hallucination_fix(True)  # 启用幻觉修复（默认开启）
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


# ============================================================================
# 幻觉工具调用检测（方案A）
# ============================================================================

# 支持的各种幻觉格式的正则模式（按精确度排序）
_HALLUCINATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # 1. XML <invoke name="xxx"> 格式
    (
        "xml_invoke",
        re.compile(
            r'<invoke\s+name\s*=\s*["\']([^"\']+)["\']\s*>\s*'
            r'(.*?)\s*'
            r'</invoke>',
            re.DOTALL | re.IGNORECASE,
        ),
    ),
    # 2. XML <tool_calls><invoke>... 格式
    (
        "xml_tool_calls_wrapper",
        re.compile(
            r'<tool_calls?>\s*'
            r'(.*?)'
            r'</tool_calls?>',
            re.DOTALL | re.IGNORECASE,
        ),
    ),
    # 3. Markdown 代码块 JSON（单段）
    (
        "md_json_block",
        re.compile(
            r'```(?:json|tool_call)?\s*\n?'
            r'(\{[^`]*?"(?:name|tool_name|function)"[^`]*?\})'
            r'\s*\n?```',
            re.DOTALL,
        ),
    ),
    # 4. 裸 JSON：{"name":"xxx","arguments":{...}}
    (
        "bare_json",
        re.compile(
            r'\{\s*"(?:name|tool_name)"\s*:\s*"([^"]+)"\s*,'
            r'\s*"(?:arguments|parameters|params)"\s*:\s*(\{[^}]+\})',
            re.DOTALL,
        ),
    ),
    # 5. XML function_call 格式
    (
        "xml_function_call",
        re.compile(
            r'<function_?calls?\s+name\s*=\s*["\']([^"\']+)["\']\s*>'
            r'\s*(.*?)\s*'
            r'</function_?calls?>',
            re.DOTALL | re.IGNORECASE,
        ),
    ),
    # 6. 双花括号 JSON（可能被 markdown 转义）: {{"name":"xxx","arguments":{...}}}
    (
        "double_brace_json",
        re.compile(
            r'\{\{\s*"(?:name|tool_name)"\s*:\s*"([^"]+)"\s*,'
            r'\s*"(?:arguments|parameters|params)"\s*:\s*(\{(?:[^{}]|\{[^{}]*\})*\})\s*\}\}',
            re.DOTALL,
        ),
    ),
]


def parse_hallucinated_tool_calls(content: str, logger: Logger | None = None) -> list[dict[str, Any]]:
    """从 AI 回复文本中提取幻觉形式的工具调用。

    按照预定义的正则模式依次尝试，一旦匹配成功即停止
    （优先匹配更精确的 XML 格式，最后匹配裸 JSON）。

    确保返回值中 arguments 始终为 dict 类型。

    Args:
        content: AI 的完整回复文本
        logger: 可选的 Logger 实例用于调试输出

    Returns:
        提取到的工具调用列表，每个元素为 {"name": str, "arguments": dict}
        若无匹配则返回空列表
    """
    if not content or not content.strip():
        return []

    # ── 预处理：归一化 DSML 等"分隔式 XML"标签 ──
    # 例：< | | DSML | | tool_calls> → <tool_calls>
    #     < | | DSML | | invoke name="x"> → <invoke name="x">
    #     </ | | DSML | | invoke> → </invoke>
    handled_content = _normalize_pipe_tagged_xml(content)

    results: list[dict[str, Any]] = []

    for pattern_name, pattern in _HALLUCINATION_PATTERNS:
        if pattern_name == "xml_tool_calls_wrapper":
            # 第一遍：看是否有外层包装
            outer_match = pattern.search(handled_content)
            if not outer_match:
                continue
            # 有外层包装，对内层递归使用子模式匹配
            inner = outer_match.group(1).strip()
            # 对内层文本尝试其他模式
            sub_results, handled = _parse_inner_xml(inner)
            if sub_results:
                results = sub_results
                if logger:
                    logger.log(
                        f"幻觉检测命中模式: {pattern_name} (内层 {len(results)} 个调用)",
                        "AIClient", "INFO",
                    )
                break
            continue

        matches = pattern.findall(handled_content)
        if not matches:
            continue

        if logger:
            logger.log(f"幻觉检测命中模式: {pattern_name}", "AIClient", "INFO")

        for match in matches:
            try:
                tool_name = ""
                arguments: dict[str, Any] = {}

                if pattern_name == "xml_invoke":
                    tool_name = match[0]
                    arguments = _extract_args_from_xml_body(match[1])

                elif pattern_name == "md_json_block":
                    obj = json.loads(match if isinstance(match, str) else str(match))
                    tool_name = obj.get("name") or obj.get("tool_name") or ""
                    arguments = _normalize_args(obj.get("arguments") or obj.get("parameters") or obj.get("params") or {})

                elif pattern_name in ("bare_json", "double_brace_json"):
                    tool_name = match[0]
                    try:
                        arguments = json.loads(match[1])
                    except json.JSONDecodeError:
                        arguments = {}

                elif pattern_name == "xml_function_call":
                    tool_name = match[0]
                    arguments = _extract_args_from_xml_body(match[1])

                if tool_name:
                    # 防御：确保 arguments 是 dict
                    if not isinstance(arguments, dict):
                        arguments = {}
                    results.append({"name": tool_name, "arguments": arguments})

            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
                if logger:
                    logger.log(
                        f"解析幻觉工具调用失败 (pattern={pattern_name}): {e}",
                        "AIClient", "WARNING",
                    )
                continue

        if results:
            break

    return results


def _parse_inner_xml(inner: str) -> tuple[list[dict[str, Any]], str]:
    """解析 <tool_calls> 或外层 XML 包裹的内部内容。"""
    results: list[dict[str, Any]] = []
    invokes = re.findall(
        r'<invoke\s+name\s*=\s*["\']([^"\']+)["\']\s*>\s*(.*?)\s*</invoke>',
        inner, re.DOTALL | re.IGNORECASE,
    )
    for name, body in invokes:
        args = _extract_args_from_xml_body(body)
        if name:
            results.append({"name": name, "arguments": args})
    return results, ""


# ---------------------------------------------------------------------------
# 标签归一化辅助函数（DSML 等"分隔式 XML"）
# ---------------------------------------------------------------------------

# 匹配任何"含 | 分隔符"的开始或结束标签
# 典型输入：< | | DSML | | tool_calls>  /  </ | | DSML | | tool_calls>
#           < | | function_call name="x">
_PIPE_TAGGED = re.compile(
    r"<[^<>]*\|[^<>]*>",
    re.IGNORECASE,
)


def _normalize_pipe_tagged_xml(content: str) -> str:
    """将"分隔式 XML"标签归一化为标准 XML 标签。

    处理示例：
    - `< | | DSML | | tool_calls>` → `<tool_calls>`
    - `< | | DSML | | invoke name="x">` → `<invoke name="x">`
    - `</ | | DSML | | invoke>` → `</invoke>`
    - `< | | function_call name="x">` → `<function_call name="x">`
    - `< | | parameter name="x">` → `<parameter name="x">`

    通用规则：标签内由 `|` 拆出的"中间段"全部丢弃，只保留最后一段
    （即真正的标签名 + 属性）。

    这样能兼容任何形式的"分隔标签"，包括但不限于 DSML 协议。

    Args:
        content: 原始文本

    Returns:
        标签归一化后的文本
    """
    def _repl(m: re.Match[str]) -> str:
        full = m.group(0)
        stripped = full.strip()
        # 是结束标签？兼容 < / | ... 这种带空格变体
        is_close = stripped.startswith("</") or stripped.startswith("< /")
        # 自闭和标签 <.../>
        is_self_close = (
            not is_close
            and stripped.rstrip().endswith("/>")
        )
        # 去掉首尾
        if is_close:
            # 去掉 < 和 / 之后的所有前导
            inner = re.sub(r"^<\s*/\s*", "", stripped)
            inner = inner.rstrip().rstrip(">").strip()
        else:
            inner = re.sub(r"^<\s*/?\s*", "", stripped)
            if inner.endswith("/>"):
                inner = inner[:-2].rstrip()
            else:
                inner = inner.rstrip(">").strip()

        # 按 | 切分
        parts = [p.strip() for p in inner.split("|") if p.strip()]
        if not parts:
            return full  # 无法解析，原样返回
        # 取最后一段作为真正的标签内容（可能含属性）
        tag_body = parts[-1]
        if is_close:
            # 结束标签只保留标签名
            tag_name = tag_body.split()[0] if tag_body.split() else ""
            return f"</{tag_name}>"
        if is_self_close:
            return f"<{tag_body}/>"
        return f"<{tag_body}>"

    return _PIPE_TAGGED.sub(_repl, content)


def _extract_args_from_xml_body(body: str) -> dict[str, Any]:
    """从 XML invoke 体内提取参数。

    支持：
    1. <parameter name="x">value</parameter> 格式
    2. 直接 JSON
    3. JSON 片段（匹配花括号）
    """
    body = body.strip()
    if not body:
        return {}

    # 1. 尝试直接 JSON
    try:
        obj = json.loads(body)
        if isinstance(obj, dict):
            return _normalize_args(obj)
    except json.JSONDecodeError:
        pass

    # 2. 提取 <parameter name="x">...</parameter>
    params: dict[str, Any] = {}
    param_matches = re.findall(
        r'<parameter\s+name\s*=\s*["\']([^"\']+)["\']\s*>(.*?)</parameter\s*>',
        body, re.DOTALL | re.IGNORECASE,
    )
    if param_matches:
        for pname, pval in param_matches:
            params[pname] = pval.strip()
        return params

    # 3. 提取嵌套 JSON（尝试匹配最外层花括号）
    brace_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', body)
    if brace_match:
        try:
            return _normalize_args(json.loads(brace_match.group(0)))
        except json.JSONDecodeError:
            pass

    return {}


def _normalize_args(raw: Any) -> dict[str, Any]:
    """将参数规范化为 dict。处理字符串包裹的 JSON。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


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

        # 方案A：幻觉工具调用自动修复
        self._fix_hallucinations: bool = True

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
        """带工具调用的流式对话生成器（含幻觉自动修复）

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

        full_text = ""
        for _round in range(max_rounds):
            # 每轮都发送 tool definitions。
            # DeepSeek V4 的 DSML tool-calling 是有状态的：
            # 一旦触发工具调用，后续轮次若收不到 tools 定义，
            # 模型不会自动退出 tool-calling 模式，反而会基于
            # 上下文"脑补"不存在的工具名。
            round_tools = tools if tools else None
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

                    continue  # 继续下一轮对话

                # ── 方案A：幻觉工具调用检测 ──
                if (
                    self._fix_hallucinations
                    and round_text.strip()
                ):
                    hallucinated = parse_hallucinated_tool_calls(
                        round_text, self._logger
                    )
                    if hallucinated:
                        self._logger.log(
                            f"检测到幻觉工具调用 {len(hallucinated)} 个，自动修复中...",
                            "AIClient", "WARNING",
                        )

                        yield {
                            "type": "hallucination_detected",
                            "patterns_found": len(hallucinated),
                        }

                        # 将 assistant 原始消息追加到历史
                        full_messages.append({
                            "role": "assistant",
                            "content": round_text,
                        })

                        # 逐个执行
                        for hc in hallucinated:
                            tool_name = hc["name"]
                            arguments = hc["arguments"]

                            self._event_bus.publish(
                                "ai:tool_call",
                                {"tool": tool_name, "args": arguments, "hallucinated": True},
                                "AIClient",
                            )
                            yield {
                                "type": "tool_call",
                                "tool": tool_name,
                                "args": arguments,
                                "hallucinated": True,
                            }

                            try:
                                result = tool_registry.execute(tool_name, arguments)
                            except Exception as e:
                                result = json.dumps(
                                    {"error": f"工具执行失败: {e}"},
                                    ensure_ascii=False,
                                )
                                self._logger.log(
                                    f"幻觉工具执行失败: {tool_name}: {e}",
                                    "AIClient", "ERROR",
                                )

                            self._event_bus.publish(
                                "ai:tool_result",
                                {"tool": tool_name, "result": str(result), "hallucinated": True},
                                "AIClient",
                            )
                            yield {
                                "type": "tool_result",
                                "tool": tool_name,
                                "result": str(result),
                                "hallucinated": True,
                            }

                            # 追加工具结果到消息历史（用 hall_ 前缀区分）
                            full_messages.append({
                                "role": "tool",
                                "tool_call_id": f"hall_{tool_name}",
                                "content": str(result),
                            })

                        # 追加引导提示，让模型基于结果继续
                        full_messages.append({
                            "role": "system",
                            "content": (
                                "你刚才的工具调用请求已通过自动修复机制执行完毕，"
                                "执行结果已附在对应的 tool 消息中。请基于这些结果继续你的回复，"
                                "无需再次发起相同的工具调用。如果需要，请通过 API 的 "
                                "tool_calls 协议发起新的工具调用。"
                            ),
                        })

                        continue  # 继续下一轮

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

    def set_hallucination_fix(self, enabled: bool) -> None:
        """启用/禁用幻觉工具调用自动修复（方案A）。

        当模型不支持原生 Function Calling 而将工具调用写为
        XML/JSON 文本时，自动解析并执行。

        Args:
            enabled: True 启用自动修复（默认）
        """
        self._fix_hallucinations = enabled

    @property
    def hallucination_fix_enabled(self) -> bool:
        """幻觉修复是否启用。"""
        return self._fix_hallucinations

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
