# 小说创作助手 (Novel Writing Assistant)

面向网络小说作者的 AI 辅助创作桌面工具。

## 功能特性

- **AI 对话**：多轮对话 + 流式输出，支持 OpenAI 兼容 API
- **创作流程**：8 步流水线（灵感搭建 → 基础设定 → 设定细化 → 剧情大纲 → 卷章划分 → 单卷细化 → 分割内容 → 内容细纲）
- **5 级大纲**：大纲(L1) → 卷纲(L2) → 简纲(L3) → 章纲(L4) → 正文(L5)
- **设定管理**：力量体系 + 角色设定（按势力分文件，支持跨势力引用）
- **配置管理**：多 AI 源配置，API 密钥 AES-256 加密存储
- **日志查看**：实时日志，按模块/级别筛选

## 系统要求

- Windows 10 / Windows 11（64 位）
- Python 3.10+

## 快速启动

### 1. 环境安装

双击运行 `setup_env.bat`，或手动执行：

```cmd
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 启动应用

双击运行 `run_app.bat`，或手动执行：

```cmd
venv\Scripts\activate
python app.py
```

### 3. 配置 AI 源

启动后，点击左侧「🔧 配置」面板：
1. 添加 AI 源：填写名称、Base URL、API Key、模型名
2. 点击「测试连接」验证配置
3. 点击「保存」

## 项目结构

```
novel_assistant/
├── app.py                  # 应用入口
├── requirements.txt        # Python 依赖
├── setup_env.bat           # 一键环境安装
├── run_app.bat             # 一键启动
├── src/
│   ├── core/               # 基础设施层
│   │   ├── logger.py       # 日志系统
│   │   ├── event_bus.py    # 事件总线
│   │   └── config_manager.py # 配置+密钥管理
│   ├── services/           # 业务逻辑层
│   │   ├── ai_client.py    # AI 客户端
│   │   ├── session_manager.py # 对话会话管理
│   │   └── project_service.py # 项目+大纲+设定服务
│   └── ui/                 # UI 层
│       ├── main_window.py  # 主窗口
│       └── panels.py       # 功能面板
├── tests/                  # 单元测试
├── docs/                   # 设计文档
└── workspace/              # 运行时数据（自动创建）
```

## 数据存储

所有数据存储在 `workspace/` 目录下：
- `config/` — 应用配置 + 加密密钥库
- `projects/` — 小说项目（大纲、正文、设定）
- `sessions/` — 对话历史
- `logs/` — 运行日志

## 许可证

MIT License
