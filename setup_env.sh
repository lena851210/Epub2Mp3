#!/usr/bin/env bash

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "=== EPUB2MP3 环境初始化脚本（macOS） ==="

# 1. 选择 Python 解释器
#
# 说明：
# - Python 3.13+ 移除了标准库 audioop，而 pydub 依赖它，容易报错；
# - Homebrew 的 Python 有时不包含 _tkinter（GUI 会启动失败）。
# 因此优先选择已安装的 python3.12（兼容性最好），其次才用 python3。
if [ -x "/opt/homebrew/bin/python3.12" ]; then
  PYTHON_BIN="/opt/homebrew/bin/python3.12"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "未找到 Python，请先安装 Python 3。"
  echo "推荐：从 https://www.python.org/downloads/macos/ 安装官方 macOS 安装包（自带 Tcl/Tk）。"
  exit 1
fi

echo "使用 Python 解释器: $PYTHON_BIN"

# 2. 创建虚拟环境
VENV_DIR="${PROJECT_DIR}/.venv"
if [ ! -d "$VENV_DIR" ]; then
  echo "正在创建虚拟环境到 .venv ..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
else
  echo "检测到已存在虚拟环境 .venv，跳过创建。"
fi

# 3. 激活虚拟环境
if [ -f "${VENV_DIR}/bin/activate" ]; then
  # shellcheck disable=SC1090
  source "${VENV_DIR}/bin/activate"
  echo "已激活虚拟环境 .venv"
else
  echo "未找到虚拟环境激活脚本：${VENV_DIR}/bin/activate"
  exit 1
fi

# 4. 升级 pip
echo "正在升级 pip ..."
pip install --upgrade pip

# 5. 安装 Python 依赖
echo "正在安装项目依赖（ebooklib, beautifulsoup4, lxml, pydub, edge-tts）..."
pip install "ebooklib" "beautifulsoup4" "lxml" "pydub" "edge-tts"

echo "依赖安装完成。"

# 6. 提示安装 FFmpeg（用于音频合并）
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo
  echo "⚠️ 未检测到 ffmpeg。这不会影响基础运行，但合并/导出 MP3 可能失败。"
  echo "   在 macOS 上可以使用 Homebrew 安装："
  echo "     brew install ffmpeg"
fi

# 7. 检查 tkinter 是否可用，并给出修复建议
echo
echo "正在检测 tkinter 支持情况 ..."

if python - <<'PY'
try:
    import tkinter  # noqa: F401
except Exception as e:
    raise SystemExit(f"TKINTER_ERROR: {e}")
PY
then
  echo "tkinter 导入正常。"
else
  echo "⚠️ 检测到 tkinter 导入失败。"
  echo
  echo "可能原因："
  echo "  - 当前 Python 不是带 Tk 支持的官方 macOS 安装包；"
  echo "  - 或者使用 Homebrew Python，但未安装对应的 Tk 组件。"
  echo
  echo "建议修复步骤（任选其一）："
  echo "  1）推荐方式：从 Python 官网安装带 Tk 的 macOS 版本，然后重新运行本脚本："
  echo "     - 打开 https://www.python.org/downloads/macos/"
  echo "     - 安装最新的 Python 3.x（官方 .pkg 安装包自带 Tcl/Tk）；"
  echo "     - 重新打开终端，执行："
  echo "         cd \"$PROJECT_DIR\""
  echo "         ./setup_env.sh"
  echo
  echo "  2）如果你使用 Homebrew Python，可尝试安装 Tk 支持（命令可能因版本不同而略有变化）："
  echo "     - brew install python-tk@3.12"
  echo "     - 或参考 brew info python / python-tk 输出中的说明。"
  echo
  echo "修复完成后，请重新运行 ./setup_env.sh。"
fi

echo
echo "=== 环境准备完成 ==="
echo "接下来可以使用："
echo "  cd \"$PROJECT_DIR\""
echo "  source .venv/bin/activate"
echo "  python app.py"
echo
echo "如果在运行 app.py 时仍看到 tkinter 报错，请把完整错误信息发给我，我帮你继续排查。"

