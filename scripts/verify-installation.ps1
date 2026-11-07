param([string]$StateDir, [string]$DataDir, [string]$ToolsDir, [string]$NodePath, [ValidateRange(1024,65535)][int]$Port)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Python environment missing: $python" }
if ($StateDir) { $env:FABU_STATE_DIR = [System.IO.Path]::GetFullPath($StateDir) }
if ($DataDir) { $env:FABU_DATA_DIR = [System.IO.Path]::GetFullPath($DataDir) }
if ($ToolsDir) { $env:FABU_TOOLS_DIR = [System.IO.Path]::GetFullPath($ToolsDir) }
if ($NodePath) { $env:FABU_NODE_PATH = [System.IO.Path]::GetFullPath($NodePath) }
if ($Port) { $env:FABU_PORT = [string]$Port }
$env:PYTHONUTF8 = '1'
# Isolated imports use this venv, not the caller's directory or stale PYTHONPATH.
# Read-only diagnostics: no downloads, upgrades, credential queries, or publication.
& $python -I -X utf8 -m fabu.launcher check --project-root $root
exit $LASTEXITCODE
