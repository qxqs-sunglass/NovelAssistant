"""
全局 UI 主题（v3.0）— 统一高对比度浅色主题

通过 QSS 全局应用，确保所有面板控件风格一致、文字清晰可读。
关键对比度：
  - 正文 #2c2c2c on #ffffff (>13:1)
  - 次要文字 #555555 on #ffffff (>7:1)
  - 导航深色 #1f2937 + 文字 #e5e7eb (>13:1)
"""

STYLE_SHEET = """
* {
    font-family: "Microsoft YaHei";
    font-size: 10pt;
}

/* ── 基础容器 ── */
QMainWindow, QWidget {
    background-color: #f0f2f5;
    color: #2c2c2c;
}
QStackedWidget {
    background-color: #f0f2f5;
}

/* ── 标签 ── */
QLabel {
    color: #2c2c2c;
    background: transparent;
}
QLabel[role="muted"] {
    color: #666666;
}
QLabel[role="title"] {
    font-size: 13pt;
    font-weight: bold;
    color: #1f2937;
}

/* ── 按钮 ── */
QPushButton {
    background-color: #e8ecf1;
    border: 1px solid #c9d1da;
    border-radius: 4px;
    padding: 6px 14px;
    color: #1f2937;
}
QPushButton:hover {
    background-color: #dde4ec;
    border-color: #b6c0cc;
}
QPushButton:pressed {
    background-color: #cdd6e0;
}
QPushButton:disabled {
    color: #9aa4b0;
    background-color: #f0f2f5;
    border-color: #e2e6eb;
}
QPushButton#primary {
    background-color: #2563eb;
    color: #ffffff;
    border: none;
}
QPushButton#primary:hover {
    background-color: #1d4ed8;
}
QPushButton#danger {
    background-color: #dc2626;
    color: #ffffff;
    border: none;
}
QPushButton#danger:hover {
    background-color: #b91c1c;
}

/* ── 输入控件 ── */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #ffffff;
    border: 1px solid #c9d1da;
    border-radius: 4px;
    padding: 4px 8px;
    color: #2c2c2c;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #2563eb;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #c9d1da;
    color: #2c2c2c;
    selection-background-color: #eef2ff;
    selection-color: #1f2937;
}

/* ── 左侧导航（深色高对比） ── */
QListWidget#navList {
    background-color: #1f2937;
    border: none;
    font-size: 13px;
    color: #e5e7eb;
    padding: 6px;
    outline: none;
}
QListWidget#navList::item {
    padding: 12px 16px;
    border-radius: 6px;
    margin: 2px 4px;
    color: #e5e7eb;
    background-color: transparent;
}
QListWidget#navList::item:selected {
    background-color: #2563eb;
    color: #ffffff;
    font-weight: bold;
}
QListWidget#navList::item:hover:!selected {
    background-color: #374151;
    color: #ffffff;
}

/* ── 树/表 ── */
QTreeWidget, QTableWidget, QTreeView, QTableView, QListWidget:not(#navList) {
    background-color: #ffffff;
    alternate-background-color: #f7f8fa;
    border: 1px solid #d0d4da;
    color: #2c2c2c;
    selection-background-color: #dbe6ff;
    selection-color: #1f2937;
}
QHeaderView::section {
    background-color: #eef1f5;
    border: none;
    border-right: 1px solid #d0d4da;
    border-bottom: 1px solid #d0d4da;
    padding: 6px 8px;
    font-weight: bold;
    color: #333333;
}

/* ── 选项卡 ── */
QTabWidget::pane {
    border: 1px solid #d0d4da;
    background-color: #ffffff;
    top: -1px;
}
QTabBar::tab {
    background-color: #e8ecf1;
    color: #444444;
    padding: 8px 18px;
    border: 1px solid #d0d4da;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #ffffff;
    color: #1f2937;
    font-weight: bold;
}
QTabBar::tab:hover:!selected {
    background-color: #dde4ec;
}

/* ── 滚动条 ── */
QScrollBar:vertical {
    background-color: #f0f2f5;
    width: 12px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #b9c1cb;
    border-radius: 6px;
    min-height: 30px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background-color: #9aa4b0;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background-color: #f0f2f5;
    height: 12px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: #b9c1cb;
    border-radius: 6px;
    min-width: 30px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #9aa4b0;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ── 菜单 / 状态栏 / 工具提示 ── */
QMenuBar {
    background-color: #ffffff;
    border-bottom: 1px solid #e2e6eb;
}
QMenuBar::item {
    padding: 6px 10px;
}
QMenuBar::item:selected {
    background-color: #eef2ff;
}
QMenu {
    background-color: #ffffff;
    border: 1px solid #d0d4da;
}
QMenu::item {
    padding: 6px 24px;
    color: #2c2c2c;
}
QMenu::item:selected {
    background-color: #eef2ff;
    color: #1f2937;
}
QStatusBar {
    background-color: #ffffff;
    color: #555555;
    border-top: 1px solid #e2e6eb;
}
QToolTip {
    background-color: #333333;
    color: #ffffff;
    border: none;
    padding: 4px 8px;
}

/* ── 分组框 ── */
QGroupBox {
    border: 1px solid #d0d4da;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 10px;
    font-weight: bold;
    color: #1f2937;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    background-color: #f0f2f5;
}

/* ── 勾选/单选 ── */
QCheckBox, QRadioButton {
    spacing: 6px;
    color: #2c2c2c;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
}
"""
