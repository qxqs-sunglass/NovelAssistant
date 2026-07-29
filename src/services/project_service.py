"""
项目与设定服务 — ProjectService v2.0

核心职责:
  - 项目管理（创建/删除/切换小说项目）
  - 大纲层级管理（5 级内容体系：大纲→卷纲→简纲→章纲→正文）
  - 通用自由分类设定管理（Markdown 文档）
  - ★ CharacterService — 角色结构化 CRUD + 阵营管理 + 多标签关联
  - ★ ForeshadowService — 伏笔条目 CRUD + 隐藏/显示 + AI 上下文

设计模式: 服务层 (Service Layer) + 仓储模式 (Repository)
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════
# 枚举与数据类
# ═══════════════════════════════════════════════════

class OutlineLevel(Enum):
    """大纲层级 (v1.0)"""
    OUTLINE = 1   # L1 大纲（全书）
    VOLUME = 2    # L2 卷纲
    BRIEF = 3     # L3 简纲（10~20章）
    CHAPTER = 4   # L4 章纲（3~6章）
    CONTENT = 5   # L5 正文


class NodeStatus(Enum):
    """节点状态 (v1.0)"""
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    IGNORED = "ignored"


@dataclass
class ProjectMeta:
    """项目元数据 (v1.0)"""
    name: str
    created_at: str = ""
    updated_at: str = ""
    description: str = ""
    current_step: int = 1


@dataclass
class OutlineNode:
    """大纲节点 (v1.0)"""
    node_id: str
    title: str
    level: OutlineLevel
    parent_id: str | None
    children_ids: list[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.TODO
    order: int = 0
    content: str = ""
    word_count: int = 0
    created_at: str = ""
    updated_at: str = ""


# 向后兼容别名 (v1.0)
Chapter = OutlineNode
ChapterStatus = NodeStatus


# ★ v2.0 角色与阵营数据类
@dataclass
class Character:
    """角色数据模型"""
    char_id: str
    name: str
    gender: str = ""
    birthday: str = ""
    age: str = ""
    bio: str = ""
    camp_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Camp:
    """阵营数据模型"""
    camp_id: str
    name: str
    description: str = ""
    created_at: str = ""


# ★ v2.0 伏笔数据类
@dataclass
class Foreshadow:
    """伏笔条目"""
    foreshadow_id: str
    content: str
    hidden: bool = False
    created_at: str = ""
    order: int = 0


# ═══════════════════════════════════════════════════
# ★ v2.0 CharacterService
# ═══════════════════════════════════════════════════

class CharacterService:
    """角色与阵营管理服务"""

    ID = "CharacterService"

    def __init__(self, project_dir: str, event_bus=None, logger=None):
        self._project_dir = Path(project_dir)
        self._event_bus = event_bus
        self._logger = logger
        self._chars_dir = self._project_dir / "characters"
        self._camps_file = self._project_dir / "camps" / "index.json"
        self._camps_order_file = self._project_dir / "camps" / "_order.json"
        self._index_file = self._chars_dir / "index.json"

    # ── 内部辅助 ──
    def _log(self, msg: str, level: str = "INFO"):
        if self._logger:
            self._logger.log(msg, self.ID, level)

    def _publish(self, name: str, data: dict):
        if self._event_bus:
            self._event_bus.publish(name, data, self.ID)

    def _read_json(self, path: Path) -> dict | list:
        if not path.exists():
            return {} if path.suffix == ".json" else []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, path: Path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # ── 角色 CRUD ──
    def list_characters(self) -> list[Character]:
        """列出所有角色（bio 按需延迟加载）"""
        index = self._read_json(self._index_file)
        if not isinstance(index, list):
            # 兼容旧格式或损坏 → 重建
            index = self._rebuild_character_index()
        result = []
        for entry in index:
            c = Character(
                char_id=entry.get("char_id", ""),
                name=entry.get("name", ""),
                gender=entry.get("gender", ""),
                camp_ids=entry.get("camp_ids", []),
                created_at=entry.get("created_at", ""),
                updated_at=entry.get("updated_at", ""),
            )
            # 延迟加载 data.json 中的 birthday/age
            data_file = self._chars_dir / c.char_id / "data.json"
            data = self._read_json(data_file)
            if isinstance(data, dict):
                c.birthday = data.get("birthday", "")
                c.age = data.get("age", "")
            result.append(c)
        return result

    def get_character(self, char_id: str) -> Optional[Character]:
        """获取完整角色信息（含 bio）"""
        data_file = self._chars_dir / char_id / "data.json"
        profile_file = self._chars_dir / char_id / "profile.md"
        if not data_file.exists():
            return None

        data = self._read_json(data_file)
        if not isinstance(data, dict):
            return None

        # 从 index 获取 name（或从 data 获取）
        index = self._read_json(self._index_file)
        name = ""
        camp_ids = []
        for entry in (index if isinstance(index, list) else []):
            if entry.get("char_id") == char_id:
                name = entry.get("name", "")
                camp_ids = entry.get("camp_ids", [])
                break

        c = Character(
            char_id=char_id,
            name=name or data.get("name", ""),
            gender=data.get("gender", ""),
            birthday=data.get("birthday", ""),
            age=data.get("age", ""),
            camp_ids=camp_ids,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
        # 加载 bio
        if profile_file.exists():
            with open(profile_file, "r", encoding="utf-8") as f:
                c.bio = f.read()
        return c

    def create_character(self, name: str) -> Character:
        """创建新角色"""
        if not name or not name.strip():
            raise ValueError("角色名称不能为空")

        char_id = str(uuid.uuid4())
        now = self._now()

        # 创建目录和文件
        char_dir = self._chars_dir / char_id
        char_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "name": name.strip(),
            "gender": "",
            "birthday": "",
            "age": "",
            "camp_ids": [],
            "created_at": now,
            "updated_at": now,
        }
        self._write_json(char_dir / "data.json", data)
        # 空 bio
        with open(char_dir / "profile.md", "w", encoding="utf-8") as f:
            f.write("")

        # 更新 index
        index = self._read_json(self._index_file)
        if not isinstance(index, list):
            index = []
        index.append({
            "char_id": char_id,
            "name": name.strip(),
            "gender": "",
            "camp_ids": [],
            "created_at": now,
            "updated_at": now,
        })
        self._write_json(self._index_file, index)

        self._log(f"创建角色: {name}")
        self._publish("character:created", {"char_id": char_id, "name": name})

        return Character(
            char_id=char_id, name=name.strip(),
            created_at=now, updated_at=now,
        )

    def update_character(self, char_id: str,
                         name: str = None,
                         gender: str = None,
                         birthday: str = None,
                         age: str = None,
                         bio: str = None,
                         camp_ids: list[str] = None) -> Character:
        """更新角色字段（None 表示不修改）"""
        char_dir = self._chars_dir / char_id
        data_file = char_dir / "data.json"
        if not data_file.exists():
            raise ValueError(f"角色不存在: {char_id}")

        now = self._now()
        data = self._read_json(data_file)
        if not isinstance(data, dict):
            data = {}

        # 更新固定字段
        if name is not None:
            data["name"] = name.strip()
        if gender is not None:
            data["gender"] = gender
        if birthday is not None:
            data["birthday"] = birthday
        if age is not None:
            data["age"] = age
        if camp_ids is not None:
            data["camp_ids"] = camp_ids
        data["updated_at"] = now
        self._write_json(data_file, data)

        # 更新 bio (Markdown 文件)
        if bio is not None:
            with open(char_dir / "profile.md", "w", encoding="utf-8") as f:
                f.write(bio)

        # 更新 index
        index = self._read_json(self._index_file)
        if isinstance(index, list):
            for entry in index:
                if entry.get("char_id") == char_id:
                    if name is not None:
                        entry["name"] = name.strip()
                    if gender is not None:
                        entry["gender"] = gender
                    if camp_ids is not None:
                        entry["camp_ids"] = camp_ids
                    entry["updated_at"] = now
                    break
            self._write_json(self._index_file, index)

        self._log(f"更新角色: {char_id}")
        self._publish("character:updated", {"char_id": char_id, "name": data.get("name", "")})

        return self.get_character(char_id)

    def delete_character(self, char_id: str) -> None:
        """删除角色及其所有数据文件"""
        char_dir = self._chars_dir / char_id
        if not char_dir.exists():
            raise ValueError(f"角色不存在: {char_id}")

        shutil.rmtree(char_dir)

        # 更新 index
        index = self._read_json(self._index_file)
        if isinstance(index, list):
            index = [e for e in index if e.get("char_id") != char_id]
            self._write_json(self._index_file, index)

        self._log(f"删除角色: {char_id}")
        self._publish("character:deleted", {"char_id": char_id})

    # ── 角色查询 ──
    def get_characters_by_camp(self, camp_id: str) -> list[Character]:
        """获取属于指定阵营的所有角色"""
        all_chars = self.list_characters()
        return [c for c in all_chars if camp_id in c.camp_ids]

    def search_characters(self, keyword: str) -> list[Character]:
        """按名称搜索角色"""
        all_chars = self.list_characters()
        kw = keyword.lower()
        return [c for c in all_chars if kw in c.name.lower()]

    # ── 阵营 CRUD ──
    def list_camps(self) -> list[Camp]:
        """列出所有阵营（按 _order.json 排序）"""
        data = self._read_json(self._camps_file)
        if not isinstance(data, list):
            return []
        camps = [
            Camp(
                camp_id=e.get("camp_id", ""),
                name=e.get("name", ""),
                description=e.get("description", ""),
                created_at=e.get("created_at", ""),
            )
            for e in data
        ]
        # 按 _order.json 排序
        order = self._read_json(self._camps_order_file)
        if isinstance(order, list):
            order_map = {cid: i for i, cid in enumerate(order) if isinstance(cid, str)}
            camps.sort(key=lambda c: order_map.get(c.camp_id, 9999))
        return camps

    def _save_camps_order(self, ordered_ids: list[str]):
        """保存阵营显示顺序"""
        self._write_json(self._camps_order_file, ordered_ids)

    def reorder_camps(self, ordered_ids: list[str]) -> None:
        """重排阵营显示顺序（传入完整的 camp_id 列表）"""
        all_camps = self.list_camps()
        existing_ids = {c.camp_id for c in all_camps}
        # 确保所有现有阵营都在列表中
        full_order = [cid for cid in ordered_ids if cid in existing_ids]
        for c in all_camps:
            if c.camp_id not in full_order:
                full_order.append(c.camp_id)
        self._save_camps_order(full_order)
        self._log(f"阵营顺序已更新")
        self._publish("camp:updated", {"camp_id": ""})

    def get_camp(self, camp_id: str) -> Optional[Camp]:
        for c in self.list_camps():
            if c.camp_id == camp_id:
                return c
        return None

    def create_camp(self, name: str, description: str = "") -> Camp:
        """创建新阵营"""
        if not name or not name.strip():
            raise ValueError("阵营名称不能为空")

        camp_id = str(uuid.uuid4())
        now = self._now()
        camps = self._read_json(self._camps_file)
        if not isinstance(camps, list):
            camps = []
        camps.append({
            "camp_id": camp_id, "name": name.strip(),
            "description": description, "created_at": now,
        })
        self._write_json(self._camps_file, camps)

        # 添加到排序列表末尾
        order = self._read_json(self._camps_order_file)
        if not isinstance(order, list):
            order = []
        order.append(camp_id)
        self._write_json(self._camps_order_file, order)

        self._log(f"创建阵营: {name}")
        self._publish("camp:created", {"camp_id": camp_id, "name": name})
        return Camp(camp_id=camp_id, name=name.strip(), description=description, created_at=now)

    def update_camp(self, camp_id: str, name: str = None, description: str = None) -> Camp:
        """更新阵营信息"""
        camps = self._read_json(self._camps_file)
        if not isinstance(camps, list):
            raise ValueError(f"阵营不存在: {camp_id}")
        for c in camps:
            if c.get("camp_id") == camp_id:
                if name is not None:
                    c["name"] = name.strip()
                if description is not None:
                    c["description"] = description
                self._write_json(self._camps_file, camps)
                self._log(f"更新阵营: {camp_id}")
                self._publish("camp:updated", {"camp_id": camp_id, "name": c.get("name", "")})
                return self.get_camp(camp_id)
        raise ValueError(f"阵营不存在: {camp_id}")

    def delete_camp(self, camp_id: str) -> None:
        """删除阵营，并清理所有关联角色的 camp_ids"""
        camps = self._read_json(self._camps_file)
        if not isinstance(camps, list):
            return
        camps = [c for c in camps if c.get("camp_id") != camp_id]
        self._write_json(self._camps_file, camps)

        # 从排序列表移除
        order = self._read_json(self._camps_order_file)
        if isinstance(order, list):
            order = [cid for cid in order if cid != camp_id]
            self._write_json(self._camps_order_file, order)

        # 清理所有角色的 camp_ids 引用
        for char in self.list_characters():
            if camp_id in char.camp_ids:
                new_camp_ids = [cid for cid in char.camp_ids if cid != camp_id]
                self.update_character(char.char_id, camp_ids=new_camp_ids)

        self._log(f"删除阵营: {camp_id}")
        self._publish("camp:deleted", {"camp_id": camp_id})

    # ── AI 上下文 ──
    def get_ai_context(self) -> str:
        """获取所有角色和阵营摘要，供 AI 对话上下文使用"""
        parts = []
        camps = self.list_camps()
        if camps:
            parts.append("【阵营列表】")
            for c in camps:
                parts.append(f"- {c.name}" + (f": {c.description}" if c.description else ""))

        chars = self.list_characters()
        if chars:
            parts.append("\n【角色列表】")
            for c in chars:
                info_parts = [c.name]
                if c.gender:
                    info_parts.append(c.gender)
                if c.age:
                    info_parts.append(f"{c.age}岁")
                cam_names = []
                for cid in c.camp_ids:
                    camp = self.get_camp(cid)
                    if camp:
                        cam_names.append(camp.name)
                if cam_names:
                    info_parts.append(f"所属: {', '.join(cam_names)}")
                parts.append(f"- {' | '.join(info_parts)}")
        return "\n".join(parts) if parts else ""

    # ── 索引重建 ──
    def _rebuild_character_index(self) -> list[dict]:
        """从 characters/ 目录重建 index.json"""
        index = []
        if self._chars_dir.exists():
            for char_dir in sorted(self._chars_dir.iterdir()):
                if char_dir.is_dir():
                    data_file = char_dir / "data.json"
                    if data_file.exists():
                        data = self._read_json(data_file)
                        if isinstance(data, dict):
                            index.append({
                                "char_id": char_dir.name,
                                "name": data.get("name", ""),
                                "gender": data.get("gender", ""),
                                "camp_ids": data.get("camp_ids", []),
                                "created_at": data.get("created_at", ""),
                                "updated_at": data.get("updated_at", ""),
                            })
        if index:
            self._write_json(self._index_file, index)
        return index


# ═══════════════════════════════════════════════════
# ★ v2.0 ForeshadowService
# ═══════════════════════════════════════════════════

class ForeshadowService:
    """伏笔管理服务"""

    ID = "ForeshadowService"

    def __init__(self, project_dir: str, event_bus=None, logger=None):
        self._project_dir = Path(project_dir)
        self._event_bus = event_bus
        self._logger = logger
        self._data_file = self._project_dir / "foreshadowing.json"

    def _log(self, msg: str, level: str = "INFO"):
        if self._logger:
            self._logger.log(msg, self.ID, level)

    def _publish(self, name: str, data: dict):
        if self._event_bus:
            self._event_bus.publish(name, data, self.ID)

    def _read(self) -> list[dict]:
        if not self._data_file.exists():
            return []
        with open(self._data_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: list[dict]):
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._data_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # ── 伏笔 CRUD ──
    def list_foreshadows(self, include_hidden: bool = False) -> list[Foreshadow]:
        """列出伏笔条目"""
        raw = self._read()
        result = []
        for entry in raw:
            f = Foreshadow(
                foreshadow_id=entry.get("foreshadow_id", ""),
                content=entry.get("content", ""),
                hidden=entry.get("hidden", False),
                created_at=entry.get("created_at", ""),
                order=entry.get("order", 0),
            )
            if include_hidden or not f.hidden:
                result.append(f)
        # 按 order 排序
        result.sort(key=lambda x: x.order)
        return result

    def get_foreshadow(self, foreshadow_id: str) -> Optional[Foreshadow]:
        """获取单条伏笔"""
        raw = self._read()
        for entry in raw:
            if entry.get("foreshadow_id") == foreshadow_id:
                return Foreshadow(
                    foreshadow_id=entry.get("foreshadow_id", ""),
                    content=entry.get("content", ""),
                    hidden=entry.get("hidden", False),
                    created_at=entry.get("created_at", ""),
                    order=entry.get("order", 0),
                )
        return None

    def add_foreshadow(self, content: str) -> Foreshadow:
        """添加新伏笔条目"""
        if not content or not content.strip():
            raise ValueError("伏笔内容不能为空")

        raw = self._read()
        foreshadow_id = str(uuid.uuid4())
        now = self._now()
        max_order = max((e.get("order", 0) for e in raw), default=-1)
        entry = {
            "foreshadow_id": foreshadow_id,
            "content": content.strip(),
            "hidden": False,
            "created_at": now,
            "order": max_order + 1,
        }
        raw.append(entry)
        self._write(raw)

        self._log(f"添加伏笔: {foreshadow_id}")
        self._publish("foreshadow:created", {"foreshadow_id": foreshadow_id})

        return Foreshadow(
            foreshadow_id=foreshadow_id, content=content.strip(),
            hidden=False, created_at=now, order=max_order + 1,
        )

    def update_foreshadow(self, foreshadow_id: str, content: str = None) -> Foreshadow:
        """更新伏笔内容"""
        raw = self._read()
        for entry in raw:
            if entry.get("foreshadow_id") == foreshadow_id:
                if content is not None:
                    entry["content"] = content.strip()
                self._write(raw)
                self._log(f"更新伏笔: {foreshadow_id}")
                self._publish("foreshadow:updated", {"foreshadow_id": foreshadow_id})
                return self.get_foreshadow(foreshadow_id)
        raise ValueError(f"伏笔不存在: {foreshadow_id}")

    def delete_foreshadow(self, foreshadow_id: str) -> None:
        """删除伏笔条目"""
        raw = self._read()
        new_raw = [e for e in raw if e.get("foreshadow_id") != foreshadow_id]
        if len(new_raw) == len(raw):
            raise ValueError(f"伏笔不存在: {foreshadow_id}")
        # 重整 order
        for i, entry in enumerate(new_raw):
            entry["order"] = i
        self._write(new_raw)

        self._log(f"删除伏笔: {foreshadow_id}")
        self._publish("foreshadow:deleted", {"foreshadow_id": foreshadow_id})

    def toggle_hidden(self, foreshadow_id: str) -> Foreshadow:
        """切换伏笔的 hidden 状态"""
        f = self.get_foreshadow(foreshadow_id)
        if f is None:
            raise ValueError(f"伏笔不存在: {foreshadow_id}")
        return self.set_hidden(foreshadow_id, not f.hidden)

    def set_hidden(self, foreshadow_id: str, hidden: bool) -> Foreshadow:
        """设置伏笔隐藏状态"""
        raw = self._read()
        for entry in raw:
            if entry.get("foreshadow_id") == foreshadow_id:
                entry["hidden"] = hidden
                self._write(raw)
                self._log(f"伏笔 {'隐藏' if hidden else '显示'}: {foreshadow_id}")
                self._publish("foreshadow:toggled", {"foreshadow_id": foreshadow_id, "hidden": hidden})
                return self.get_foreshadow(foreshadow_id)
        raise ValueError(f"伏笔不存在: {foreshadow_id}")

    # ── AI 上下文 ──
    def get_ai_context(self) -> str:
        """获取所有未隐藏伏笔的格式化文本"""
        items = self.list_foreshadows(include_hidden=False)
        if not items:
            return ""
        lines = ["【当前伏笔线索】"]
        for i, f in enumerate(items, 1):
            lines.append(f"{i}. {f.content}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════
# ProjectService（主服务，v1.0 核心 + v2.0 集成）
# ═══════════════════════════════════════════════════

class ProjectService:
    """项目与设定服务"""

    ID = "ProjectService"

    def __init__(self, workspace_dir: str, event_bus=None, logger=None):
        self._workspace_dir = Path(workspace_dir).resolve()
        self._event_bus = event_bus
        self._logger = logger
        self._current_project: str | None = None

        # ★ v2.0 子服务（懒加载）
        self._character_service: Optional[CharacterService] = None
        self._foreshadow_service: Optional[ForeshadowService] = None

    @property
    def character_service(self) -> CharacterService:
        """★ v2.0: 懒加载 CharacterService"""
        if self._character_service is None:
            project_dir = self._get_project_dir()
            self._character_service = CharacterService(
                str(project_dir) if project_dir else str(self._workspace_dir),
                self._event_bus, self._logger,
            )
        return self._character_service

    @property
    def foreshadow_service(self) -> ForeshadowService:
        """★ v2.0: 懒加载 ForeshadowService"""
        if self._foreshadow_service is None:
            project_dir = self._get_project_dir()
            self._foreshadow_service = ForeshadowService(
                str(project_dir) if project_dir else str(self._workspace_dir),
                self._event_bus, self._logger,
            )
        return self._foreshadow_service

    @property
    def _projects_dir(self) -> Path:
        return self._workspace_dir / "projects"

    def _get_project_dir(self) -> Optional[Path]:
        if not self._current_project:
            return None
        return self._projects_dir / self._current_project

    def _log(self, msg: str, level: str = "INFO"):
        if self._logger:
            self._logger.log(msg, self.ID, level)

    def _publish(self, name: str, data: dict):
        if self._event_bus:
            self._event_bus.publish(name, data, self.ID)

    def _read_file(self, path: str) -> str:
        full = Path(path)
        if not full.is_absolute() and self._get_project_dir():
            full = self._get_project_dir() / path
        if not full.exists():
            return ""
        with open(full, "r", encoding="utf-8") as f:
            return f.read()

    def _write_file(self, path: str, content: str):
        full = Path(path)
        if not full.is_absolute() and self._get_project_dir():
            full = self._get_project_dir() / path
        full.parent.mkdir(parents=True, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def _read_json(self, path: str) -> dict | list:
        full = Path(path)
        if not full.is_absolute() and self._get_project_dir():
            full = self._get_project_dir() / path
        if not full.exists():
            return {} if full.suffix == ".json" else []
        with open(full, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, path: str, data):
        full = Path(path)
        if not full.is_absolute() and self._get_project_dir():
            full = self._get_project_dir() / path
        full.parent.mkdir(parents=True, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # ═══════════════════════════════════════════════
    # 项目管理 (v1.0)
    # ═══════════════════════════════════════════════

    def list_projects(self) -> list[ProjectMeta]:
        """列出所有项目"""
        if not self._projects_dir.exists():
            return []
        result = []
        for d in sorted(self._projects_dir.iterdir()):
            if d.is_dir():
                pj = d / "project.json"
                if pj.exists():
                    data = self._read_json(str(pj))
                    if isinstance(data, dict):
                        result.append(ProjectMeta(
                            name=d.name,
                            description=data.get("description", ""),
                            created_at=data.get("created_at", ""),
                            updated_at=data.get("updated_at", ""),
                            current_step=data.get("current_step", 1),
                        ))
        return result

    def create_project(self, name: str, description: str = "") -> ProjectMeta:
        """创建项目并自动创建根大纲节点"""
        if not name or not name.strip():
            raise ValueError("项目名称不能为空")
        proj_dir = self._projects_dir / name
        if proj_dir.exists():
            raise ValueError(f"项目已存在: {name}")

        now = self._now()
        proj_dir.mkdir(parents=True, exist_ok=True)

        # project.json
        self._write_json(str(proj_dir / "project.json"), {
            "name": name, "description": description,
            "created_at": now, "updated_at": now, "current_step": 1,
        })

        # 创建根大纲节点
        root = OutlineNode(
            node_id=str(uuid.uuid4()), title=f"{name}·全书大纲",
            level=OutlineLevel.OUTLINE, parent_id=None,
            status=NodeStatus.TODO, order=0,
            created_at=now, updated_at=now,
        )
        self._current_project = name
        self._save_outline_node(root)
        self._log(f"创建项目: {name}")
        return ProjectMeta(name=name, description=description, created_at=now, updated_at=now)

    def delete_project(self, name: str) -> None:
        proj_dir = self._projects_dir / name
        if not proj_dir.exists():
            raise ValueError(f"项目不存在: {name}")
        shutil.rmtree(proj_dir)
        if self._current_project == name:
            self._current_project = None
            # ★ v2.0: 重置子服务
            self._character_service = None
            self._foreshadow_service = None
        self._log(f"删除项目: {name}")

    def get_current_project(self) -> Optional[str]:
        return self._current_project

    def switch_project(self, name: str) -> None:
        proj_dir = self._projects_dir / name
        if not proj_dir.exists():
            raise ValueError(f"项目不存在: {name}")
        self._current_project = name
        # ★ v2.0: 重置子服务（指向新项目目录）
        self._character_service = None
        self._foreshadow_service = None
        self._publish("project:switched", {"project_name": name})
        self._log(f"切换项目: {name}")

    # ═══════════════════════════════════════════════
    # 大纲层级管理 (v1.0)
    # ═══════════════════════════════════════════════

    def _load_outline(self) -> dict:
        if not self._get_project_dir():
            return {"nodes": {}, "root_id": None}
        outline_file = self._get_project_dir() / "outline.json"
        if not outline_file.exists():
            return {"nodes": {}, "root_id": None}
        data = self._read_json(str(outline_file))
        if not isinstance(data, dict):
            return {"nodes": {}, "root_id": None}
        return data

    def _save_outline(self, data: dict):
        if self._get_project_dir():
            self._write_json(str(self._get_project_dir() / "outline.json"), data)

    def _save_outline_node(self, node: OutlineNode):
        """保存节点内容到 outline/ 目录"""
        proj = self._get_project_dir()
        if not proj:
            return
        outline = self._load_outline()
        nodes = outline.get("nodes", {})
        if isinstance(nodes, list):
            nodes = {n.get("node_id", ""): n for n in nodes}

        # 确定文件名
        level_names = {1: "outline_L1", 2: "volume", 3: "brief", 4: "chapter", 5: "content"}
        prefix = level_names.get(node.level.value, "node")
        filename = f"outline/{prefix}_{node.node_id[:8]}.md"

        # 保存内容
        self._write_file(filename, node.content)

        # 更新索引
        nodes[node.node_id] = {
            "node_id": node.node_id, "title": node.title,
            "level": node.level.value, "parent_id": node.parent_id,
            "children_ids": node.children_ids, "status": node.status.value,
            "order": node.order, "file": filename,
            "word_count": node.word_count if hasattr(node, 'word_count') else len(node.content),
            "created_at": node.created_at, "updated_at": node.updated_at,
        }
        if outline.get("root_id") is None and node.level == OutlineLevel.OUTLINE:
            outline["root_id"] = node.node_id
        outline["nodes"] = nodes
        self._save_outline(outline)

    def get_outline_tree(self) -> list[OutlineNode]:
        """获取完整大纲树"""
        data = self._load_outline()
        nodes_data = data.get("nodes", {})
        if isinstance(nodes_data, list):
            nodes_data = {n.get("node_id", ""): n for n in nodes_data}
        result = []
        for nid, nd in nodes_data.items():
            if not isinstance(nd, dict):
                continue
            result.append(OutlineNode(
                node_id=nid,
                title=nd.get("title", ""),
                level=OutlineLevel(nd.get("level", 1)),
                parent_id=nd.get("parent_id"),
                children_ids=nd.get("children_ids", []),
                status=NodeStatus(nd.get("status", "todo")),
                order=nd.get("order", 0),
                word_count=nd.get("word_count", 0),
                created_at=nd.get("created_at", ""),
                updated_at=nd.get("updated_at", ""),
            ))
        return result

    def get_node(self, node_id: str) -> Optional[OutlineNode]:
        for n in self.get_outline_tree():
            if n.node_id == node_id:
                data = self._load_outline()
                nodes = data.get("nodes", {})
                if isinstance(nodes, list):
                    nodes = {nd.get("node_id", ""): nd for nd in nodes}
                nd = nodes.get(node_id, {})
                content = ""
                if isinstance(nd, dict):
                    # ★ v1 兼容: content 可能直接嵌在 outline.json 中
                    if nd.get("content"):
                        content = nd["content"]
                    # ★ v2: 从独立 .md 文件加载（优先，因为更完整）
                    if nd.get("file"):
                        file_content = self._read_file(nd["file"])
                        # 容错：原路径找不到时按 node_id+level 推断
                        if not file_content and isinstance(nd.get("level"), int):
                            prefix = {1: "outline_L1", 2: "volume", 3: "brief",
                                      4: "chapter", 5: "content"}.get(nd["level"], "node")
                            alt = f"outline/{prefix}_{node_id[:8]}.md"
                            if alt != nd["file"]:
                                file_content = self._read_file(alt)
                            # L5 v1 格式回退
                            if not file_content and nd["level"] == 5:
                                file_content = self._read_file(f"content/ch_{node_id[:8]}.md")
                        if file_content:
                            content = file_content
                n.content = content
                return n
        return None

    def get_children(self, parent_id: str) -> list[OutlineNode]:
        parent = self.get_node(parent_id)
        if parent is None:
            return []
        return [n for n in self.get_outline_tree() if n.node_id in parent.children_ids]

    def create_node(self, parent_id: str | None, title: str,
                    level: OutlineLevel, content: str = "",
                    order: int = -1) -> OutlineNode:
        now = self._now()
        node = OutlineNode(
            node_id=str(uuid.uuid4()), title=title, level=level,
            parent_id=parent_id, status=NodeStatus.TODO,
            order=order, content=content,
            word_count=len(content), created_at=now, updated_at=now,
        )
        # 确定 order
        if order < 0 and parent_id:
            siblings = self.get_children(parent_id)
            node.order = max((s.order for s in siblings), default=-1) + 1

        # 保存
        self._save_outline_node(node)
        # 更新父节点 children_ids
        if parent_id:
            outline = self._load_outline()
            nodes = outline.get("nodes", {})
            if isinstance(nodes, list):
                nodes = {n.get("node_id", ""): n for n in nodes}
            if parent_id in nodes:
                pnode = nodes[parent_id]
                if isinstance(pnode, dict):
                    kids = pnode.get("children_ids", [])
                    kids.append(node.node_id)
                    pnode["children_ids"] = kids
                outline["nodes"] = nodes
                self._save_outline(outline)

        self._publish("outline:tree_changed", {"project_name": self._current_project})
        return node

    def update_node(self, node_id: str, title: str = None,
                    content: str = None, status: NodeStatus = None) -> OutlineNode:
        outline = self._load_outline()
        nodes = outline.get("nodes", {})
        if isinstance(nodes, list):
            nodes = {n.get("node_id", ""): n for n in nodes}
        if node_id not in nodes:
            raise ValueError(f"节点不存在: {node_id}")

        nd = nodes[node_id]
        now = self._now()
        if title is not None:
            nd["title"] = title
        if status is not None:
            nd["status"] = status.value
        nd["updated_at"] = now
        if content is not None:
            nd["word_count"] = len(content)
            # 写入内容文件
            if nd.get("file"):
                self._write_file(nd["file"], content)

        outline["nodes"] = nodes
        self._save_outline(outline)
        self._publish("chapter:saved", {"node_id": node_id})
        return self.get_node(node_id)

    def delete_node(self, node_id: str) -> None:
        outline = self._load_outline()
        nodes = outline.get("nodes", {})
        if isinstance(nodes, list):
            nodes = {n.get("node_id", ""): n for n in nodes}
        if node_id not in nodes:
            return

        # 递归删除子节点
        def _collect_ids(nid):
            nd = nodes.get(nid, {})
            ids = [nid]
            for cid in nd.get("children_ids", []):
                ids.extend(_collect_ids(cid))
            return ids

        all_ids = _collect_ids(node_id)

        # 从父节点移除
        nd = nodes.get(node_id, {})
        pid = nd.get("parent_id")
        if pid and pid in nodes:
            pnode = nodes[pid]
            if isinstance(pnode, dict):
                pnode["children_ids"] = [
                    c for c in pnode.get("children_ids", []) if c not in all_ids
                ]

        # 删除节点
        for nid in all_ids:
            # 删除内容文件
            nd_info = nodes.get(nid, {})
            if isinstance(nd_info, dict) and nd_info.get("file"):
                fp = self._get_project_dir() / nd_info["file"] if self._get_project_dir() else None
                if fp and fp.exists():
                    fp.unlink()
            nodes.pop(nid, None)

        outline["nodes"] = nodes
        self._save_outline(outline)
        self._publish("outline:tree_changed", {"project_name": self._current_project})
        self._log(f"删除节点: {node_id}")

    def move_node(self, node_id: str, new_parent_id: str | None, new_order: int) -> None:
        outline = self._load_outline()
        nodes = outline.get("nodes", {})
        if isinstance(nodes, list):
            nodes = {n.get("node_id", ""): n for n in nodes}
        if node_id not in nodes:
            raise ValueError(f"节点不存在: {node_id}")

        nd = nodes[node_id]
        old_pid = nd.get("parent_id")
        # 从旧父节点移除
        if old_pid and old_pid in nodes:
            old_parent = nodes[old_pid]
            if isinstance(old_parent, dict):
                old_parent["children_ids"] = [
                    c for c in old_parent.get("children_ids", []) if c != node_id
                ]
        # 设置新父节点
        nd["parent_id"] = new_parent_id
        nd["order"] = new_order
        if new_parent_id and new_parent_id in nodes:
            new_parent = nodes[new_parent_id]
            if isinstance(new_parent, dict):
                kids = new_parent.get("children_ids", [])
                if node_id not in kids:
                    kids.append(node_id)
                new_parent["children_ids"] = kids

        outline["nodes"] = nodes
        self._save_outline(outline)
        self._publish("outline:tree_changed", {"project_name": self._current_project})

    def reorder_siblings(self, parent_id: str, ordered_ids: list[str]) -> None:
        outline = self._load_outline()
        nodes = outline.get("nodes", {})
        if isinstance(nodes, list):
            nodes = {n.get("node_id", ""): n for n in nodes}
        if parent_id in nodes:
            pnode = nodes[parent_id]
            if isinstance(pnode, dict):
                pnode["children_ids"] = ordered_ids
        for i, nid in enumerate(ordered_ids):
            if nid in nodes:
                nodes[nid]["order"] = i
        outline["nodes"] = nodes
        self._save_outline(outline)
        self._publish("outline:tree_changed", {"project_name": self._current_project})

    def split_node(self, node_id: str, split_titles: list[str]) -> list[OutlineNode]:
        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在: {node_id}")
        if node.level.value >= 5:
            raise ValueError("正文节点不允许拆分")
        new_level = OutlineLevel(node.level.value + 1)
        result = []
        for title in split_titles:
            child = self.create_node(node_id, title, new_level)
            result.append(child)
        return result

    def merge_nodes(self, child_ids: list[str], new_title: str) -> OutlineNode:
        if not child_ids:
            raise ValueError("child_ids 不能为空")
        first = self.get_node(child_ids[0])
        if first is None:
            raise ValueError("节点不存在")
        parent_id = first.parent_id
        for cid in child_ids[1:]:
            n = self.get_node(cid)
            if n is None:
                raise ValueError(f"节点不存在: {cid}")
            if n.parent_id != parent_id:
                raise ValueError("只能合并同父节点的兄弟节点")
        merged = self.create_node(parent_id, new_title, first.level)
        for cid in child_ids:
            self.move_node(cid, merged.node_id, -1)
        return merged

    def get_nodes_by_level(self, level: OutlineLevel) -> list[OutlineNode]:
        return [n for n in self.get_outline_tree() if n.level == level]

    def get_full_path(self, node_id: str) -> list[OutlineNode]:
        result = []
        nid = node_id
        while nid:
            node = self.get_node(nid)
            if node is None:
                break
            result.insert(0, node)
            nid = node.parent_id
        return result

    def get_workflow_step(self) -> int:
        if self._get_project_dir():
            pj = self._read_json(str(self._get_project_dir() / "project.json"))
            if isinstance(pj, dict):
                return pj.get("current_step", 1)
        return 1

    def set_workflow_step(self, step: int) -> None:
        if self._get_project_dir():
            pj = self._read_json(str(self._get_project_dir() / "project.json"))
            if isinstance(pj, dict):
                pj["current_step"] = step
                self._write_json(str(self._get_project_dir() / "project.json"), pj)

    # 正文 L5 快捷接口
    def list_chapters(self) -> list[OutlineNode]:
        return self.get_nodes_by_level(OutlineLevel.CONTENT)

    def get_chapter(self, chapter_id: str) -> Optional[OutlineNode]:
        return self.get_node(chapter_id)

    def create_chapter(self, parent_id: str, title: str, content: str = "") -> OutlineNode:
        return self.create_node(parent_id, title, OutlineLevel.CONTENT, content)

    def update_chapter(self, chapter_id: str, **kwargs) -> OutlineNode:
        return self.update_node(chapter_id, **kwargs)

    def delete_chapter(self, chapter_id: str) -> None:
        self.delete_node(chapter_id)

    # ═══════════════════════════════════════════════
    # 通用自由分类设定管理 (v1.0)
    # ═══════════════════════════════════════════════

    @property
    def _settings_dir(self) -> Optional[Path]:
        if not self._get_project_dir():
            return None
        return self._get_project_dir() / "settings"

    def list_categories(self) -> list[str]:
        sd = self._settings_dir
        if not sd or not sd.exists():
            return []
        order_file = sd / "_order.json"
        order_data = self._read_json(str(order_file)) if order_file.exists() else []
        if isinstance(order_data, list) and order_data:
            ordered = [d for d in order_data if (sd / d).is_dir()]
            remaining = [d.name for d in sorted(sd.iterdir()) if d.is_dir() and d.name not in ordered and d.name != "_order"]
            return ordered + remaining
        return sorted([d.name for d in sd.iterdir() if d.is_dir()])

    def list_docs(self, category: str) -> list[str]:
        sd = self._settings_dir
        if not sd:
            return []
        cat_dir = sd / category
        if not cat_dir.exists():
            return []
        order_file = cat_dir / "_order.json"
        order_data = self._read_json(str(order_file)) if order_file.exists() else []
        if isinstance(order_data, list) and order_data:
            ordered = [d for d in order_data if (cat_dir / f"{d}.md").exists()]
            remaining = [f.stem for f in sorted(cat_dir.iterdir()) if f.suffix == ".md" and f.stem not in ordered and f.name != "_order"]
            return ordered + remaining
        return sorted([f.stem for f in cat_dir.iterdir() if f.suffix == ".md"])

    def get_setting(self, category: str, name: str) -> Optional[str]:
        sd = self._settings_dir
        if not sd:
            return None
        fp = sd / category / f"{name}.md"
        if not fp.exists():
            return None
        return self._read_file(str(fp))

    def save_setting(self, category: str, name: str, content: str) -> None:
        sd = self._settings_dir
        if not sd:
            return
        cat_dir = sd / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        self._write_file(str(cat_dir / f"{name}.md"), content)
        self._log(f"设定 {category}/{name} 已保存")
        self._publish("setting:updated", {"category": category, "name": name})

    def delete_setting(self, category: str, name: str) -> None:
        sd = self._settings_dir
        if not sd:
            return
        fp = sd / category / f"{name}.md"
        if fp.exists():
            fp.unlink()
            self._log(f"设定 {category}/{name} 已删除")

    def delete_category(self, category: str) -> None:
        sd = self._settings_dir
        if not sd:
            return
        cat_dir = sd / category
        if cat_dir.exists():
            shutil.rmtree(cat_dir)
            self._log(f"分类 {category} 已删除")

    def rename_category(self, old_name: str, new_name: str) -> None:
        sd = self._settings_dir
        if not sd:
            return
        old = sd / old_name
        new = sd / new_name
        if old.exists() and not new.exists():
            old.rename(new)

    def rename_setting(self, category: str, old_name: str, new_name: str) -> None:
        sd = self._settings_dir
        if not sd:
            return
        old = sd / category / f"{old_name}.md"
        new = sd / category / f"{new_name}.md"
        if old.exists() and not new.exists():
            old.rename(new)

    def reorder_categories(self, ordered_names: list[str]) -> None:
        sd = self._settings_dir
        if not sd:
            return
        self._write_json(str(sd / "_order.json"), ordered_names)

    def reorder_docs(self, category: str, ordered_names: list[str]) -> None:
        sd = self._settings_dir
        if not sd:
            return
        cat_dir = sd / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(str(cat_dir / "_order.json"), ordered_names)

    def search_content(self, keyword: str, category: str | None = None) -> list[dict]:
        results = []
        proj = self._get_project_dir()
        if not proj:
            return results

        # 搜索设定
        sd = proj / "settings"
        if sd.exists():
            for cat in sd.iterdir():
                if cat.is_dir() and (category is None or cat.name == category):
                    for f in cat.iterdir():
                        if f.suffix == ".md":
                            content = self._read_file(str(f))
                            if keyword in content:
                                results.append({
                                    "type": "setting",
                                    "path": f"settings/{cat.name}/{f.name}",
                                    "snippet": self._extract_snippet(content, keyword),
                                })

        # 搜索正文
        cd = proj / "content"
        if cd.exists() and category is None:
            for f in cd.iterdir():
                if f.suffix == ".md":
                    content = self._read_file(str(f))
                    if keyword in content:
                        results.append({
                            "type": "chapter",
                            "path": f"content/{f.name}",
                            "snippet": self._extract_snippet(content, keyword),
                        })
        return results

    def _extract_snippet(self, content: str, keyword: str) -> str:
        lines = content.split("\n")
        for line in lines:
            if keyword in line:
                return line.strip()[:200]
        return content[:200]

    def export_settings(self, category: str, names: list[str],
                        output_dir: str, merge: bool = False) -> str:
        if merge:
            output_path = Path(output_dir) / f"{category}_export.md"
            parts = [f"# {category}\n\n> 导出时间: {self._now()}\n"]
            for name in names:
                content = self.get_setting(category, name)
                if content:
                    parts.append(f"## {name}\n\n{content}\n\n---\n")
            self._write_file(str(output_path), "\n".join(parts))
            return str(output_path)
        else:
            out_dir = Path(output_dir) / category
            out_dir.mkdir(parents=True, exist_ok=True)
            for name in names:
                content = self.get_setting(category, name)
                if content:
                    self._write_file(str(out_dir / f"{name}.md"), content)
            return str(out_dir)

    # ═══════════════════════════════════════════════
    # 向后兼容别名 (v1.0)
    # ═══════════════════════════════════════════════
    def list_power_systems(self) -> list[str]:
        return self.list_docs("power_systems")

    def get_power_system(self, name: str) -> Optional[str]:
        return self.get_setting("power_systems", name)

    def save_power_system(self, name: str, content: str) -> None:
        self.save_setting("power_systems", name, content)

    def delete_power_system(self, name: str) -> None:
        self.delete_setting("power_systems", name)

    def list_factions(self) -> list[str]:
        return self.list_categories()

    def list_characters(self, faction: str) -> list[str]:
        return self.list_docs(faction)

    def get_character(self, faction: str, name: str) -> Optional[str]:
        return self.get_setting(faction, name)

    def save_character(self, faction: str, name: str, content: str) -> None:
        self.save_setting(faction, name, content)

    def delete_character(self, faction: str, name: str) -> None:
        self.delete_setting(faction, name)
