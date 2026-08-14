#!/usr/bin/env bash
#
# 本地构建 MyCodex 独立 .dmg
# ------------------------------------------------------------------
# 为什么这么做：
#   之前的打包用 PyInstaller --onefile，把 python+pyobjc 全压进单个
#   可执行，峰值内存超过本机 8GB -> OOM(exit 137)。
#   本脚本改用 --onedir（依赖散在目录），峰值内存低很多；并在隔离
#   venv 里用系统 framework python 安装 pyobjc，保证 macOS 上可用。
#
# 用法：
#   bash macos/build_dmg.sh
#
# 若本机仍 OOM，改用 GitHub Actions（见 .github/workflows/build-macos.yml），
# 那是经验上最稳的路径。
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

APP_NAME="MyCodex"
BUNDLE_ID="com.mycodex.app"
OUT_DMG="dist/${APP_NAME}.dmg"
VENV="$REPO_ROOT/.build_venv"

echo "[1/5] 创建隔离构建 venv（系统 framework python，保证 pyobjc 可装可用）"
if [ ! -x "$VENV/bin/python3" ]; then
  /usr/bin/python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "pywebview[mac]>=6.0" pyinstaller

PY="$VENV/bin/python3"

echo "[2/5] 清理旧构建"
rm -rf dist build "${APP_NAME}.spec"

echo "[3/5] PyInstaller --onedir 打包（省内存，避免单文件 OOM）"
"$PY" -m PyInstaller \
  --name "$APP_NAME" \
  --windowed \
  --onedir \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --icon macos/AppIcon.icns \
  --add-data "app:app" \
  --add-data "macos/AppIcon.icns:." \
  --hidden-import "webview.platforms.cocoa" \
  --hidden-import "webview.platforms" \
  --hidden-import "urllib.request" \
  --hidden-import "urllib.error" \
  --hidden-import "PIL" \
  macos/launcher.py

echo "[4/5] 清理 PyInstaller 临时目录"
rm -rf "build"

echo "[5/5] create-dmg 封盘"
if ! command -v npx >/dev/null 2>&1; then
  echo "需要 node/npx，请先安装 Node.js"
  exit 1
fi
npx --yes create-dmg "$OUT_DMG" "dist/${APP_NAME}.app" --overwrite
echo "完成: $OUT_DMG （双击若提示身份不明开发者，右键->打开 即可本地测试）"
