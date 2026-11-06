param(
    [string]$StateDir,
    [string]$DataDir,
    [string]$ToolsDir,
    [string]$NodePath,
    [ValidateRange(1024,65535)][int]$Port,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python environment missing: $python. Follow README setup; nothing was installed automatically."
}
if ($StateDir) { $env:FABU_STATE_DIR = [System.IO.Path]::GetFullPath($StateDir) }
if ($DataDir) { $env:FABU_DATA_DIR = [System.IO.Path]::GetFullPath($DataDir) }
if ($ToolsDir) { $env:FABU_TOOLS_DIR = [System.IO.Path]::GetFullPath($ToolsDir) }
if ($NodePath) { $env:FABU_NODE_PATH = [System.IO.Path]::GetFullPath($NodePath) }
if ($Port) { $env:FABU_PORT = [string]$Port }
$env:PYTHONUTF8 = '1'
# Python launches an owned CREATE_NO_WINDOW runtime. No visible helper window or shell command.
if ($NoBrowser) { & $python -m fabu.launcher start --no-browser }
else { & $python -m fabu.launcher start }
exit $LASTEXITCODE
