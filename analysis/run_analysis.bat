@echo off
setlocal

cd /d %~dp0

REM 修改成你自己的实验目录
set TX_LOG=..\output\run_other\tx_log.csv
set RX_LOG=..\output\run_other\rx_log.csv
set OUT_DIR=results\run_other_analysis

python tools\analyze_loop_rt_logs.py ^
  --tx-log "%TX_LOG%" ^
  --rx-log "%RX_LOG%" ^
  --out-dir "%OUT_DIR%"

echo.
echo Analysis done.
pause
