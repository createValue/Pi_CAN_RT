\# CAN Timing Analysis on Windows



本目录用于分析：



1\. 程序内部 CSV 日志

2\. 外部 BUSMASTER CAN log



并输出：

\- 内部调度实时性

\- 总线实际出现实时性

\- 周期 jitter

\- 统计表

\- PNG 图



\## 目录

\- `tools/analyze\_can\_timing\_enhanced.py`：分析脚本

\- `requirements.txt`：Python 依赖

\- `run\_analysis\_windows.bat`：Windows 一键运行

\- `results/`：分析结果输出目录



\## 依赖安装

建议先安装 Python 3.10+



然后执行：



```bash

pip install -r requirements.txt

```



\## Windows 运行

修改 `run\_analysis\_windows.bat` 里的输入文件路径，然后执行：



```bash

run\_analysis\_windows.bat

```



或者在命令行手动执行：



```bash

python tools\\analyze\_can\_timing\_enhanced.py ^

&#x20; --internal-csv ..\\output\\G01.csv ^

&#x20; --bus-log ..\\logs\\busmaster.log ^

&#x20; --out-dir results\\run1

```



\## 输出文件

\- `matched\_timing.csv`

\- `internal\_period\_jitter.csv`

\- `bus\_period\_jitter.csv`

\- `summary\_stats.csv`

\- `unmatched\_internal.csv`

\- `unmatched\_bus.csv`

\- `plots/\*.png`



\## 主要指标

\- `wakeup\_latency\_ns`

\- `send\_path\_delay\_ns`

\- `app\_lateness\_ns`

\- `bus\_queue\_delay\_ns`

\- `bus\_lateness\_ns`

\- `internal\_period\_jitter\_ns`

\- `bus\_period\_jitter\_ns`



