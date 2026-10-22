# Build dist\luxstatus.exe: a single file with no console window.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot)

uv sync --locked
New-Item -ItemType Directory -Force build | Out-Null
uv run python -c "from luxstatus.tray import icon_image; icon_image((0, 200, 0), 256).save('build/luxstatus.ico', sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv run pyinstaller --noconfirm --clean --onefile --windowed `
    --name luxstatus --icon build/luxstatus.ico --paths . `
    --hidden-import pystray._win32 --hidden-import hid `
    luxstatus/__main__.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Built dist\luxstatus.exe"
