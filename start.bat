@echo off
chcp 65001 > nul
REM 激活 Conda 环境并运行 Ember 项目

call conda activate Ember
if %errorlevel% neq 0 (
    echo [ERROR] 无法激活 Conda 环境 "Ember"，请检查环境是否存在。
    pause
    exit /b %errorlevel%
)

echo [INFO] 正在启动 Ember...
python main.py

if %errorlevel% neq 0 (
    echo [ERROR] Ember 运行出错。
    pause
)

pause