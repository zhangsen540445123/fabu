param([string]$StateDir, [ValidateRange(1024,65535)][int]$Port)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Python environment missing: $python" }
if ($StateDir) { $env:FABU_STATE_DIR = [System.IO.Path]::GetFullPath($StateDir) }
if ($Port) { $env:FABU_PORT = [string]$Port }
$env:PYTHONUTF8 = '1'
# No taskkill or Stop-Process by name: authenticated local control checks PID and creation time.
& $python -m fabu.launcher stop
exit $LASTEXITCODE
