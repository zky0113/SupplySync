@echo off
chcp 65001 >nul
echo ============================================
echo   SupplySync 一键推送到 GitHub
echo   代理端口: 7897 (梯子请保持开启)
echo ============================================
echo.

cd /d "%~dp0"

:: 配置项目本地代理（不污染全局）
git config http.proxy http://127.0.0.1:7897
git config https.proxy http://127.0.0.1:7897
git config http.sslVerify false

echo [1/3] 代理已配置，正在推送...
echo.

git push -u origin main
set PUSH_RESULT=%ERRORLEVEL%

echo.

:: 清除本地代理配置
echo [2/3] 清除本地代理配置...
git config --unset http.proxy
git config --unset https.proxy
git config --unset http.sslVerify

if %PUSH_RESULT%==0 (
    echo.
    echo ============================================
    echo   ✅ 推送成功！
    echo   现在去 Streamlit Cloud 点 Manage app
    echo   -^> Reboot app 即可
    echo ============================================
) else (
    echo.
    echo ============================================
    echo   ❌ 推送失败，错误码: %PUSH_RESULT%
    echo   请检查:
    echo   1. 梯子是否已开启并监听 7897 端口
    echo   2. 梯子的 HTTP 代理端口确实是 7897
    echo   3. 梯子是否允许本地回环连接
    echo ============================================
)

echo.
pause
