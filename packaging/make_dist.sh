#!/usr/bin/env bash
# 一键三平台打包（在 macOS / Linux 上运行 Windows 需 wine + 32 位 Python）
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

if ! command -v pyinstaller >/dev/null 2>&1; then
  echo "[ERR] pyinstaller 未安装。请先执行： uv pip install '.[packaging]'" >&2
  exit 1
fi

mkdir -p dist

for spec in packaging/build_current.spec; do
  echo "==> 打包: $spec"
  pyinstaller "$spec" --clean --noconfirm
done

echo
echo "[OK] 打包完成："
ls -lh dist/ 2>/dev/null || true
