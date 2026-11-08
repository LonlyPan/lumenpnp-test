$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$qtRuntime = python -c "import PySide6,pathlib; print(pathlib.Path(PySide6.__file__).parent)"
python -m PyInstaller --add-binary "C:/Windows/System32/icuuc.dll;." --add-binary "$qtRuntime/vcruntime140.dll;." --add-binary "$qtRuntime/vcruntime140_1.dll;." --add-binary "$qtRuntime/msvcp140.dll;." --noconfirm --clean --onefile --windowed --name LumenPnP-BoardTester --distpath board_tester/dist --workpath board_tester/build --specpath board_tester --add-data "${PWD}/LumenPnP-Config-v4-1/LumenPnP-Config-v4-1/machine.xml;LumenPnP-Config-v4-1/LumenPnP-Config-v4-1" --add-data "${PWD}/openpnp-photon/LICENSE.txt;licenses" board_tester/main.py
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }




