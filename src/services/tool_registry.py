"""
AI 工具注册表 — 让 AI 获得直接管理项目数据的能力

每个工具定义包含:
  - name: 工具名称（sent to AI）
  - description: 描述（AI 据此决定何时调用）
  - parameters: JSON Schema 参数定义
  - handler: 实际执行函数

用法:
    from src.services.tool_registry import ToolRegistry, create_tools
    registry = create_tools(project_service)
    result = registry.execute("read_setting", {"category": "力量体系", "doc": "斗气"})
"""

from __future__ import annotations

import json
from typing import Any, Optional, Callable
from dataclasses import dataclass, field

from src.services.project_service import OutlineLevel


@dataclass
class ToolDef:
    """工具定义"""
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable  # (dict) -> str


class ToolRegistry:
    """工具注册表"""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.name] = tool

    def get_tool_schemas(self) -> list[dict]:
        """获取 OpenAI 兼容的 tool definitions"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict) -> str:
        """执行工具调用，返回结果字符串"""
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        try:
            result = tool.handler(arguments)
            return json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def has(self, name: str) -> bool:
        return name in self._tools


def create_tools(project_service) -> ToolRegistry:
    """创建并注册所有核心工具"""

    registry = ToolRegistry()

    # ── 大纲结构工具 ──

    registry.register(ToolDef(
        name="list_outline",
        description="获取当前小说的大纲结构，返回所有节点的 ID、标题、层级和父子关系。用于理解项目结构、找到目标节点的 ID 以便后续读取/编辑",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=lambda args: {
            "nodes": [
                {
                    "id": n.node_id,
                    "title": n.title,
                    "level": n.level.name,
                    "parent_id": n.parent_id,
                    "children_ids": n.children_ids,
                    "status": n.status.value,
                }
                for n in project_service.get_outline_tree()
            ]
        },
    ))

    # ── 设定类工具 ──

    registry.register(ToolDef(
        name="list_categories",
        description="列出当前小说的所有设定分类（如力量体系、角色、地理等），返回分类名列表",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=lambda args: {"categories": project_service.list_categories()},
    ))

    registry.register(ToolDef(
        name="list_settings",
        description="列出指定设定分类下的所有文档",
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "设定分类名，如 '力量体系'"},
            },
            "required": ["category"],
        },
        handler=lambda args: {
            "docs": project_service.list_docs(args["category"]),
        },
    ))

    registry.register(ToolDef(
        name="read_setting",
        description="读取指定设定文档的完整 Markdown 内容",
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "设定分类名"},
                "doc": {"type": "string", "description": "文档名（不含 .md）"},
            },
            "required": ["category", "doc"],
        },
        handler=lambda args: {
            "content": project_service.get_setting(args["category"], args["doc"]) or "(文档不存在)",
        },
    ))

    registry.register(ToolDef(
        name="write_setting",
        description="创建或更新一个设定文档。用于保存 AI 生成的世界观设定、角色设定等",
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "设定分类名，不存在则自动创建"},
                "doc": {"type": "string", "description": "文档名（不含扩展名）"},
                "content": {"type": "string", "description": "Markdown 格式的设定内容"},
            },
            "required": ["category", "doc", "content"],
        },
        handler=lambda args: (
            project_service.save_setting(args["category"], args["doc"], args["content"])
            or {"saved": True, "category": args["category"], "doc": args["doc"]}
        ),
    ))

    # ── 大纲与章节工具 ──

    registry.register(ToolDef(
        name="read_chapter",
        description="读取指定大纲节点或章节的 Markdown 内容。请先通过 list_outline 了解节点结构",
        parameters={
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "大纲节点 ID"},
            },
            "required": ["node_id"],
        },
        handler=lambda args: (
            {"content": project_service.get_node(args["node_id"]).content}
            if project_service.get_node(args["node_id"])
            else {"error": "节点不存在"}
        ),
    ))

    registry.register(ToolDef(
        name="write_chapter",
        description="创建新的大纲节点/章节，或通过 node_id 覆写已有节点的 Markdown 内容",
        parameters={
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "要覆写的已有节点ID（更新时传入；不传则创建新节点）"},
                "parent_id": {"type": "string", "description": "父节点ID，创建新节点时使用（如创建顶级节点则不传）"},
                "title": {"type": "string", "description": "节点标题（覆写时可选，不传则保留原标题）"},
                "content": {"type": "string", "description": "Markdown 内容"},
                "level": {"type": "integer", "description": "层级: 1=大纲 2=卷 3=简纲 4=章纲 5=正文（仅创建新节点时使用）"},
            },
            "required": ["content"],
        },
        handler=lambda args: (
            # 覆写已有节点
            (
                lambda nid: (
                    {"updated": nid, "title": project_service.update_node(nid, title=args.get("title"), content=args["content"]).title}
                    if project_service.get_node(nid)
                    else {"error": f"节点 {nid} 不存在"}
                )
            )(args["node_id"])
            if args.get("node_id")
            # 创建新节点
            else (
                lambda: (
                    node := project_service.create_node(
                        args.get("parent_id"), args.get("title", "未命名"),
                        OutlineLevel(args.get("level", 5)),
                        args["content"],
                    )
                ) and {"created": node.node_id, "title": node.title}
            )()
        ),
    ))

    # ── 批量读取工具 ──

    registry.register(ToolDef(
        name="read_settings",
        description="批量读取多个设定文档的内容。传入分类名和文档名列表，一次获取多份设定，用于需要同时了解多个相关设定的场景",
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "设定分类名"},
                "docs": {"type": "array", "items": {"type": "string"},
                         "description": "要读取的文档名列表，如 [\"斗气体系\", \"魔法体系\"]"},
            },
            "required": ["category", "docs"],
        },
        handler=lambda args: {
            doc: project_service.get_setting(args["category"], doc) or "(不存在)"
            for doc in args["docs"]
        },
    ))

    registry.register(ToolDef(
        name="read_outline_nodes",
        description="批量读取多个大纲节点的内容。传入节点ID列表，一次获取多个节点的 Markdown 内容",
        parameters={
            "type": "object",
            "properties": {
                "node_ids": {"type": "array", "items": {"type": "string"},
                             "description": "要读取的节点ID列表"},
            },
            "required": ["node_ids"],
        },
        handler=lambda args: {
            nid: (project_service.get_node(nid).content if project_service.get_node(nid) else "(不存在)")
            for nid in args["node_ids"]
        },
    ))

    # ── 精准编辑工具（行/列级）──

    def _text_edit_at(content: str, line: int, col: int, text: str, action: str) -> dict:
        """在指定行/列位置执行 insert 或 replace 操作"""
        lines = content.split("\n")
        line = max(1, min(line, len(lines)))
        col = max(0, min(col, len(lines[line - 1])))
        target = lines[line - 1]
        if action == "insert":
            lines[line - 1] = target[:col] + text + target[col:]
        elif action == "replace":
            end_col = col + len(text)
            lines[line - 1] = target[:col] + text + target[end_col:]
        return {"result": "\n".join(lines), "edited_line": line, "col": col, "action": action}

    def _edit_handler(args: dict, ps, action: str) -> dict:
        """精准编辑的统一处理函数"""
        if args["type"] == "setting":
            cat = args.get("category", "")
            doc = args.get("doc", "")
            if not cat or not doc:
                return {"error": "category 和 doc 为必填参数"}
            content = ps.get_setting(cat, doc) or ""
            result = _text_edit_at(content, args["line"], args["col"], args["text"], action)
            ps.save_setting(cat, doc, result["result"])
            return result
        else:
            nid = args.get("node_id", "")
            if not nid:
                return {"error": "node_id 为必填参数"}
            node = ps.get_node(nid)
            content = node.content if node else ""
            result = _text_edit_at(content, args["line"], args["col"], args["text"], action)
            ps.update_node(nid, content=result["result"])
            return result

    registry.register(ToolDef(
        name="insert_text_at",
        description="在设定文档或章节内容的指定行/列位置插入文本。注意：line 从 1 开始计数，col 从 0 开始计数。用于 AI 精准修改已有内容",
        parameters={
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["setting", "outline"],
                         "description": "文件类型：setting=设定文档, outline=大纲节点"},
                "category": {"type": "string", "description": "设定分类名（type=setting 时必填）"},
                "doc": {"type": "string", "description": "设定文档名（type=setting 时必填）"},
                "node_id": {"type": "string", "description": "大纲节点ID（type=outline 时必填）"},
                "line": {"type": "integer", "description": "目标行号（从 1 开始）"},
                "col": {"type": "integer", "description": "目标列号（从 0 开始，0=行首）"},
                "text": {"type": "string", "description": "要插入的文本"},
            },
            "required": ["type", "line", "col", "text"],
        },
        handler=lambda args: _edit_handler(args, project_service, "insert"),
    ))

    registry.register(ToolDef(
        name="replace_text_at",
        description="替换设定文档或章节内容中指定行/列位置的文本。用于 AI 精准改写已有内容",
        parameters={
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["setting", "outline"],
                         "description": "文件类型：setting=设定文档, outline=大纲节点"},
                "category": {"type": "string", "description": "设定分类名（type=setting 时必填）"},
                "doc": {"type": "string", "description": "设定文档名（type=setting 时必填）"},
                "node_id": {"type": "string", "description": "大纲节点ID（type=outline 时必填）"},
                "line": {"type": "integer", "description": "目标行号（从 1 开始）"},
                "col": {"type": "integer", "description": "起始列号（从 0 开始）"},
                "text": {"type": "string", "description": "替换后的新文本"},
            },
            "required": ["type", "line", "col", "text"],
        },
        handler=lambda args: _edit_handler(args, project_service, "replace"),
    ))

    # ── 搜索工具 ──

    registry.register(ToolDef(
        name="search_content",
        description="在当前小说项目中搜索包含关键词的所有内容，返回匹配的文档路径和摘要",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["keyword"],
        },
        handler=lambda args: {
            "results": project_service.search_content(args["keyword"]),
        },
    ))

    return registry
