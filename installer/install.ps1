# open-db-mcp 团队成员安装脚本（Windows PowerShell）
# 用法：iwr https://your-host/install.ps1 -UseBasicParsing | iex
# 自定义版本：$env:OPEN_DB_MCP_VERSION = "0.2.0"; iwr ... | iex

$ErrorActionPreference = "Stop"

$VERSION = $env:OPEN_DB_MCP_VERSION
if (-not $VERSION) { $VERSION = "0.1.0" }

$REPO_BASE = $env:OPEN_DB_MCP_REPO
if (-not $REPO_BASE) { $REPO_BASE = "https://your-host/releases" }

$INSTALL_DIR = $env:OPEN_DB_MCP_HOME
if (-not $INSTALL_DIR) { $INSTALL_DIR = "$env:USERPROFILE\.open-db-mcp\bin" }

$CONFIG_DIR = "$env:USERPROFILE\.open-db-mcp"

# 架构判断
$ARCH = $env:PROCESSOR_ARCHITECTURE
if ($ARCH -eq "AMD64") { $ARCH_TAG = "x64" } elseif ($ARCH -eq "ARM64") { $ARCH_TAG = "arm64" } else { Write-Error "不支持的架构: $ARCH"; exit 1 }

$ASSET = "open-db-mcp-$VERSION-windows-$ARCH_TAG.zip"
$URL = "$REPO_BASE/$ASSET"

Write-Host "==> 准备安装目录: $INSTALL_DIR"
New-Item -ItemType Directory -Force -Path $INSTALL_DIR, $CONFIG_DIR | Out-Null

Write-Host "==> 下载: $URL"
$TMP = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ([System.Guid]::NewGuid().ToString()))
$ZIP = Join-Path $TMP $ASSET
try {
    Invoke-WebRequest -Uri $URL -OutFile $ZIP -UseBasicParsing
} catch {
    Write-Error "下载失败：$($_.Exception.Message)"
    exit 1
}

Write-Host "==> 解压到: $INSTALL_DIR"
Expand-Archive -Path $ZIP -DestinationPath $INSTALL_DIR -Force

$EXE = Join-Path $INSTALL_DIR "open-db-mcp.exe"
if (-not (Test-Path $EXE)) {
    Write-Error "找不到可执行文件: $EXE"
    exit 1
}

# 追加 PATH
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*open-db-mcp*") {
    [Environment]::SetEnvironmentVariable("Path", "$INSTALL_DIR;$currentPath", "User")
    Write-Host "==> 已追加 PATH（重新打开 PowerShell 生效）"
}

# 引导
& $EXE init

Write-Host @"

================================================
 open-db-mcp $VERSION 安装完成！

 下一步：
   1. 编辑 $CONFIG_DIR\config.yaml 指定 jdbc.properties 路径
   2. 编辑 $CONFIG_DIR\whitelist.json 配置表/列白名单
   3. 在 MCP 客户端配置：
        command: $EXE
        args:    ["run"]
        env:     { MCP_JDBC_PROPERTIES_PATH: "C:\\path\\to\\jdbc.properties" }
   4. 重启 MCP 客户端
================================================
"@
