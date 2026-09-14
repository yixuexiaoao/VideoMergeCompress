$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m pip install --require-hashes -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
python -m PyInstaller --noconfirm VideoMergeCompress.spec
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
& "$PSScriptRoot\packaging\inno\ISCC.exe" "$PSScriptRoot\packaging\installer.iss"
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
Get-ChildItem -LiteralPath "$PSScriptRoot\release" -Filter '*.exe' | Get-FileHash -Algorithm SHA256 | Format-List
