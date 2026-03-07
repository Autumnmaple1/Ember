@echo off
chcp 65001 > nul
set "CURRENT_DIR=%~dp0"

echo ==========================================
echo       Ember 项目一键启动 (全服务)
echo ==========================================

REM 1. 尝试激活 Conda 环境
call conda --version > nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] 未找到 Conda，请确保已安装并配置环境变量。
    pause
    exit /b %errorlevel%
)

REM 2. 启动容器 (Postgres/Neo4j等) - 如果你使用了 docker
if exist "docker-compose.yml" (
    echo [INFO] 正在后台启动 Docker 容器...
    docker-compose up -d
)

REM 3. 启动后端服务 (server.py)
echo [INFO] 正在启动 Backend Server (Port: 8000)...
start "Ember-Backend" cmd /k "chcp 65001 > nul && call conda activate Ember && cd /d %CURRENT_DIR% && python server.py"

REM 4. 等待后端启动
timeout /t 5 > nul

REM 5. 启动前端服务 (Vite/npm)
echo [INFO] 正在启动 Frontend Server (Vite)...
if exist "frontend\package.json" (
    start "Ember-Frontend" cmd /k "chcp 65001 > nul && cd /d %CURRENT_DIR%frontend && npm run dev"
) else (
    echo [WARNING] 未找到前端目录 frontend/，跳过前端启动。
)

echo.
echo ==========================================
echo [SUCCESS] 所有服务均在独立窗口中启动！
echo 后端: http://localhost:8000
echo 前端: 请关注控制台中的 URL (通常是 http://localhost:5173)
echo ==========================================
echo.
echo 如需停止服务，请直接关闭弹出的命令行窗口。
pause
