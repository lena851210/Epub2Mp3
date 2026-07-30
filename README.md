```md
# Epub2Mp3

本地 GUI 工具：**EPUB → TXT → MP3 有声书生成器**

- **当前版本**：v3.x（持续迭代中）
- **主要开发环境**：macOS + Python 3.12
- **GUI**：tkinter
- **语音引擎**：Microsoft Edge TTS
- **音频处理**：ffmpeg + pydub

---

## 📋 目录

1. [项目概述](#项目概述)
2. [核心设计理念](#核心设计理念)
3. [项目结构](#项目结构)
4. [安装指南](#安装指南)
5. [运行方式](#运行方式)
6. [使用说明](#使用说明)
7. [功能说明](#功能说明)
8. [常见问题](#常见问题)
9. [开发者说明](#开发者说明)
10. [版本记录](#版本记录)

---

## 📖 项目概述

**Epub2Mp3** 是一个面向本地使用的桌面 GUI 工具，目标是把电子书逐步转换成可收听的有声书。

当前支持的主要流程是：

### 1. EPUB → TXT
- 读取 EPUB 文件
- 提取章节正文
- 清理 HTML 标签、脚注、噪音内容
- 尽量保留章节/小节结构
- 输出为一组 TXT 文件

### 2. TXT → MP3
- 使用 Edge TTS 生成语音
- 支持多种中文音色
- 支持语速 / 音调 / 音量调节
- 支持单文件模式和合并模式
- 支持按目标时长拆分或合并音频

---

## 🎯 核心设计理念

当前项目采用的是：

# **EPUB → TXT：结构优先**
# **TXT → MP3：时长优先**

这意味着：

### EPUB 转 TXT 阶段
重点是尽量保留书籍原本的章节结构，而不是过早按时长切碎文本。

### TXT 转 MP3 阶段
再根据目标时长决定：
- 单个 TXT 是否要拆分成多个 MP3
- 多个 TXT 是否要合并成一个较长 MP3

这种设计的好处是：
- 文本结构更清楚
- 后续处理更灵活
- 更接近真实“先整理文本、再组织音频”的工作流程

---

## 📁 项目结构

当前项目结构如下：

```text
Epub2Mp3/
│
├── models.py                  # 配置管理、字数估算、TTS、文本处理工具
├── epub_processor.py          # EPUB 解析与 TXT 导出
├── audio_processor.py         # 音频处理底层函数（MP3 生成、分段命名等）
│
├── generation_manager.py      # 音频生成流程控制
├── file_manager.py            # TXT 文件列表与目录状态管理
├── main.py                    # 主应用类与 GUI 组装
├── app.py                     # 程序入口
│
├── config.example.json        # 配置示例文件
├── config.json                # 实际配置文件（运行后自动生成/更新）
│
├── requirements.txt           # 依赖列表
├── setup_env.sh               # 环境初始化脚本（如有使用）
├── README.md                  # 当前说明文档
└── 项目结构说明.txt            # 项目结构与模块职责说明
```

---

## 🧩 模块职责说明

### `models.py`
负责：
- `ConfigManager`：配置读写
- `DurationEstimator`：字数与时长估算
- `EdgeTTSWrapper`：TTS 包装
- 文本清理与 HTML 处理辅助函数

---

### `epub_processor.py`
负责：
- 读取 EPUB
- 解析章节结构
- 提取正文
- 清理重复标题、噪音页、目录碎片
- 导出 TXT 文件

---

### `audio_processor.py`
负责：
- 具体音频片段处理
- TTS 合成后的 MP3 输出
- 文件命名与底层处理逻辑

---

### `generation_manager.py`
负责：
- 开始/停止转换
- 单文件模式 / 合并模式
- 长文本按目标时长切分
- TTS 重试逻辑
- 整体 MP3 生成流程

---

### `file_manager.py`
负责：
- TXT 文件列表加载
- 全选 / 全不选 / 反选
- 目录变化轮询监测
- 外部删除 TXT 目录 / TXT 文件后的自动刷新
- “开始转换”按钮状态联动

---

### `main.py`
负责：
- 主窗口初始化
- `create_ui()` 界面布局
- 试听音频
- 导入 EPUB
- 打开音频目录
- 保存配置
- 关闭程序

---

### `app.py`
负责：
- 启动 `AudiobookGenerator`

---

## 🛠️ 安装指南

## 环境要求

当前推荐环境：

- **macOS**
- **Python 3.12**
- **tkinter 可用**
- **ffmpeg 已安装**

项目当前开发与测试主要围绕上述环境进行。

---

### 1. 检查 Python 版本

```bash
python3 --version
```

推荐输出类似：

```bash
Python 3.12.x
```

---

### 2. 创建虚拟环境（推荐）

```bash
python3 -m venv .venv
source .venv/bin/activate
```

---

### 3. 安装依赖

当前项目至少需要这些依赖：

```bash
pip install ebooklib beautifulsoup4 lxml pydub edge-tts
```

如果你使用 `requirements.txt`，也可以：

```bash
pip install -r requirements.txt
```

---

### 4. 安装 ffmpeg

#### macOS
```bash
brew install ffmpeg
```

#### Windows
下载并安装：
https://ffmpeg.org/download.html

#### Linux（Debian / Ubuntu）
```bash
sudo apt-get install ffmpeg
```

验证：

```bash
ffmpeg -version
```

---

### 5. 检查 tkinter

在 macOS 下可用性可以用下面命令简单测试：

```bash
python3 -c "import tkinter; print('tkinter ok')"
```

---

## ▶️ 运行方式

在项目目录中执行：

```bash
python app.py
```

如果你使用虚拟环境：

```bash
source .venv/bin/activate
python app.py
```

---

## 🖥️ 使用说明

当前 GUI 流程为：

### Step 1：文本准备
- 导入 EPUB → TXT
- 查看 TXT 列表
- 双击 TXT 名称可预览
- 支持全选 / 全不选 / 反选

### Step 2：语音设置
- 选择音色
- 调整语速、音调、音量
- 支持试听

### Step 3：输出设置
- 选择是否合并音频
- 设置目标时长
- 选择 TXT 目录

### 底部操作区
- 打开音频目录
- 停止
- 开始转换

---

## 📚 功能说明

## 功能 1：导入 EPUB 并生成 TXT

使用方式：

1. 点击 **“导入 EPUB→TXT”**
2. 选择 EPUB 文件
3. 程序自动解析并导出 TXT
4. 自动加载生成后的 TXT 列表

### 当前行为
- 默认按书本结构导出 TXT
- 不再默认按目标时长切 TXT
- 优先保留章节结构

---

## 功能 2：TXT 列表管理

支持：
- 全选
- 全不选
- 反选
- 双击预览文本
- 自然排序显示文件名

例如支持这类命名顺序：
- `001 标题.txt`
- `001-1 标题.txt`
- `002 标题.txt`

---

## 功能 3：语音试听与参数调整

支持设置：

| 选项 | 说明 |
|------|------|
| 音色 | 当前可用的 Edge 中文音色 |
| 语速 | 0.5x ~ 2.0x |
| 音调 | -50Hz ~ +50Hz |
| 音量 | -100% ~ +100% |

### 当前特性
- 只显示当前真正可用的音色
- 不可用音色不会出现在下拉列表中
- 可先试听，再开始转换

---

## 功能 4：单文件模式

如果不勾选“合并音频为长段落”，则每个 TXT 单独处理。

### 行为
- 如果单个 TXT 较短，直接生成一个 MP3
- 如果单个 TXT 过长，则按目标时长切分成多个音频片段

### 命名规则
- 不分段：`001 章节名.mp3`
- 分段：`001-1 章节名.mp3`、`001-2 章节名.mp3`

---

## 功能 5：合并模式

如果勾选“合并音频为长段落”，程序会尝试：

- 按顺序读取勾选的 TXT
- 按目标时长把多个 TXT 合并为较长 MP3
- 如果某一个 TXT 本身就很长，也会先按时长拆分

---

## 功能 6：目录状态自动检测

当前程序支持对 TXT 目录做轮询检测。

例如：
- 如果用户在 Finder / 文件管理器中删除了当前 TXT 目录
- 或删除了目录中的 TXT 文件

程序会在短时间内自动更新界面状态，包括：
- 清空文件列表
- 更新提示信息
- 自动禁用“开始转换”按钮

---

## 功能 7：打开音频目录

点击 **“打开音频目录”** 后，会打开当前 TXT 目录下的：

```text
Audio/
```

用于查看和试听输出的 MP3 文件。

---

## ❓ 常见问题

## Q1：程序启动时提示音色列表获取失败（503）怎么办？

这通常是 Edge TTS 远程服务临时不可用。

### 现象
终端可能显示类似：

```text
获取真实可用声音列表失败: 503 Service Unavailable
```

### 影响
- 一般不会导致程序完全无法启动
- 程序通常会回退到本地预设音色标签

### 建议
- 稍后重试
- 检查网络连接
- 不使用不稳定代理

---

## Q2：为什么“开始转换”按钮是灰的？

通常是以下原因之一：

- 当前 TXT 目录无效
- 当前目录不存在
- 当前目录中没有 TXT 文件
- TXT 文件已被外部删除

这是正常保护行为，避免用户在无有效文本时启动转换。

---

## Q3：为什么导入 EPUB 后，TXT 目录会自动变化？

因为程序会把 EPUB 转换结果输出到：

```text
原EPUB文件名_txt/
```

然后自动把这个目录设置为当前 TXT 目录，并加载列表。

---

## Q4：音频输出在哪里？

默认在当前 TXT 目录下的：

```text
Audio/
```

例如：

```text
思考快与慢_txt/
├── 001 第一章.txt
├── 002 第二章.txt
└── Audio/
    ├── 001 第一章.mp3
    ├── 002 第二章.mp3
```

---

## Q5：ffmpeg 没安装会怎样？

如果没有 ffmpeg：
- 某些 MP3 处理可能失败
- 合并、导出等操作可能受影响

建议提前安装并确保命令行可用。

---

## Q6：为什么有时试听失败？

试听依赖：
- Edge TTS 网络请求
- 系统播放命令
- 临时 MP3 文件生成成功

如果失败，优先检查：
- 网络是否正常
- `afplay`（macOS）是否可用
- Edge TTS 服务是否临时异常

---

## 👨‍💻 开发者说明

## 当前架构关系

```text
app.py
  └── main.py
        ├── generation_manager.py
        ├── file_manager.py
        ├── epub_processor.py
        ├── models.py
        └── audio_processor.py
```

---

## 当前推荐修改入口

### 如果你要改 EPUB→TXT 解析逻辑
改：

```text
epub_processor.py
```

---

### 如果你要改 TXT 列表、目录刷新、按钮状态
改：

```text
file_manager.py
```

---

### 如果你要改单文件/合并模式、TTS 重试、转换流程
改：

```text
generation_manager.py
```

---

### 如果你要改界面布局和按钮位置
改：

```text
main.py
```

主要关注：

```python
create_ui()
```

---

### 如果你要改配置、文本清理、TTS 封装
改：

```text
models.py
```

---

## 当前协作建议

如果后续继续和 AI 协作，建议按模块贴代码，而不是总是贴整个项目：

- UI 问题 → 提供 `main.py`
- 文件列表 / 目录状态问题 → 提供 `file_manager.py`
- 转换逻辑问题 → 提供 `generation_manager.py`
- EPUB 解析问题 → 提供 `epub_processor.py`
- 配置 / 音色 / 文本工具问题 → 提供 `models.py`

这样准确率会高很多，也更不容易产生“局部替换污染”。

---

## 📝 版本记录

### v3.x
- EPUB→TXT 改为结构优先
- TXT→MP3 维持时长优先
- 修复章节丢失问题
- 优化标题重复清理
- 只显示真正可用的音色
- GUI 布局调整为 Step 1 / Step 2 / Step 3
- 主流程按钮增强
- 增加目录自动监测
- `main.py` 开始拆分为多个模块
- 新增：
  - `generation_manager.py`
  - `file_manager.py`

### 更早版本
- 基础 EPUB→TXT
- 基础 TXT→MP3
- 初版 GUI

---

## 🚧 未来计划

- [ ] 继续拆分 `main.py`（如有必要，可拆出 `ui_manager.py`）
- [ ] 继续优化 EPUB TOC 叶子章节识别
- [ ] 进一步减少图题/��明文字误判为标题
- [ ] 优化更多音频命名细节
- [ ] 继续提升 macOS 下主按钮视觉体验
- [ ] 补充更完整的 GitHub 文档与截图

---

## 📄 许可证

MIT License

---

如果这个项目对你有帮助，欢迎 Star ⭐
```

---