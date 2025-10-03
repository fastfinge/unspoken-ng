@echo off
cl /nologo /EHsc /LD main.cpp /I..\steamaudio\include ..\steamaudio\lib\windows-x64\phonon.lib /link /out:steam_audio.dll
if errorlevel 1 goto end
copy /y steam_audio.dll addon\globalPlugins\Unspoken
:end
del *.nvda-addon
scons
pause