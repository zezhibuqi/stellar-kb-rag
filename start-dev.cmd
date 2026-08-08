@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   stellar-kb-rag 本地开发一键启动
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境 .venv，请先执行：
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist ".env" (
    echo [提示] 未找到 .env，请复制 .env.example 为 .env 并填写 API Key。
    pause
    exit /b 1
)

if not exist "frontend\node_modules" (
    echo [提示] 前端依赖未安装，请先执行：
    echo   cd frontend ^&^& npm install
    pause
    exit /b 1
)

echo [1/3] 初始化数据库（幂等）...
".venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, 'backend'); from models import init_db; init_db()"
if errorlevel 1 (
    echo [错误] 数据库初始化失败
    pause
    exit /b 1
)

netstat -ano | findstr ":5000" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo [提示] 5000 端口已被占用，跳过后端启动。
) else (
    echo [2/3] 启动后端：http://localhost:5000
    start "stellar-kb-backend" /D "%~dp0backend" cmd /k "%~dp0.venv\Scripts\python.exe -m flask --app app run --port 5000"
)

netstat -ano | findstr ":3000" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo [提示] 3000 端口已被占用，跳过前端启动。
) else (
    echo [3/3] 启动前端：http://localhost:3000
    start "stellar-kb-frontend" /D "%~dp0frontend" cmd /k "npm.cmd run dev"
)

echo.
echo 启动完成。前后端窗口保持运行，关闭对应窗口即停止服务。
echo 前端地址：http://localhost:3000
echo 后端地址：http://localhost:5000
endlocal
