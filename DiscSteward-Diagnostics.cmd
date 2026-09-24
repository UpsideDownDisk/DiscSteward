@echo off
title DiscSteward
py -3 "%~dp0discsteward-ui.py"
if errorlevel 1 (
  echo DiscSteward could not start. Read the error above.
  pause
)
