"""
项目服务 — 管理项目、大纲层级（5 级）、正文、设定

特性:
  - 5 级大纲: L1 大纲 → L2 卷纲 → L3 简纲 → L4 章纲 → L5 正文
  - 8 步创作流程: 灵感搭建→基础设定→设定细化→剧情大纲→卷章划分→单卷细化→分割内容→内容细纲
  - 大纲节点 CRUD、拆分、合并、移动（升降级）
  - 力量体系 + 角色设定管理（跨势力双写）
  - 设定导出为 Markdown

数据存储:
    workspace/projects/{项目名}/
    ├── project.json           # 项目元数据 + 流程步骤
    ├── outline.json           # 大纲树索引
    ├── outline/               # L1~L4 大纲内容 .md
    ├── content/               # L5 正文 .md
    └── settings/              # 力量体系 + 角色设定
"""

from __future__ import annotations

import json
import os
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum, IntEnum

from src.core.event_bus import EventBus
from src.core.logger import Logger


# ==================== 枚举与数据类 ====================

class OutlineLevel(IntEnum):
    """大纲层级"""
    OUTLINE = 1      # L1 大纲（全书）
    VOLUME = 2       # L2 卷纲
    BRIEF = 3        # L3 简纲（10~20章）
    CHAPTER = 4      # L4 章纲（3~6章）
    CONTENT = 5      # L5 正文


class NodeStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    IGNORED = "ignored"


@dataclass
class ProjectMeta:
    """项目元数据"""
    name: str
    created_at: str = ""
    updated_at: str = ""
    description: str = ""
    current_step: int = 1  # 创作流程步骤 1~8


@dataclass
class OutlineNode:
    """大纲节点（5 级内容层级中的任意节点）"""
    node_id: str
    title: str
    level: OutlineLevel
    parent_id: Optional[str]
    children_ids: list[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.TODO
    order: int = 0
    content: str = ""
    file: str = ""
    word_count: int = 0
    created_at: str = ""
    updated_at: str = ""


# L5 正文兼容别名
Chapter = OutlineNode
ChapterStatus = NodeStatus


# ==================== 项目服务 ====================

class ProjectService:
    """项目与设定服务"""

    # 创作流程步骤名称
    WORKFLOW_STEPS = [
        "灵感搭建",
        "基础设定生成",
        "设定细化",
        "剧情大纲（全书）",
        "卷章划分",
        "单卷细化",
        "分割单卷内容",
        "生成内容细纲",
    ]

    # 合法项目名字符
    VALID_NAME_CHARS = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789_-\u4e00-\u9fff"  # 中英文数字下划线连字符
    )

    def __init__(self, workspace_dir: str, event_bus: EventBus, logger: Logger):
        """
        Args:
            workspace_dir: 工作区根目录
            event_bus: 事件总线
            logger: 日志系统
        """
        self.ID = "ProjectService"
        self._workspace_dir = Path(workspace_dir)
        self._projects_dir = self._workspace_dir / "projects"
        self._projects_dir.mkdir(parents=True, exist_ok=True)
        self._event_bus = event_bus
        self._logger = logger
        self._current_project: Optional[str] = None

    # ==================== 项目管理 ====================

    def list_projects(self) -> list[ProjectMeta]:
        """列出所有项目"""
        projects = []
        if not self._projects_dir.exists():
            return projects
        for item in sorted(self._projects_dir.iterdir()):
            if item.is_dir():
                meta = self._load_project_meta(item.name)
                if meta:
                    projects.append(meta)
        return projects

    def create_project(self, name: str, description: str = "") -> ProjectMeta:
        """创建新项目"""
        self._validate_name(name)

        project_dir = self._projects_dir / name
        if project_dir.exists():
            raise ValueError(f"项目已存在: {name}")

        # 创建目录结构
        (project_dir / "outline").mkdir(parents=True)
        (project_dir / "content").mkdir(parents=True)
        (project_dir / "settings").mkdir(parents=True)

        now = datetime.now().isoformat()
        meta = ProjectMeta(
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
            current_step=1,
        )
        self._save_project_meta(meta)
        # 初始化空大纲
        self._save_outline_index(project_dir, {"nodes": {}, "root_id": None})

        # 自动创建根节点
        self._current_project = name
        self.create_node(None, f"{name}·全书大纲", OutlineLevel.OUTLINE,
                         content=f"# {name}\n\n## 全书大纲\n\n> 在此编写全书剧情主线、核心冲突、结局走向。")
        self._current_project = None

        self._logger.log(f"创建项目: {name}", self.ID, "INFO")
        return meta

    def delete_project(self, name: str) -> None:
        """删除项目"""
        project_dir = self._projects_dir / name
        if project_dir.exists():
            shutil.rmtree(project_dir)
            if self._current_project == name:
                self._current_project = None
            self._logger.log(f"删除项目: {name}", self.ID, "INFO")

    def get_current_project(self) -> Optional[str]:
        """获取当前项目名"""
        return self._current_project

    def switch_project(self, name: str) -> None:
        """切换当前项目"""
        project_dir = self._projects_dir / name
        if not project_dir.exists():
            raise ValueError(f"项目不存在: {name}")
        self._current_project = name
        self._event_bus.publish("project:switched", {"project_name": name}, self.ID)
        self._logger.log(f"切换项目: {name}", self.ID, "INFO")

    # ==================== 大纲层级管理 ====================

    def get_outline_tree(self) -> list[OutlineNode]:
        """获取完整大纲树（所有节点列表）"""
        index = self._load_outline_index()
        if not index:
            return []
        nodes = index.get("nodes", {})
        return [self._dict_to_node(d) for d in nodes.values()]

    def get_node(self, node_id: str) -> Optional[OutlineNode]:
        """获取单个节点"""
        index = self._load_outline_index()
        if not index:
            return None
        data = index.get("nodes", {}).get(node_id)
        return self._dict_to_node(data) if data else None

    def get_children(self, parent_id: str) -> list[OutlineNode]:
        """获取某节点的直接子节点"""
        parent = self.get_node(parent_id)
        if not parent:
            return []
        children = []
        for cid in parent.children_ids:
            child = self.get_node(cid)
            if child:
                children.append(child)
        return sorted(children, key=lambda n: n.order)

    def create_node(
        self, parent_id: Optional[str], title: str,
        level: OutlineLevel, content: str = "", order: int = -1,
    ) -> OutlineNode:
        """在指定父节点下创建子节点"""
        node_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        # 生成文件名
        level_prefixes = {1: "outline_L1", 2: "volume", 3: "brief", 4: "chapter"}
        prefix = level_prefixes.get(level.value, "node")
        file_name = f"{prefix}_{node_id[:8]}.md"

        node = OutlineNode(
            node_id=node_id,
            title=title,
            level=level,
            parent_id=parent_id,
            status=NodeStatus.TODO,
            order=order if order >= 0 else self._next_order(parent_id),
            content=content,
            file=file_name,
            created_at=now,
            updated_at=now,
        )

        # 保存节点内容
        self._write_node_content(node)

        # 更新大纲索引
        index = self._load_outline_index()
        index["nodes"][node_id] = self._node_to_dict(node)

        # 更新父节点的 children_ids
        if parent_id and parent_id in index["nodes"]:
            parent = index["nodes"][parent_id]
            parent.setdefault("children_ids", []).append(node_id)

        # 如果是第一个节点（无 parent 且 root 为空），设为根
        if parent_id is None and index.get("root_id") is None:
            index["root_id"] = node_id

        self._save_outline_index(self._project_dir(), index)
        self._event_bus.publish("outline:tree_changed", {"project": self._current_project}, self.ID)
        self._logger.log(f"创建大纲节点: {title} (L{level.value})", self.ID, "INFO")
        return node

    def update_node(
        self, node_id: str, title: Optional[str] = None,
        content: Optional[str] = None, status: Optional[NodeStatus] = None,
    ) -> Optional[OutlineNode]:
        """更新节点"""
        node = self.get_node(node_id)
        if node is None:
            return None

        if title is not None:
            node.title = title
        if content is not None:
            node.content = content
            node.word_count = len(content.replace(" ", "").replace("\n", ""))
        if status is not None:
            node.status = status

        node.updated_at = datetime.now().isoformat()
        self._write_node_content(node)
        self._update_index_node(node)

        self._event_bus.publish("outline:tree_changed", {"project": self._current_project}, self.ID)
        return node

    def delete_node(self, node_id: str) -> None:
        """删除节点及其所有子节点"""
        node = self.get_node(node_id)
        if node is None:
            return

        # 递归删除子节点
        for child_id in list(node.children_ids):
            self.delete_node(child_id)

        # 从父节点移除
        if node.parent_id:
            parent = self.get_node(node.parent_id)
            if parent:
                parent.children_ids = [c for c in parent.children_ids if c != node_id]
                self._update_index_node(parent)

        # 删除文件
        self._delete_node_file(node)

        # 从索引移除
        index = self._load_outline_index()
        index["nodes"].pop(node_id, None)
        if index.get("root_id") == node_id:
            index["root_id"] = None
        self._save_outline_index(self._project_dir(), index)

        self._event_bus.publish("outline:tree_changed", {"project": self._current_project}, self.ID)
        self._logger.log(f"删除大纲节点: {node.title}", self.ID, "INFO")

    # ==================== 节点操作 ====================

    def move_node(self, node_id: str, new_parent_id: Optional[str], new_order: int) -> None:
        """移动节点到新的父节点下（支持升级/降级）"""
        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在: {node_id}")

        old_parent_id = node.parent_id

        # 从旧父节点移除
        if old_parent_id:
            old_parent = self.get_node(old_parent_id)
            if old_parent:
                old_parent.children_ids = [c for c in old_parent.children_ids if c != node_id]
                self._update_index_node(old_parent)

        # 设置新父节点
        node.parent_id = new_parent_id
        if new_parent_id:
            new_parent = self.get_node(new_parent_id)
            if new_parent:
                new_parent.children_ids.insert(
                    max(0, min(new_order, len(new_parent.children_ids))),
                    node_id,
                )
                self._update_index_node(new_parent)

        # 更新节点本身
        self._update_index_node(node)
        self._event_bus.publish("outline:tree_changed", {"project": self._current_project}, self.ID)

    def reorder_siblings(self, parent_id: str, ordered_ids: list[str]) -> None:
        """重排同级节点顺序"""
        parent = self.get_node(parent_id)
        if parent is None:
            raise ValueError(f"父节点不存在: {parent_id}")

        # 验证所有 ID 都是该父节点的子节点
        current_ids = set(parent.children_ids)
        if set(ordered_ids) != current_ids:
            raise ValueError("排序 ID 列表与当前子节点不匹配")

        parent.children_ids = ordered_ids
        # 同时更新每个子节点的 order 字段（get_children 按 order 排序）
        for i, cid in enumerate(ordered_ids):
            child = self.get_node(cid)
            if child:
                child.order = i
                self._update_index_node(child)
        self._update_index_node(parent)
        self._event_bus.publish("outline:tree_changed", {"project": self._current_project}, self.ID)

    def split_node(self, node_id: str, split_titles: list[str]) -> list[OutlineNode]:
        """将节点拆分为多个子节点"""
        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在: {node_id}")
        if node.level.value >= OutlineLevel.CONTENT.value:
            raise ValueError("正文节点不允许拆分")

        new_level = OutlineLevel(node.level.value + 1)
        new_nodes = []
        for i, title in enumerate(split_titles):
            child = self.create_node(node_id, title, new_level, order=i)
            new_nodes.append(child)

        self._logger.log(f"拆分节点 [{node.title}] → {len(new_nodes)} 个子节点", self.ID, "INFO")
        return new_nodes

    def merge_nodes(self, child_ids: list[str], new_title: str) -> OutlineNode:
        """将多个同级子节点合并为一个"""
        if len(child_ids) < 2:
            raise ValueError("至少需要 2 个节点进行合并")

        first = self.get_node(child_ids[0])
        if first is None:
            raise ValueError("节点不存在")

        parent_id = first.parent_id
        for cid in child_ids[1:]:
            n = self.get_node(cid)
            if n is None or n.parent_id != parent_id:
                raise ValueError("只能合并同父节点的兄弟节点")

        merged = self.create_node(parent_id, new_title, first.level)
        # 将原节点作为子节点挂载（保留内容）
        for cid in child_ids:
            self.move_node(cid, merged.node_id, -1)

        self._logger.log(f"合并 {len(child_ids)} 个节点 → {new_title}", self.ID, "INFO")
        return merged

    # ==================== 快捷查询 ====================

    def get_nodes_by_level(self, level: OutlineLevel) -> list[OutlineNode]:
        """获取指定层级的所有节点"""
        return [n for n in self.get_outline_tree() if n.level == level]

    def get_full_path(self, node_id: str) -> list[OutlineNode]:
        """获取从根到该节点的完整路径"""
        path = []
        current = self.get_node(node_id)
        while current:
            path.insert(0, current)
            if current.parent_id:
                current = self.get_node(current.parent_id)
            else:
                break
        return path

    # ==================== 流程状态 ====================

    def get_workflow_step(self) -> int:
        """获取当前创作流程步骤"""
        meta = self._load_project_meta(self._current_project or "")
        return meta.current_step if meta else 1

    def set_workflow_step(self, step: int) -> None:
        """设置当前创作流程步骤"""
        if not 1 <= step <= 8:
            raise ValueError(f"步骤值必须在 1~8 之间，当前: {step}")
        meta = self._load_project_meta(self._current_project or "")
        if meta:
            meta.current_step = step
            meta.updated_at = datetime.now().isoformat()
            self._save_project_meta(meta)
            self._event_bus.publish("workflow:step_changed", {"step": step}, self.ID)

    # ==================== 正文（L5）快捷接口 ====================

    def list_chapters(self) -> list[OutlineNode]:
        """获取所有 L5 正文节点"""
        return self.get_nodes_by_level(OutlineLevel.CONTENT)

    def get_chapter(self, chapter_id: str) -> Optional[OutlineNode]:
        return self.get_node(chapter_id)

    def create_chapter(self, parent_id: str, title: str, content: str = "") -> OutlineNode:
        return self.create_node(parent_id, title, OutlineLevel.CONTENT, content)

    def update_chapter(self, chapter_id: str, **kwargs) -> Optional[OutlineNode]:
        return self.update_node(
            chapter_id,
            title=kwargs.get("title"),
            content=kwargs.get("content"),
            status=kwargs.get("status"),
        )

    def delete_chapter(self, chapter_id: str) -> None:
        self.delete_node(chapter_id)

    # ==================== 通用设定管理（自由分类） ====================

    def list_categories(self) -> list[str]:
        """列出当前项目下所有设定分类（按自定义顺序，或字母排序）"""
        settings_dir = self._project_dir() / "settings"
        if not settings_dir.exists():
            return []
        items = sorted([d.name for d in settings_dir.iterdir() if d.is_dir()])
        return self._load_order(settings_dir, items)

    def list_docs(self, category: str) -> list[str]:
        """列出指定分类下的所有设定文档（按自定义顺序，或字母排序）"""
        cat_dir = self._project_dir() / "settings" / category
        if not cat_dir.exists():
            return []
        items = sorted([f.stem for f in cat_dir.glob("*.md")])
        return self._load_order(cat_dir, items)

    def get_setting(self, category: str, doc_name: str) -> Optional[str]:
        """读取指定分类下的设定文档内容
        Args:
            category: 分类名（即目录名），如 "力量体系"
            doc_name: 文档名（不含 .md），如 "斗气修炼"
        """
        file_path = self._project_dir() / "settings" / category / f"{doc_name}.md"
        if not file_path.exists():
            return None
        return file_path.read_text(encoding="utf-8")

    def save_setting(self, category: str, doc_name: str, content: str) -> None:
        """创建或更新设定文档
        Args:
            category: 分类名（目录名），不存在则自动创建
            doc_name: 文档名（不含扩展名）
            content: Markdown 内容
        """
        cat_dir = self._project_dir() / "settings" / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        (cat_dir / f"{doc_name}.md").write_text(content, encoding="utf-8")
        self._event_bus.publish("setting:updated", {"category": category, "doc": doc_name}, self.ID)
        self._logger.log(f"保存设定: {category}/{doc_name}", self.ID, "INFO")

    def delete_setting(self, category: str, doc_name: str) -> None:
        """删除设定文档"""
        file_path = self._project_dir() / "settings" / category / f"{doc_name}.md"
        if file_path.exists():
            file_path.unlink()
            self._logger.log(f"删除设定: {category}/{doc_name}", self.ID, "INFO")

    def delete_category(self, category: str) -> None:
        """删除整个设定分类目录"""
        import shutil
        cat_dir = self._project_dir() / "settings" / category
        if cat_dir.exists():
            shutil.rmtree(cat_dir)
            self._logger.log(f"删除设定分类: {category}", self.ID, "INFO")

    # ---- 设定排序与重命名 ----

    def _get_order_file(self, dir_path: Path) -> Path:
        return dir_path / "_order.json"

    def _load_order(self, dir_path: Path, current_items: list[str]) -> list[str]:
        """加载自定义排序列表，合并新项目（如新增的目录/文件）"""
        order_file = self._get_order_file(dir_path)
        if not order_file.exists():
            return current_items
        try:
            saved = json.loads(order_file.read_text(encoding="utf-8"))
        except Exception:
            return current_items
        # 合并：保留已排序的项，追加新出现的项
        result = [s for s in saved if s in current_items]
        for item in current_items:
            if item not in result:
                result.append(item)
        return result

    def _save_order(self, dir_path: Path, ordered: list[str]) -> None:
        self._get_order_file(dir_path).write_text(
            json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")

    def reorder_categories(self, ordered_names: list[str]) -> None:
        """重排设定分类的显示顺序"""
        settings_dir = self._project_dir() / "settings"
        self._save_order(settings_dir, ordered_names)
        self._logger.log("设定分类顺序已更新", self.ID, "INFO")

    def reorder_docs(self, category: str, ordered_names: list[str]) -> None:
        """重排指定分类下文档的显示顺序"""
        cat_dir = self._project_dir() / "settings" / category
        self._save_order(cat_dir, ordered_names)
        self._logger.log(f"设定文档顺序已更新: {category}", self.ID, "INFO")

    def rename_category(self, old_name: str, new_name: str) -> None:
        """重命名设定分类（目录级 rename）"""
        old_dir = self._project_dir() / "settings" / old_name
        new_dir = self._project_dir() / "settings" / new_name
        if old_dir.exists():
            old_dir.rename(new_dir)
            self._logger.log(f"设定分类重命名: {old_name} → {new_name}", self.ID, "INFO")

    def rename_setting(self, category: str, old_name: str, new_name: str) -> None:
        """重命名设定文档（文件级 rename）"""
        old_path = self._project_dir() / "settings" / category / f"{old_name}.md"
        new_path = self._project_dir() / "settings" / category / f"{new_name}.md"
        if old_path.exists():
            old_path.rename(new_path)
            self._logger.log(f"设定文档重命名: {category}/{old_name} → {new_name}", self.ID, "INFO")

    # ---- 向后兼容别名（废弃，保留不删避免旧测试失败） ----
    def list_power_systems(self) -> list[str]:
        return self.list_docs("力量体系")

    def get_power_system(self, name: str) -> Optional[str]:
        return self.get_setting("力量体系", name)

    def save_power_system(self, name: str, content: str) -> None:
        self.save_setting("力量体系", name, content)

    def delete_power_system(self, name: str) -> None:
        self.delete_setting("力量体系", name)

    def list_factions(self) -> list[str]:
        return self.list_categories()

    def list_characters(self, faction: str) -> list[str]:
        return self.list_docs(faction)

    def get_character(self, faction: str, name: str) -> Optional[str]:
        return self.get_setting(faction, name)

    def save_character(self, faction: str, name: str, content: str,
                       cross_factions: Optional[list[str]] = None) -> None:
        self.save_setting(faction, name, content)
        if cross_factions:
            for cf in cross_factions:
                if cf != faction:
                    self.save_setting(cf, name, content)

    def delete_character(self, faction: str, name: str, remove_cross: bool = True) -> None:
        self.delete_setting(faction, name)

    # ==================== 全局搜索 ====================

    def search_content(self, keyword: str) -> list[dict]:
        """在当前项目中搜索包含关键词的内容
        Returns:
            [{"type": "outline"|"setting"|"content", "path": str, "snippet": str}, ...]
        """
        results = []
        proj_dir = self._project_dir()
        keyword_lower = keyword.lower()
        for md_file in proj_dir.rglob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8")
                if keyword_lower in text.lower():
                    idx = text.lower().index(keyword_lower)
                    start = max(0, idx - 40)
                    end = min(len(text), idx + len(keyword) + 40)
                    snippet = ("..." if start > 0 else "") + text[start:end] + ("..." if end < len(text) else "")
                    rel_path = str(md_file.relative_to(proj_dir))
                    results.append({"path": rel_path, "snippet": snippet})
            except Exception:
                pass
        return results[:20]  # 最多 20 条

    # ==================== 导出 ====================

    def export_settings(
        self, categories: Optional[list[str]] = None,
        output_dir: str = "", merge: bool = False,
    ) -> str:
        """导出设定为 Markdown 文件
        Args:
            categories: 要导出的分类列表，None=全部
            output_dir: 输出目录
            merge: True 合并为单文件
        Returns: 导出文件路径
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        project = self._current_project or "unknown"
        cats = categories or self.list_categories()

        if merge:
            content = f"# {project} 设定导出\n导出时间: {timestamp}\n\n"
            for cat in cats:
                content += f"## {cat}\n\n"
                for doc in self.list_docs(cat):
                    text = self.get_setting(cat, doc) or ""
                    content += f"### {doc}\n\n{text}\n\n"
            file_path = output_path / f"{project}_settings_{timestamp}.md"
            file_path.write_text(content, encoding="utf-8")
        else:
            for cat in cats:
                cat_out = output_path / cat
                cat_out.mkdir(exist_ok=True)
                for doc in self.list_docs(cat):
                    text = self.get_setting(cat, doc)
                    if text:
                        (cat_out / f"{doc}.md").write_text(text, encoding="utf-8")
            file_path = output_path

        self._logger.log(f"导出设定: {len(cats)} 个分类 → {output_dir}", self.ID, "INFO")
        return str(file_path)

    # ==================== 内部实现 ====================

    def _project_dir(self) -> Path:
        """获取当前项目目录"""
        if not self._current_project:
            raise ValueError("未选择项目")
        return self._projects_dir / self._current_project

    def _load_project_meta(self, name: str) -> Optional[ProjectMeta]:
        """加载项目元数据"""
        meta_path = self._projects_dir / name / "project.json"
        if not meta_path.exists():
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ProjectMeta(
                name=data.get("name", name),
                description=data.get("description", ""),
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
                current_step=data.get("current_step", 1),
            )
        except (json.JSONDecodeError, KeyError):
            return None

    def _save_project_meta(self, meta: ProjectMeta) -> None:
        """保存项目元数据"""
        meta_path = self._projects_dir / meta.name / "project.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "name": meta.name,
                "description": meta.description,
                "created_at": meta.created_at,
                "updated_at": meta.updated_at,
                "current_step": meta.current_step,
            }, f, ensure_ascii=False, indent=2)

    def _load_outline_index(self) -> Optional[dict]:
        """加载大纲索引"""
        index_path = self._project_dir() / "outline.json"
        if not index_path.exists():
            return {"nodes": {}, "root_id": None}
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            # 尝试重建
            self._logger.log("outline.json 损坏，尝试重建索引", self.ID, "WARNING")
            return self._rebuild_outline_index()

    def _save_outline_index(self, project_dir: Path, index: dict) -> None:
        """保存大纲索引"""
        index_path = project_dir / "outline.json"
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    def _rebuild_outline_index(self) -> dict:
        """从 outline/ 目录重建大纲索引"""
        outline_dir = self._project_dir() / "outline"
        if not outline_dir.exists():
            return {"nodes": {}, "root_id": None}

        nodes = {}
        for md_file in outline_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            # 简单重建：从文件名推断信息
            node_id = str(uuid.uuid4())
            nodes[node_id] = {
                "node_id": node_id,
                "title": md_file.stem,
                "level": 1,
                "parent_id": None,
                "children_ids": [],
                "status": "todo",
                "order": 0,
                "content": content,
                "file": f"outline/{md_file.name}",
                "word_count": len(content.replace(" ", "").replace("\n", "")),
                "created_at": "",
                "updated_at": "",
            }

        index = {"nodes": nodes, "root_id": None}
        self._save_outline_index(self._project_dir(), index)
        return index

    def _write_node_content(self, node: OutlineNode) -> None:
        """将节点内容写入 .md 文件"""
        if node.level.value <= 4:  # L1~L4 存 outline/
            content_dir = self._project_dir() / "outline"
        else:  # L5 存 content/
            content_dir = self._project_dir() / "content"
        content_dir.mkdir(parents=True, exist_ok=True)
        (content_dir / node.file).write_text(node.content or "", encoding="utf-8")

    def _delete_node_file(self, node: OutlineNode) -> None:
        """删除节点对应的 .md 文件"""
        if node.level.value <= 4:
            file_path = self._project_dir() / "outline" / node.file
        else:
            file_path = self._project_dir() / "content" / node.file
        if file_path.exists():
            file_path.unlink()

    def _update_index_node(self, node: OutlineNode) -> None:
        """更新索引中的单个节点"""
        index = self._load_outline_index()
        index["nodes"][node.node_id] = self._node_to_dict(node)
        self._save_outline_index(self._project_dir(), index)

    def _next_order(self, parent_id: Optional[str]) -> int:
        """获取下一个排序序号"""
        if parent_id is None:
            return 0
        children = self.get_children(parent_id)
        return children[-1].order + 1 if children else 0

    @staticmethod
    def _node_to_dict(node: OutlineNode) -> dict:
        return {
            "node_id": node.node_id,
            "title": node.title,
            "level": node.level.value,
            "parent_id": node.parent_id,
            "children_ids": node.children_ids,
            "status": node.status.value,
            "order": node.order,
            "content": node.content,
            "file": node.file,
            "word_count": node.word_count,
            "created_at": node.created_at,
            "updated_at": node.updated_at,
        }

    @staticmethod
    def _dict_to_node(d: dict) -> OutlineNode:
        return OutlineNode(
            node_id=d.get("node_id", ""),
            title=d.get("title", ""),
            level=OutlineLevel(d.get("level", 1)),
            parent_id=d.get("parent_id"),
            children_ids=d.get("children_ids", []),
            status=NodeStatus(d.get("status", "todo")),
            order=d.get("order", 0),
            content=d.get("content", ""),
            file=d.get("file", ""),
            word_count=d.get("word_count", 0),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )

    @staticmethod
    def _validate_name(name: str) -> None:
        """校验项目名称"""
        if not name or not name.strip():
            raise ValueError("项目名不能为空")
        # 简化校验：不允许路径分隔符
        if "/" in name or "\\" in name or ":" in name:
            raise ValueError("项目名不能包含 / \\ : 等特殊字符")
