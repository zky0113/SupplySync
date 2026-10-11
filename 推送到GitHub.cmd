@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "GIT=C:\Program Files\Git\cmd\git.exe"
set "REMOTE=https://ghfast.top/https://github.com/zky0113/SupplySync.git"

echo ============================================
echo   Push to GitHub (via ghfast mirror)
echo ============================================
echo.

if not exist "%GIT%" (
    echo [!] Git not found at %GIT%
    pause
    exit /b 1
)

echo [1/3] Setting remote URL to ghfast mirror...
"%GIT%" remote set-url origin "%REMOTE%"
"%GIT%" config http.sslVerify false

echo.
echo [2/3] Pushing to GitHub...
echo.
"%GIT%" push origin main
set "PUSH_CODE=%ERRORLEVEL%"

echo.
echo [3/3] Restoring original remote URL...
"%GIT%" remote set-url origin "https://github.com/zky0113/SupplySync.git"
"%GIT%" config --unset http.sslVerify 2>nul

if %PUSH_CODE% equ 0 (
    echo.
    echo ============================================
    echo   PUSH SUCCESS!
    echo   Streamlit Cloud will auto-redeploy.
    echo   Wait 2-3 minutes then refresh.
    echo ============================================
) else (
    echo.
    echo ============================================
    echo   PUSH FAILED (exit code %PUSH_CODE%)
    echo   Try running with VPN or try again.
    echo ============================================
)

echo.
pause
