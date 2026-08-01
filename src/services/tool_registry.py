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
        description="读取设定文档内容。可传单个文档名或文档名数组批量读取，提高效率",
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "设定分类名"},
                "doc": {
                    "description": "文档名（字符串）或文档名数组（批量读取）",
                },
            },
            "required": ["category", "doc"],
        },
        handler=lambda args: (
            # 批量读取
            {d: project_service.get_setting(args["category"], d) or "(不存在)"
             for d in args["doc"]}
            if isinstance(args.get("doc"), list)
            # 单文档读取
            else {"content": project_service.get_setting(args["category"], args["doc"]) or "(文档不存在)"}
        ),
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
        description="读取大纲节点/章节内容。可传单个节点 ID 或 ID 数组批量读取，提高效率",
        parameters={
            "type": "object",
            "properties": {
                "node_id": {
                    "description": "大纲节点 ID（字符串）或 ID 数组（批量读取）",
                },
            },
            "required": ["node_id"],
        },
        handler=lambda args: (
            # 批量读取
            {
                nid: (
                    project_service.get_node(nid).content
                    if project_service.get_node(nid) else "(不存在)"
                )
                for nid in args["node_id"]
            }
            if isinstance(args.get("node_id"), list)
            # 单节点读取
            else (
                {"content": project_service.get_node(args["node_id"]).content}
                if project_service.get_node(args["node_id"])
                else {"error": "节点不存在"}
            )
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

    # ═══════════════════════════════════════════════════
    # ★ v2.0: 角色/阵营工具
    # ═══════════════════════════════════════════════════

    registry.register(ToolDef(
        name="list_characters",
        description="列出当前小说的所有角色，包含名称、性别和所属阵营。用于了解有哪些角色及其基本信息",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: {
            "characters": [
                {
                    "char_id": c.char_id,
                    "name": c.name,
                    "gender": c.gender,
                    "camps": [
                        project_service.character_service.get_camp(cid).name
                        if project_service.character_service.get_camp(cid)
                        else cid
                        for cid in c.camp_ids
                    ],
                }
                for c in project_service.character_service.list_characters()
            ]
        },
    ))

    registry.register(ToolDef(
        name="read_character",
        description="读取角色完整信息。可传单个角色 ID 或 ID 数组批量读取，提高效率",
        parameters={
            "type": "object",
            "properties": {
                "char_id": {
                    "description": "角色 ID（字符串）或 ID 数组（批量读取多个角色）",
                },
            },
            "required": ["char_id"],
        },
        handler=lambda args: (
            # 批量读取
            {
                cid: (
                    lambda ch: (
                        {"char_id": ch.char_id, "name": ch.name, "gender": ch.gender,
                         "age": ch.age, "birthday": ch.birthday, "bio": ch.bio,
                         "camps": [project_service.character_service.get_camp(cid2).name
                                   if project_service.character_service.get_camp(cid2) else cid2
                                   for cid2 in ch.camp_ids]}
                        if ch else {"error": "角色不存在"}
                    )
                )(project_service.character_service.get_character(cid))
                for cid in args["char_id"]
            }
            if isinstance(args.get("char_id"), list)
            # 单角色读取
            else (
                lambda ch: (
                    {"char_id": ch.char_id, "name": ch.name, "gender": ch.gender,
                     "age": ch.age, "birthday": ch.birthday, "bio": ch.bio,
                     "camps": [project_service.character_service.get_camp(cid).name
                               if project_service.character_service.get_camp(cid) else cid
                               for cid in ch.camp_ids]}
                    if ch else {"error": "角色不存在"}
                )
            )(project_service.character_service.get_character(args["char_id"]))
        ),
    ))

    registry.register(ToolDef(
        name="write_character",
        description="创建新角色或更新已有角色信息。可修改名称、简介（Markdown）、性别、年龄、生日、阵营标签等",
        parameters={
            "type": "object",
            "properties": {
                "char_id": {"type": "string", "description": "角色 ID（更新时传入；不传则创建新角色）"},
                "name": {"type": "string", "description": "角色名称（创建时必填；更新时可选）"},
                "gender": {"type": "string", "description": "性别"},
                "age": {"type": "string", "description": "年龄"},
                "birthday": {"type": "string", "description": "生日"},
                "bio": {"type": "string", "description": "角色简介（Markdown 格式）"},
                "camp_ids": {"type": "array", "items": {"type": "string"},
                             "description": "要设置的阵营 ID 列表（会覆盖原有阵营）"},
            },
            "required": [],
        },
        handler=lambda args: (
            # 更新已有角色
            (
                lambda: project_service.character_service.update_character(
                    args["char_id"],
                    name=args.get("name"),
                    gender=args.get("gender"),
                    age=args.get("age"),
                    birthday=args.get("birthday"),
                    bio=args.get("bio"),
                    camp_ids=args.get("camp_ids"),
                )
                and {"updated": args["char_id"], "name": project_service.character_service.get_character(args["char_id"]).name}
            )()
            if args.get("char_id") and project_service.character_service.get_character(args["char_id"])
            # 创建新角色
            else (
                {"created": project_service.character_service.create_character(args.get("name", "未命名")).char_id,
                 "name": args.get("name", "未命名")}
                if not args.get("char_id")
                else {"error": f"角色不存在: {args['char_id']}"}
            )
        ),
    ))

    registry.register(ToolDef(
        name="list_camps",
        description="列出当前小说的所有阵营（势力），包含名称和简介",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: {
            "camps": [
                {"camp_id": c.camp_id, "name": c.name, "description": c.description}
                for c in project_service.character_service.list_camps()
            ]
        },
    ))

    # ═══════════════════════════════════════════════════
    # ★ v2.0: 伏笔工具
    # ═══════════════════════════════════════════════════

    registry.register(ToolDef(
        name="list_foreshadows",
        description="列出当前小说所有未隐藏的伏笔条目",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: {
            "foreshadows": [
                {"id": f.foreshadow_id, "content": f.content, "order": f.order}
                for f in project_service.foreshadow_service.list_foreshadows(include_hidden=False)
            ]
        },
    ))

    registry.register(ToolDef(
        name="read_foreshadow",
        description="读取伏笔信息。可传单个伏笔 ID 或 ID 数组批量读取",
        parameters={
            "type": "object",
            "properties": {
                "foreshadow_id": {
                    "description": "伏笔 ID（字符串）或 ID 数组（批量读取）",
                },
            },
            "required": ["foreshadow_id"],
        },
        handler=lambda args: (
            # 批量
            {
                fid: (
                    lambda f: (
                        {"id": f.foreshadow_id, "content": f.content, "hidden": f.hidden,
                         "created_at": f.created_at}
                        if f else {"error": "伏笔不存在"}
                    )
                )(project_service.foreshadow_service.get_foreshadow(fid))
                for fid in args["foreshadow_id"]
            }
            if isinstance(args.get("foreshadow_id"), list)
            # 单个
            else (
                lambda f: (
                    {"id": f.foreshadow_id, "content": f.content, "hidden": f.hidden,
                     "created_at": f.created_at}
                    if f else {"error": "伏笔不存在"}
                )
            )(project_service.foreshadow_service.get_foreshadow(args["foreshadow_id"]))
        ),
    ))

    registry.register(ToolDef(
        name="write_foreshadow",
        description="添加新伏笔条目。伏笔是散布在故事中等待回收的线索",
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "伏笔内容描述"},
            },
            "required": ["content"],
        },
        handler=lambda args: {
            "created": project_service.foreshadow_service.add_foreshadow(args["content"]).foreshadow_id
        },
    ))

    # ═══════════════════════════════════════════════════
    # ★ v2.0: 状态工具
    # ═══════════════════════════════════════════════════

    registry.register(ToolDef(
        name="get_status",
        description="获取当前小说的创作进度摘要，包含大纲节点数、完成率、正文字数等统计信息",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: {
            "status": {
                "current_project": project_service.get_current_project(),
                "total_nodes": len(project_service.get_outline_tree()),
                "completed_nodes": sum(
                    1 for n in project_service.get_outline_tree()
                    if n.status.value == "completed"
                ),
                "l1_count": len(project_service.get_nodes_by_level(OutlineLevel.OUTLINE)),
                "l2_count": len(project_service.get_nodes_by_level(OutlineLevel.VOLUME)),
                "l3_count": len(project_service.get_nodes_by_level(OutlineLevel.BRIEF)),
                "l4_count": len(project_service.get_nodes_by_level(OutlineLevel.CHAPTER)),
                "l5_count": len(project_service.get_nodes_by_level(OutlineLevel.CONTENT)),
                "total_words": sum(
                    n.word_count
                    for n in project_service.get_nodes_by_level(OutlineLevel.CONTENT)
                ),
            }
        },
    ))

    return registry
