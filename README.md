# 有声书生成工具 (EPUB to MP3)

**版本：v1.0 | 语言：Python 3.8+ | 系统：Windows / macOS / Linux**

---

## 📋 目录

1. [项目概述](#项目概述)
2. [项目结构](#项目结构)
3. [安装指南](#安装指南)
4. [使用说明](#使用说明)
5. [功能详解](#功能详解)
6. [常见问题](#常见问题)
7. [开发者指南](#开发者指南)

---

## 📖 项目概述

**有声书生成工具** 是一个一体化解决方案，可以：

✅ **将 EPUB 电子书转换为 TXT 文本**
- 智能提取章节和正文
- 自动清理 HTML 标签和噪音
- 支持长章节自动拆分

✅ **将 TXT 文本转换为 MP3 有声书**
- 使用 Microsoft Edge TTS 进行语音合成
- 支持 12 种中文语音
- 可调节语速、音调、音量

✅ **灵活的音频组织**
- 单文件转换模式
- 多文件合并模式（按时长组织）
- 长文本自动分割

---

## 📁 项目结构

```
EPUB_to_MP3_Project/
├── models.py                  # 【模块1】配置类 + TTS + 文本工具
├── epub_processor.py          # 【模块2】EPUB 解析 + 转 TXT
├── audio_processor.py         # 【模块3】文本预处理 + 音频处理
├── main.py                    # 【模块4】GUI 应用 + 业务逻辑
├── app.py                     # 【模块5】程序入口
├── config.json                # 配置文件（自动生成）
└── README.md                  # 本文件
```

### 📊 文件功能说明

| 文件 | 行数 | 功能 | 依赖 |
|------|------|------|------|
| **models.py** | 230 | 配置管理、字数估算、TTS 语音、文本处理工具 | 无 |
| **epub_processor.py** | 350 | EPUB 解析、章节提取、HTML 清理、文本转换 | models.py |
| **audio_processor.py** | 280 | 文本预处理、TTS 合成、MP3 合并 | models.py |
| **main.py** | 550 | GUI 界面、文件列表、业务逻辑核心 | 前三个模块 |
| **app.py** | 5 | 程序入口、启动应用 | main.py |

### 🔗 模块依赖关系

```
app.py
  └── main.py
        ├── models.py
        ├── epub_processor.py  ──→ models.py
        └── audio_processor.py ──→ models.py
```

---

## 🛠️ 安装指南

### 前置要求

- **Python 3.8+** （推荐 3.10+）
- **pip** 包管理器
- **ffmpeg**（用于 MP3 合并）

### 1️⃣ 检查 Python 版本

```bash
python3 --version
# 输出示例: Python 3.12.7
```

### 2️⃣ 安装依赖包

```bash
pip install ebooklib beautifulsoup4 pydub edge-tts
```

验证安装：
```bash
pip list | grep -E "ebooklib|beautifulsoup4|pydub|edge-tts"
```

### 3️⃣ 安装 FFmpeg

**macOS:**
```bash
brew install ffmpeg
```

**Windows:**
下载安装：https://ffmpeg.org/download.html

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get install ffmpeg
```

验证安装：
```bash
ffmpeg -version
```

### 4️⃣ 项目文件检查

确保项目文件夹中有：
- ✅ models.py
- ✅ epub_processor.py
- ✅ audio_processor.py
- ✅ main.py
- ✅ app.py

---

## 🚀 使用说明

### 快速开始

#### 方式 1：运行 Python 脚本

```bash
cd /Users/lilina/GitHub Repositories/Epub2Mp3/
python3 app.py
```

#### 方式 2：在 IDE 中运行

在 PyCharm/VS Code 中打开 `app.py`，点击 **Run**

---

### 界面说明

#### 【语音设置】区域

| 选项 | 说明 | 范围 |
|------|------|------|
| **音色** | 选择语音角色（12 种中文语音） | 下拉菜单 |
| **语速** | 朗读速度倍数 | 0.5x ~ 2.0x |
| **音调** | 声音高低 | -50Hz ~ +50Hz |
| **音量** | 音频音量 | -100% ~ +100% |
| **试听** | 播放试听样本 | 点击按钮 |

#### 【输出设置】区域

| 选项 | 说明 | 备注 |
|------|------|------|
| **合并音频** | 启用/禁用多文件合并 | 关闭=单文件模式 |
| **目标时长** | 单个 MP3 音频长度 | 10~120 分钟 |
| **TXT目录** | 输入 TXT 文件夹 | 浏览选择 |

#### 【源文件】列表

| 列 | 说明 |
|----|------|
| ✓ | 选择复选框（点击切换） |
| TXT名称 | 文件名（双击打开预览） |
| 大小(KB) | 文件大小 |
| 字数 | 文本总字数 |
| 预估时长 | 按当前语速估算的朗读时长 |
| 状态 | 处理状态（待处理/处理中/已完成等） |
| 进度 | 处理进度条 |

#### 【按钮】说明

| 按钮 | 功能 |
|------|------|
| **【全选】** | 选中所有文件 |
| **【全不选】** | 取消所有选中 |
| **【反选】** | 反转选择状态 |
| **停止** | 中止当前任务 |
| **导入 EPUB→TXT** | 转换 EPUB 为 TXT |
| **打开音频目录** | 打开输出文件夹 |
| **开始转换 🚀** | 开始��成有声书 |

---

## 📚 功能详解

### 功能 1：EPUB 转 TXT

**场景：** 你有一本 EPUB 格式的电子书，想转换为纯文本

**步骤：**

1. 点击 **【导入 EPUB→TXT】** 按钮
2. 选择 EPUB 文件
3. 等待转换完成
4. 系统自动加载生成的 TXT 文件

**结果：**
- 在 EPUB 文件同目录生成 `xxx_txt` 文件夹
- 每一章节对应一个 TXT 文件
- 自动清理 HTML 标签、脚注、注释等噪音

**配置说明：**
```python
# 在 epub_processor.py 中可调整：
max_chars_per_file = 50000  # 单个 TXT 最大字数
# 如果章节超过此值，会自动拆分
```

---

### 功能 2：TXT 转 MP3（单文件模式）

**场景：** 将每个 TXT 文件独立转换为 MP3

**步骤：**

1. 选择 TXT 文件夹
2. **取消勾选** "合并音频为长段落"
3. 勾选要转换的 TXT 文件
4. 点击 **【开始转换 🚀】**
5. 等待生成完成

**输出结构：**
```
TXT目录/
├── 001-第一章.txt
├── 002-第二章.txt
└── Audio/
    ├── 001-第一章.mp3
    ├── 002-第二章.mp3
    └── ...
```

**长文本处理：**
- 如果单个 TXT 超过 60 分钟
- 自动分割为多个 40 分钟的 MP3
- 文件名：`001-第一章.mp3`、`001-第一章_p2.mp3`、`001-第一章_p3.mp3`

---

### 功能 3：TXT 转 MP3（合并模式）

**场景：** 将多个 TXT 文件合并为少数几个 MP3（按时长组织）

**步骤：**

1. 选择 TXT 文件夹
2. **勾选** "合并音频为长段落"
3. 设置目标时长（例：40 分钟）
4. 勾选要转换的 TXT 文件
5. 点击 **【开始转换 🚀】**
6. 等待生成完成

**示例：**

假设有 3 个 TXT 文件：
- 第一章：15 分钟
- 第二章：20 分钟
- 第三章：25 分钟
- 目标时长：40 分钟

**合并结果：**
```
Audio/
├── 001-第一章_到_第二章.mp3    # 35分钟（第一+第二）
└── 002-第三章.mp3              # 25分钟（第三）
```

---

### 功能 4：语音参数调整

**步骤：**

1. 在【语音设置】中调整参数
2. 点击 **【试听】** 预听效果
3. 满意后点击 **【开始转换】**

**推荐配置：**

| 场景 | 语速 | 音调 | 音量 |
|------|------|------|------|
| 标准阅读 | 1.0x | 0Hz | 0% |
| 快速浏览 | 1.5x | 0Hz | 0% |
| 舒缓阅读 | 0.8x | -10Hz | +10% |
| 女性语音 | 1.0x | +5Hz | 0% |
| 男性语音 | 1.0x | -5Hz | 0% |

---

### 功能 5：EPUB 导入与预览

**双击预览文件：**
- 在【源文件】列表中双击 TXT 文件名
- 系统会用默认文本编辑器打开文件

**打开音频目录：**
- 点击 **【打开音频目录 📁】**
- 直接打开生成的 MP3 文件所在文件夹

---

## ❓ 常见问题

### Q1：转换很慢，是网络问题吗？

**A：** 不是。Edge TTS 依赖网络，但速度主要取决于：
- 文本长度（字数越多越慢）
- 网络速度（稳定的网络更快）
- 语速设置（语速快的合成速度也快）

**优化建议：**
- 确保网络稳定（不要用 VPN）
- 使用默认语速（1.0x）
- 分割长文本为多个小文件

---

### Q2：为什么没有检测到 ffmpeg？

**A：** ffmpeg 未安装或未加入 PATH

**解决方案：**

macOS:
```bash
brew install ffmpeg
```

Windows:
1. 下载：https://ffmpeg.org/download.html
2. 解压到 `C:\ffmpeg`
3. 添加到 PATH（高级系统设置 → 环境变量）

验证：
```bash
ffmpeg -version
```

---

### Q3：TXT 文件编码问题？

**A：** 工具自动处理 UTF-8 编码

如果某些字符显示错误：
1. 用文本编辑器打开 TXT 文件
2. 另存为 **UTF-8 编码**
3. 重新加载文件列表

---

### Q4：能否只转换某些文件？

**A：** 可以。在【源文件】列表中：
- 点击文件名或第一列的 ✓ 符号来选择/取消
- 使用【全选】【全不选】【反选】按钮
- 只有勾选的文件才会被转换

---

### Q5：MP3 文件在哪里？

**A：** 在 TXT 文件夹内的 `Audio` 子文件夹中

例：
```
/Users/lilina/Downloads/my_books/
├── 第一章.txt
├── 第二章.txt
└── Audio/              ← MP3 文件在这里
    ├── 001-第一章.mp3
    └── 002-第二章.mp3
```

点击 **【打开音频目录 📁】** 可直接打开

---

### Q6：转换中途出错怎么办？

**A：** 查看【状态】列显示的错误信息

常见错误：

| 错误 | 原因 | 解决 |
|------|------|------|
| 网络连接失败 | 网络不稳定 | 检查网络，重试 |
| ffmpeg 未找到 | ffmpeg 未安装 | 安装 ffmpeg |
| 文件不存在 | TXT 文件被删除 | 刷新文件列表 |
| 磁盘空间不足 | 输出目录满 | 清理磁盘空间 |

---

### Q7：能否使用其他语音或语言？

**A：** 目前仅支持 12 种中文语音

如需其他语言，需修改代码：

在 `models.py` 中修改 `VOICE_MAPPING`：
```python
VOICE_MAPPING = {
    "你的标签": "Edge TTS 语音代码",
}
```

完整语音列表：https://learn.microsoft.com/zh-cn/azure/ai-services/speech-service/language-support?tabs=tts

---

## 👨‍💻 开发者指南

### 项目架构

```
应用入口 (app.py)
    ↓
主应用类 (main.py: AudiobookGenerator)
    ├── EPUB 处理 (epub_processor.py)
    │   ├── build_chapters_from_book()    # 构建章节
    │   ├── clean_text_from_html_bytes()  # 清理 HTML
    │   └── convert_epub_to_txt()         # EPUB→TXT
    │
    ├── 音频处理 (audio_processor.py)
    │   ├── preprocess_text()             # 文本预处理
    │   └── _process_audio_chunk()        # TTS 合成+合并
    │
    └── 工具类 (models.py)
        ├── ConfigManager                 # 配置管理
        ├── DurationEstimator             # 时长估算
        ├── EdgeTTSWrapper                # TTS 包装
        └── 文本处理函数                   # 工具集
```

### 关键类和方法

#### ConfigManager（配置管理）

```python
config_mgr = ConfigManager(CONFIG_FILE)

# 获取配置
value = config_mgr.get("key", default_value)

# 设置配置
config_mgr.set("key", value)

# 强制保存
config_mgr.flush()
```

#### DurationEstimator（时长估算）

```python
estimator = DurationEstimator(base_wpm=300)

# 计算字数
chars = estimator.count_chars("文本内容")

# 估算秒数
seconds = estimator.estimate_seconds(chars, wpm=300)
```

#### EdgeTTSWrapper（语音合成）

```python
tts = EdgeTTSWrapper()

# 获取可用语音
voices = tts.refresh_voices()

# 文本转语音
tts.text_to_speech(
    text="朗读文本",
    voice="zh-CN-XiaoxiaoNeural",
    speed=1.0,
    pitch=0,
    volume=0,
    output_file="output.mp3"
)
```

### 扩展功能的方式

#### 1. 添加新的语音

在 `models.py` 中修改 `VOICE_MAPPING`：
```python
VOICE_MAPPING = {
    "新语音": "zh-CN-NewVoiceNeural",
    # ...
}
```

#### 2. 修改文本处理规则

在 `models.py` 中编辑这些常量：
```python
BLOCK_TAGS = (...)           # HTML 块级标签
REMOVE_TAGS = (...)          # 要删除的标签
NOISE_KEYWORDS = (...)       # 噪音关键词
```

#### 3. 调整时长估算

在 `models.py` 中修改：
```python
BASE_WORDS_PER_MINUTE = 300  # 默认朗读速度
```

#### 4. 修改分段策略

在 `main.py` 中编辑 `generate_single_files()` 和 `generate_merged_files()` 方法

#### 5. 修改 UI 布局

在 `main.py` 中编辑 `create_ui()` 方法

### 常见修改需求

#### 需求：改变默认输出格式

**文件：** `audio_processor.py` 的 `_process_audio_chunk()` 函数

```python
# 原来：
opath = os.path.join(out_dir, f"{base_name}.mp3")

# 修改为：
opath = os.path.join(out_dir, f"{base_name}_voice_{voice_name}.mp3")
```

#### 需求：修改长文本分割阈值

**文件：** `main.py` 的 `generate_single_files()` 方法

```python
# 原来：
if file_duration > 60:  # 超过 60 分钟分割

# 修改为：
if file_duration > 120:  # 超过 120 分钟分割
```

#### 需求：修改 TTS 重试次数

**文件：** `main.py` 的 `tts_with_retry()` 方法

```python
# 原来：
def tts_with_retry(..., max_retries: int = 3):

# 修改为：
def tts_with_retry(..., max_retries: int = 5):
```

---

### 调试技巧

#### 启用详细日志

在 `main.py` 开头添加：
```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# 然后在代码中使用：
logger.debug("调试信息")
logger.info("普通信息")
logger.warning("警告信息")
```

#### 测试单个模块

```bash
# 测试 EPUB 转换
python3 -c "from epub_processor import convert_epub_to_txt; convert_epub_to_txt('test.epub')"

# 测试 TTS
python3 -c "from models import EdgeTTSWrapper; tts = EdgeTTSWrapper(); print(tts.voices)"
```

---

## 📝 版本历史

### v3.0 (2026-03-05)
- ✅ 完全重构为模块化架构
- ✅ 修复长文本分割问题
- ✅ 改进表格选择交互
- ✅ 优化文件命名规则
- ✅ 增强代码文档

### v2.0 (之前版本)
- 单文件架构
- 基础 EPUB→TXT 功能
- 基础 TTS 合成

---

## 📧 技术支持

如遇到问题：

1. **查看【常见问题】部分**
2. **检查【状态栏】的错误信息**
3. **查看【错误详情】（右键点击状态列）**

---

## 📄 许可证

MIT License

---

## 🎯 未来规划

- [ ] 支持英文和其他语言
- [ ] 添加更多语音角色
- [ ] 支持 MOBI、AZW 格式
- [ ] 增加音频效果调整
- [ ] Web 界面版本

---

**感谢使用有声书生成工具！** 🎉
