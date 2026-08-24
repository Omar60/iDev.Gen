@echo off
REM iDev.Gen - the LAN entry point. This file prints the warning that
REM the rest of the app cannot: the whole interface is about to be
REM reachable from every device on this network, with no authentication.
REM Anything that needs a password (delete a session, queue a generation)
REM is reachable from there too. The bootstrap is delegated to start.bat
REM so a change to how the app is prepared cannot leave one entry point
REM working and the other broken.
echo.
echo WARNING: start-lan.bat exposes the entire iDev.Gen app to every
echo device on this network. There is no authentication: any device on
echo the same network can read the photographs, delete sessions and
echo queue generations. Use only on a trusted network.
echo.
call "%~dp0start.bat" 0.0.0.0
