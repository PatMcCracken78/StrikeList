@echo off
rem Builds StrikeList.exe for Windows. Run on a Windows PC from the StrikeList folder:
rem     build\build_windows.bat
rem Output: build\release\StrikeList-<version>-windows.exe  (+ source zip)
setlocal
cd /d "%~dp0\.."
set "ROOT=%CD%"
for /f tokens^=2^ delims^=^" %%v in ('findstr /b "VERSION" strikelist.py') do set "VER=%%v"
echo Building Strike List %VER% for Windows...

py -3 -m venv build\.venv || python -m venv build\.venv || goto fail
build\.venv\Scripts\python -m pip install -q --upgrade pip
build\.venv\Scripts\python -m pip install -q -r requirements.txt pyinstaller || goto fail

build\.venv\Scripts\pyinstaller --noconfirm --clean --windowed --onefile ^
  --name StrikeList --icon "%ROOT%\assets\strikelist.ico" ^
  --add-data "%ROOT%\assets;assets" ^
  --distpath build\dist --workpath build\work --specpath build\work ^
  strikelist.py || goto fail

if not exist build\release mkdir build\release
copy /y build\dist\StrikeList.exe "build\release\StrikeList-%VER%-windows.exe" >nul
powershell -NoProfile -Command "Compress-Archive -Force -Path core.py,strikelist.py,requirements.txt,README.txt,LICENSE.txt,THIRD-PARTY-NOTICES.txt,'Start Strike List.command','Start Strike List.bat',assets,yata_awards,build\build_mac.sh,build\build_windows.bat -DestinationPath 'build\release\StrikeList-%VER%-source.zip'"
copy /y build\HOW-TO-OPEN-WINDOWS.txt "build\release\How to open (Windows).txt" >nul
rmdir /s /q build\work build\dist
echo Done: build\release\StrikeList-%VER%-windows.exe
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
