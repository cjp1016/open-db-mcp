#!/usr/bin/env bash
# open-db-mcp 团队成员安装脚本（macOS / Linux）
# 用法：curl -fsSL https://your-host/install.sh | bash
# 自定义版本：OPEN_DB_MCP_VERSION=0.2.0 curl ... | bash
set -euo pipefail

VERSION="${OPEN_DB_MCP_VERSION:-0.1.0}"
REPO_BASE="${OPEN_DB_MCP_REPO:-https://your-host/releases}"
INSTALL_DIR="${OPEN_DB_MCP_HOME:-$HOME/.open-db-mcp/bin}"
CONFIG_DIR="$HOME/.open-db-mcp"

OS_RAW="$(uname -s)"
case "$OS_RAW" in
  Linux*)  OS_TAG="linux" ;;
  Darwin*) OS_TAG="macos" ;;
  *) echo "[ERR] 不支持的操作系统: $OS_RAW" >&2; exit 1 ;;
esac

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH_TAG="x64" ;;
  arm64|aarch64) ARCH_TAG="arm64" ;;
  *) echo "[ERR] 不支持的架构: $ARCH" >&2; exit 1 ;;
esac

ASSET="open-db-mcp-${VERSION}-${OS_TAG}-${ARCH_TAG}.tar.gz"
URL="${REPO_BASE}/${ASSET}"

echo "==> 准备安装目录: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"

echo "==> 下载: $URL"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if ! curl -fsSL "$URL" -o "$TMP/$ASSET"; then
  echo "[ERR] 下载失败，请检查 OPEN_DB_MCP_REPO / OPEN_DB_MCP_VERSION" >&2
  exit 1
fi

echo "==> 解压到: $INSTALL_DIR"
tar -xzf "$TMP/$ASSET" -C "$INSTALL_DIR"

EXE="$INSTALL_DIR/open-db-mcp"
if [ ! -x "$EXE" ]; then
  echo "[ERR] 找不到可执行文件: $EXE" >&2
  exit 1
fi
chmod +x "$EXE"

# 写 PATH
SHELL_RC="$HOME/.zshrc"
[ -f "$HOME/.bashrc" ] && [ ! -f "$SHELL_RC" ] && SHELL_RC="$HOME/.bashrc"
if [ -f "$SHELL_RC" ] && ! grep -q 'open-db-mcp/bin' "$SHELL_RC"; then
  echo "" >> "$SHELL_RC"
  echo "# open-db-mcp" >> "$SHELL_RC"
  echo "export PATH=\"$INSTALL_DIR:\$PATH\"" >> "$SHELL_RC"
  echo "==> 已追加 PATH 到 $SHELL_RC（请重新打开终端或 source $SHELL_RC）"
fi

echo "==> 引导初始化配置"
"$EXE" init --jdbc "${MCP_JDBC_PROPERTIES_PATH:-}" || true

cat <<EOF

================================================
 open-db-mcp ${VERSION} 安装完成！

 下一步：
   1. 编辑 $CONFIG_DIR/config.yaml 指定 jdbc.properties 路径
   2. 编辑 $CONFIG_DIR/whitelist.json 配置表/列白名单
   3. 在 MCP 客户端（如 Claude Desktop）配置：
        command: $EXE
        args:    ["run"]
        env:     { MCP_JDBC_PROPERTIES_PATH: "/abs/path/jdbc.properties" }
   4. 重启 MCP 客户端
================================================
EOF
