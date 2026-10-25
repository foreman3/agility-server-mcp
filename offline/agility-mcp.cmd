@echo off
setlocal
if defined AGILITY_PYTHON (
  "%AGILITY_PYTHON%" -I "%~dp0agility-mcp.py" %*
) else (
  python -I "%~dp0agility-mcp.py" %*
)
exit /b %errorlevel%
