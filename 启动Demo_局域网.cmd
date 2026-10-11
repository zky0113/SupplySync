@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   SupplySync 局域网共享版启动
echo ============================================
echo.
echo   本机访问:  http://127.0.0.1:8501
for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr /c:"IPv4" ^| findstr /v "127.0.0.1"') do (
    for /f "tokens=1" %%j in ("%%i") do echo   局域网:   http://%%j:8501
)
echo.
echo   同 Wi-Fi 的手机/电脑可直接访问上面的局域网地址
echo   关闭此窗口 = 停止服务
echo ============================================
echo.

python -m streamlit run app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true

pause
