@echo off
rem PC do Arthur: hospeda o servidor e abre o inZOI.
cd /d "%~dp0"
python client\launcher.py --host --name "Arthur" %*
