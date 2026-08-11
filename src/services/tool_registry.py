"""
AI 工具注册表 — 让 AI 获得直接管理项目数据的能力

v2.2: 统一 fetch 工具取代独立 read 工具，支持批量读取
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, Callable
from dataclasses import dataclass

from src.services.project_service import OutlineLevel


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    handler: Callable


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.name] = tool

    def get_tool_schemas(self) -> list[dict]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in self._tools.values()]

    def execute(self, name: str, arguments: dict) -> str:
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
    registry = ToolRegistry()

    # ── 统一批量读取 ★ v2.2 ──
    registry.register(ToolDef(
        name="fetch",
        description="统一批量读取工具。一口气读取多个目标的内容。target: outline(大纲节点ID数组)/setting(需同时传category和ids)/character(角色ID数组)/foreshadow(伏笔ID数组)",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["outline", "setting", "character", "foreshadow"],
                           "description": "读取目标类型"},
                "ids": {"type": "array", "items": {"type": "string"},
                        "description": "目标ID数组（setting类型时为文档名数组）"},
                "category": {"type": "string", "description": "设定分类名（仅target=setting时必填）"},
            },
            "required": ["target", "ids"],
        },
        handler=lambda args: (
            {"error": "category 必填"} if args["target"] == "setting" and not args.get("category")
            else (
                # outline
                {nid: (project_service.get_node(nid).content if project_service.get_node(nid) else "(不存在)")
                 for nid in args["ids"]}
                if args["target"] == "outline"
                # setting
                else {d: project_service.get_setting(args.get("category", ""), d) or "(不存在)" for d in args["ids"]}
                if args["target"] == "setting"
                # character
                else {
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
                    for cid in args["ids"]
                }
                if args["target"] == "character"
                # foreshadow
                else {
                    fid: (
                        lambda f: (
                            {"id": f.foreshadow_id, "content": f.content, "hidden": f.hidden,
                             "created_at": f.created_at}
                            if f else {"error": "伏笔不存在"}
                        )
                    )(project_service.foreshadow_service.get_foreshadow(fid))
                    for fid in args["ids"]
                }
            )
        ),
    ))

    # ── 大纲结构 ──
    registry.register(ToolDef(
        name="list_outline",
        description="获取小说大纲结构，返回所有节点ID、标题、层级、父子关系。用于了解项目结构",
        parameters={"type": "object", "properties": {}},
        handler=lambda args: {"nodes": [
            {"id": n.node_id, "title": n.title, "level": n.level.name,
             "parent_id": n.parent_id, "children_ids": n.children_ids, "status": n.status.value}
            for n in project_service.get_outline_tree()
        ]},
    ))

    # ── 设定管理 ──
    registry.register(ToolDef(
        name="list_categories",
        description="列出所有设定分类名",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: {"categories": project_service.list_categories()},
    ))
    registry.register(ToolDef(
        name="list_settings",
        description="列出指定分类下的所有文档名",
        parameters={"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]},
        handler=lambda args: {"docs": project_service.list_docs(args["category"])},
    ))
    registry.register(ToolDef(
        name="write_setting",
        description="创建或更新设定文档（Markdown）",
        parameters={"type": "object", "properties": {
            "category": {"type": "string"}, "doc": {"type": "string"}, "content": {"type": "string"}},
            "required": ["category", "doc", "content"]},
        handler=lambda args: (
            project_service.save_setting(args["category"], args["doc"], args["content"])
            or {"saved": True, "category": args["category"], "doc": args["doc"]}
        ),
    ))

    # ── 章节管理 ──
    registry.register(ToolDef(
        name="write_chapter",
        description="创建新的大纲节点/章节，或覆写已有节点的内容",
        parameters={"type": "object", "properties": {
            "node_id": {"type": "string", "description": "要覆写的已有节点ID（不传则创建新节点）"},
            "parent_id": {"type": "string", "description": "父节点ID（创建时使用）"},
            "title": {"type": "string"}, "content": {"type": "string"},
            "level": {"type": "integer", "description": "1=大纲 2=卷 3=简纲 4=章纲 5=正文"}},
            "required": ["content"]},
        handler=lambda args: (
            {"updated": nid, "title": project_service.update_node(nid, title=args.get("title"), content=args["content"]).title}
            if (nid := args.get("node_id")) and project_service.get_node(nid)
            else (lambda: (node := project_service.create_node(args.get("parent_id"), args.get("title", "未命名"),
                     OutlineLevel(args.get("level", 5)), args["content"])) and {"created": node.node_id, "title": node.title})()
            if not args.get("node_id")
            else {"error": f"节点 {args['node_id']} 不存在"}
        ),
    ))

    # ── 精准编辑 ──
    def _text_edit_at(content, line, col, text, action):
        lines = content.split("\n")
        line = max(1, min(line, len(lines)))
        col = max(0, min(col, len(lines[line-1])))
        target = lines[line-1]
        if action == "insert": lines[line-1] = target[:col] + text + target[col:]
        elif action == "replace":
            end = col + len(text)
            lines[line-1] = target[:col] + text + target[end:]
        return {"result": "\n".join(lines), "edited_line": line, "col": col, "action": action}

    def _edit_handler(args, ps, action):
        if args["type"] == "setting":
            cat, doc = args.get("category", ""), args.get("doc", "")
            if not cat or not doc: return {"error": "category 和 doc 为必填"}
            content = ps.get_setting(cat, doc) or ""
            result = _text_edit_at(content, args["line"], args["col"], args["text"], action)
            ps.save_setting(cat, doc, result["result"])
            return result
        else:
            nid = args.get("node_id", "")
            if not nid: return {"error": "node_id 为必填"}
            node = ps.get_node(nid)
            result = _text_edit_at(node.content if node else "", args["line"], args["col"], args["text"], action)
            ps.update_node(nid, content=result["result"])
            return result

    for name, action in [("insert_text_at", "insert"), ("replace_text_at", "replace")]:
        desc = "在指定行/列插入文本" if action == "insert" else "替换指定行/列的文本"
        registry.register(ToolDef(name=name, description=desc, parameters={
            "type": "object", "properties": {
                "type": {"type": "string", "enum": ["setting", "outline"]},
                "category": {"type": "string"}, "doc": {"type": "string"},
                "node_id": {"type": "string"}, "line": {"type": "integer"},
                "col": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["type", "line", "col", "text"]},
            handler=lambda args, a=action: _edit_handler(args, project_service, a)))

    # ── 搜索 ──
    registry.register(ToolDef(name="search_content",
        description="在当前小说项目中搜索关键词，返回匹配路径和摘要",
        parameters={"type": "object", "properties": {"keyword": {"type": "string"}}, "required": ["keyword"]},
        handler=lambda args: {"results": project_service.search_content(args["keyword"])}))

    # ── 角色管理 ──
    registry.register(ToolDef(name="list_characters",
        description="列出所有角色，含名称、性别、阵营",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: {"characters": [
            {"char_id": c.char_id, "name": c.name, "gender": c.gender,
             "camps": [project_service.character_service.get_camp(cid).name
                       if project_service.character_service.get_camp(cid) else cid for cid in c.camp_ids]}
            for c in project_service.character_service.list_characters()
        ]}))
    registry.register(ToolDef(name="write_character",
        description="创建或更新角色。可修改名称、简介、性别、年龄、生日、阵营",
        parameters={"type": "object", "properties": {
            "char_id": {"type": "string"}, "name": {"type": "string"}, "gender": {"type": "string"},
            "age": {"type": "string"}, "birthday": {"type": "string"}, "bio": {"type": "string"},
            "camp_ids": {"type": "array", "items": {"type": "string"}}}, "required": []},
        handler=lambda args: (
            (lambda: project_service.character_service.update_character(args["char_id"],
                name=args.get("name"), gender=args.get("gender"), age=args.get("age"),
                birthday=args.get("birthday"), bio=args.get("bio"), camp_ids=args.get("camp_ids"))
                and {"updated": args["char_id"]})()
            if args.get("char_id") and project_service.character_service.get_character(args["char_id"])
            else {"created": project_service.character_service.create_character(args.get("name", "未命名")).char_id}
            if not args.get("char_id")
            else {"error": f"角色不存在: {args['char_id']}"}
        )))
    registry.register(ToolDef(name="list_camps",
        description="列出所有阵营（势力），含名称和简介",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: {"camps": [
            {"camp_id": c.camp_id, "name": c.name, "description": c.description}
            for c in project_service.character_service.list_camps()
        ]}))

    # ── 伏笔管理 ──
    registry.register(ToolDef(name="list_foreshadows",
        description="列出所有未隐藏的伏笔条目",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: {"foreshadows": [
            {"id": f.foreshadow_id, "content": f.content, "order": f.order}
            for f in project_service.foreshadow_service.list_foreshadows(include_hidden=False)
        ]}))
    registry.register(ToolDef(name="write_foreshadow",
        description="添加新伏笔条目",
        parameters={"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]},
        handler=lambda args: {"created": project_service.foreshadow_service.add_foreshadow(args["content"]).foreshadow_id}))
    registry.register(ToolDef(name="delete_foreshadow",
        description="删除伏笔条目（传入伏笔 ID，可用 list_foreshadows 获取）",
        parameters={"type": "object", "properties": {"foreshadow_id": {"type": "string"}}, "required": ["foreshadow_id"]},
        handler=lambda args: (
            project_service.foreshadow_service.delete_foreshadow(args["foreshadow_id"])
            or {"deleted": args["foreshadow_id"]}
        )))

    # ── 状态 ──
    registry.register(ToolDef(name="get_status",
        description="获取创作进度摘要：大纲节点数、完成率、正文字数等",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: {"status": {
            "current_project": project_service.get_current_project(),
            "total_nodes": len(project_service.get_outline_tree()),
            "completed_nodes": sum(1 for n in project_service.get_outline_tree() if n.status.value == "completed"),
            "l5_count": len(project_service.get_nodes_by_level(OutlineLevel.CONTENT)),
            "total_words": sum(n.word_count for n in project_service.get_nodes_by_level(OutlineLevel.CONTENT)),
        }}))

    # ── 字数统计 ★ v3 ──
    registry.register(ToolDef(name="count_words",
        description="统计文本字数。中文按字计、英文按空格分词计、标点各计一字。返回 {total, chinese, english}。",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待统计的文本内容"},
            },
            "required": ["text"],
        },
        handler=lambda args: _count_words(args.get("text", ""))))

    return registry


def _count_words(text: str) -> dict:
    """字数统计实现 — 中文按字、英文按空格分词、标点各计一字（v3）"""
    if not text:
        return {"total": 0, "chinese": 0, "english": 0}
    # 中文字符
    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
    # 英文/数字词：先剔除中文字符，再按空白分词
    non_cjk = re.sub(r'[\u4e00-\u9fff]', ' ', text)
    english = len([w for w in non_cjk.split() if w.strip()])
    # 标点符号（非中文、非字母数字、非空白）
    punct = len(re.findall(r'[^\u4e00-\u9fff\w\s]', text))
    return {"total": chinese + english + punct, "chinese": chinese, "english": english + punct}
