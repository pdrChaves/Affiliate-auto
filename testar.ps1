# Roda a bateria de testes do Promo Radar de qualquer lugar.  Uso:  .\testar.ps1
$ErrorActionPreference = "Stop"
$raiz = Join-Path $PSScriptRoot "promo-radar"
Set-Location $raiz
$python = Join-Path $raiz ".venv\Scripts\python.exe"
& $python -m pytest -q
