@echo off
cd /d "%~dp0"
set "JAVA_HOME=%~dp0jdk"
set "PATH=%JAVA_HOME%\bin;%PATH%"
"C:\Program Files\Git\bin\bash.exe" -c claude.exe
