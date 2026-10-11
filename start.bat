@echo off
REM MaintTriage one-click starter (Windows). Double-click this file.
cd /d %~dp0
echo Installing requirements (first run only takes a moment)...
python -m pip install -r requirements.txt
echo.
echo Opening the app in your browser...
start "" http://127.0.0.1:5000
echo Starting the server - keep this window open. Press Ctrl+C to stop.
python app.py
pause
