@echo off
setlocal

REM 切换到项目根目录下的 analysis 目录执行
cd /d %~dp0

REM 你可以按实际文件名修改下面两个路径
set INTERNAL_CSV=..\output\G10.csv
set BUS_LOG=..\output\G10.log
set OUT_DIR=results\run2

python tools\analyze_can_timing_enhanced.py ^
  --internal-csv "%INTERNAL_CSV%" ^
  --bus-log "%BUS_LOG%" ^
  --out-dir "%OUT_DIR%"

echo.
echo Analysis done.
pause
