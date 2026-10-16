@echo off
rem Builds cv1bridge.exe with Visual Studio's C compiler. Needs the Oculus PC SDK (ovr_sdk_win_32.0.0.zip
rem from developers.meta.com) unpacked: set OVR_SDK to its LibOVR folder if it isn't in ..\..\ext\ovr_sdk.
setlocal
if not defined OVR_SDK set OVR_SDK=%~dp0..\..\ext\ovr_sdk\LibOVR
if not exist "%OVR_SDK%\Include\OVR_CAPI.h" (echo Oculus PC SDK not found in %OVR_SDK% & exit /b 1)
for /f "usebackq delims=" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -property installationPath`) do set VS=%%i
call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul
cd /d "%~dp0"
cl /nologo /O2 /W3 /I"%OVR_SDK%\Include" cv1bridge.c "%OVR_SDK%\Shim\OVR_CAPIShim.c" /Fe:cv1bridge.exe /link ws2_32.lib winmm.lib user32.lib d3d11.lib dxgi.lib dxguid.lib
del /q *.obj 2>nul
