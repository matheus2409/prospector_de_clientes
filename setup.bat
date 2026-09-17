@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Prospector Maps - Setup
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH. Instale o Python 3.10+ em https://python.org
    echo        e marque "Add Python to PATH" durante a instalacao.
    pause
    exit /b 1
)

if not exist venv (
    echo Criando ambiente virtual em .\venv ...
    python -m venv venv
)

echo Instalando dependencias Python...
call venv\Scripts\python.exe -m pip install --upgrade pip -q
call venv\Scripts\python.exe -m pip install -r backend\requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias. Veja a mensagem acima.
    pause
    exit /b 1
)

echo.
echo Instalando o navegador Chromium do Playwright (pode demorar um pouco)...
call venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 (
    echo [ERRO] Falha ao instalar o Chromium do Playwright.
    pause
    exit /b 1
)

if not exist .env (
    copy .env.example .env >nul
    echo Criado o arquivo .env a partir do .env.example.
)

echo.
echo ============================================
echo   Setup concluido!
echo   Edite o arquivo .env se quiser configurar
echo   webhook do n8n, Google Sheets ou API key.
echo   Depois, rode run.bat para iniciar o servidor.
echo ============================================
pause
