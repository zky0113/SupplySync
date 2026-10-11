@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   SupplySync - 一键打包供云服务器部署
echo ============================================
echo.
echo 本脚本将项目打包成 supplysync.tar.gz
echo 然后上传到你的云服务器上执行:
echo   docker build -t supplysync .
echo   docker run -d --restart always -p 8501:8501 supplysync
echo.

if not exist "deploy" mkdir deploy

echo [1/3] 清理旧文件...
if exist "deploy\supplysync.tar.gz" del "deploy\supplysync.tar.gz"

echo [2/3] 打包项目（排除 .runtime .venv __pycache__ 等）...
powershell -Command "Get-ChildItem -Recurse -File | Where-Object { $_.FullName -notmatch '\\.runtime\\' -and $_.FullName -notmatch '\\.venv\\' -and $_.FullName -notmatch '\\__pycache__\\' -and $_.FullName -notmatch '\\node_modules\\' -and $_.FullName -notmatch '\\.git\\' -and $_.FullName -notmatch '\\deploy\\' -and $_.Name -notmatch 'cloudflared\.exe|cpolar\.exe|natapp\.exe' } | Compress-Archive -DestinationPath 'deploy\supplysync.zip' -Force"

echo [3/3] 转换为 tar.gz（Docker 解压更友好）...
python -c "
import zipfile, tarfile, os, io
zip_path = 'deploy/supplysync.zip'
tar_path = 'deploy/supplysync.tar.gz'
with zipfile.ZipFile(zip_path) as zf:
    with tarfile.open(tar_path, 'w:gz') as tf:
        for member in zf.infolist():
            data = zf.read(member.filename)
            ti = tarfile.TarInfo(member.filename.replace('/', os.sep))
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
os.remove(zip_path)
print('Package created:', tar_path, 'Size:', os.path.getsize(tar_path), 'bytes')
"

echo.
echo ============================================
echo   ✅ 打包完成！
echo   输出文件: deploy\supplysync.tar.gz
echo ============================================
echo.
echo   接下来需要你手动：
echo   1. 买一台云服务器（阿里云/腾讯云轻量，2核2G）
echo   2. 把 deploy\supplysync.tar.gz 上传到服务器
echo   3. SSH 到服务器执行：
echo      tar xzf supplysync.tar.gz -C /opt/supplysync
echo      cd /opt/supplysync
echo      docker build -t supplysync .
echo      docker run -d --name supplysync --restart always -p 8501:8501 supplysync
echo   4. 云控制台安全组开放 8501 端口
echo.
echo   国内用户直接访问: http://服务器IP:8501
echo ============================================
pause
