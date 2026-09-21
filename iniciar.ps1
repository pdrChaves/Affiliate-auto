# Sobe o painel do Promo Radar de qualquer lugar, sem depender da pasta em que
# o terminal abriu. Uso:  .\iniciar.ps1
# (se o PowerShell bloquear:  Set-ExecutionPolicy -Scope Process RemoteSigned)

$ErrorActionPreference = "Stop"
$raiz = Join-Path $PSScriptRoot "promo-radar"      # a pasta do projeto, ao lado deste script

if (-not (Test-Path $raiz)) {
    Write-Host "Não achei a pasta promo-radar ao lado deste script." -ForegroundColor Red
    Write-Host "Coloque o iniciar.ps1 na pasta que CONTÉM promo-radar." -ForegroundColor Yellow
    exit 1
}

Set-Location $raiz

$python = Join-Path $raiz ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Ambiente virtual não encontrado. Criando..." -ForegroundColor Yellow
    py -3 -m venv .venv
    & $python -m pip install --upgrade pip
    & $python -m pip install -r requirements.lock
}

if (-not (Test-Path (Join-Path $raiz ".env"))) {
    Copy-Item .env.example .env
    Write-Host "Criei o .env a partir do exemplo. Defina PANEL_PASSWORD (mínimo 8 caracteres) antes de entrar." -ForegroundColor Yellow
    notepad .env
}

Write-Host ""
Write-Host "Painel subindo em http://127.0.0.1:8000  (Ctrl+C para parar)" -ForegroundColor Green
Write-Host ""
& $python -m app.cli serve
