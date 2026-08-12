#!/usr/bin/env bash

set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python"
SETUP_SCRIPT="${PROJECT_DIR}/setup_env.sh"

# Finder 双击启动时不会继承终端里的 Homebrew PATH。
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"

pause_on_error() {
  local message="$1"

  echo
  echo "启动失败：${message}"
  echo "请把这个窗口截图发给 Codex。"
  echo
  read -r -p "按回车键关闭窗口..."
  exit 1
}

prepare_environment() {
  if [ ! -f "$SETUP_SCRIPT" ]; then
    pause_on_error "未找到环境安装脚本 setup_env.sh。"
  fi

  if ! bash "$SETUP_SCRIPT"; then
    pause_on_error "运行环境安装或修复失败。"
  fi
}

cd "$PROJECT_DIR" || pause_on_error "无法进入项目目录。"

if [ ! -x "$VENV_PYTHON" ]; then
  echo "首次启动：正在准备运行环境，请稍候..."
  prepare_environment
fi

if ! "$VENV_PYTHON" -c \
  "import tkinter, tkinterdnd2, ebooklib, bs4, lxml, pydub, edge_tts, main" \
  >/dev/null 2>&1; then
  echo "检测到运行环境不完整，正在自动修复..."
  prepare_environment
fi

if [ ! -x "$VENV_PYTHON" ]; then
  pause_on_error "虚拟环境中的 Python 不可用。"
fi

if ! "$VENV_PYTHON" -c \
  "import tkinter, tkinterdnd2, ebooklib, bs4, lxml, pydub, edge_tts, main" \
  >/dev/null 2>&1; then
  pause_on_error "依赖检查未通过。"
fi

if [ "${1:-}" = "--check" ]; then
  echo "启动器检查通过。"
  exit 0
fi

echo "正在启动 EPUB to MP3..."
"$VENV_PYTHON" "$PROJECT_DIR/app.py"
exit_code=$?

if [ "$exit_code" -ne 0 ]; then
  pause_on_error "程序异常退出（错误代码：${exit_code}）。"
fi

echo "EPUB to MP3 已关闭。"
