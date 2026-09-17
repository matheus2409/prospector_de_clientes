@echo off
setlocal
cd /d "%~dp0"

if not exist venv (
    echo [ERRO] Ambiente virtual nao encontrado. Rode setup.bat primeiro.
    pause
    exit /b 1
)

echo Iniciando servidor em http://127.0.0.1:8000
echo Abra esse endereco no navegador. Pressione Ctrl+C aqui para parar o servidor.
echo.

call venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
