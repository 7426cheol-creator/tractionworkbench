@echo off
REM Build the Windows application locally (requires Python 3.10+ on PATH).
REM Result: dist\TractionWorkbench\TractionWorkbench.exe and dist\TractionWorkbench-<ver>-windows-x64.zip
setlocal
cd /d "%~dp0\.."
python -m venv .venv-build || goto :error
call .venv-build\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
pip install -e ".[gui,build]" || goto :error
python packaging\build.py || goto :error
echo.
echo Done: dist\TractionWorkbench\TractionWorkbench.exe
exit /b 0
:error
echo Build failed.
exit /b 1
