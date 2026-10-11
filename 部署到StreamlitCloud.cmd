@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   SupplySync → Streamlit Community Cloud
echo ============================================
echo.
echo [1/3] 检查 git...
where git >nul 2>&1
if errorlevel 1 (
    echo [!] 未检测到 git，请先安装: https://git-scm.com/download/win
    echo 或用 winget install Git.Git
    pause
    exit /b 1
)

echo [2/3] 初始化仓库...
if not exist ".git" (
    git init -b main
    git config user.name "SupplySync Deploy"
    git config user.email "deploy@example.com"
    echo 仓库已初始化
) else (
    echo 仓库已存在
)

echo.
echo [3/3] 暂存并提交...
git add app.py bootstrap.py requirements.txt Dockerfile .dockerignore README.md
git add demo/ data/ engine_input/ assets/ .streamlit/
git add vendor/ docs/
git commit -m "SupplySync demo for Streamlit Cloud" 2>nul

echo.
echo ============================================
echo   ✅ 本地仓库已准备好！
echo ============================================
echo.
echo   接下来你需要做两步（需要 GitHub 账号）：
echo.
echo   1. 在 GitHub 新建一个空仓库，然后执行：
echo       git remote add origin https://github.com/你的用户名/仓库名.git
echo       git push -u origin main
echo.
echo   2. 打开 https://streamlit.io/cloud 用 GitHub 登录
echo      → New app → 选刚推的仓库
echo      → Entry file 填 app.py → Deploy
echo.
echo   部署完成后会得到: https://xxx.streamlit.app
echo ============================================
pause
