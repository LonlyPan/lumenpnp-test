# LumenPnP Test

基于 Python / PySide6 的 LumenPnP 主板通信测试工具。

## 功能

- 串口热插拔排序、手动选择优先与失败回退扫描。
- G28 归零，XYZ 箭头点动，R/L 旋转（R→B、L→A）。连接后即可手动移动。
- OpenPnP 五档步进：0.01、0.1、1、10、100；全局速度 0–100%，基础速度读取 machine.xml。
- 左右两路气泵和电磁阀联动启停，成功后按钮切换；压力原始值独立显示并保留日志。
- Photon 飞达只读查询：默认 1–50，编号圆圈检测成功变绿，范围与高度动态调整。

## Windows 直接运行

下载 Release 中的 `LumenPnP-BoardTester.exe`，或使用 `board_tester/dist/` 中同一版本文件。无需安装 Python。Windows x64 已验证窗口启动。

## 从源码运行

```sh
python -m pip install -r board_tester/requirements.txt
python board_tester/main.py
python -m unittest discover -s board_tester -p "test_*.py"
```

源码面向 Windows/macOS/Linux；此版本仅验证 Windows，其他平台需验证串口和 Qt 环境。

Windows 打包：先安装 PyInstaller，再执行 `powershell -ExecutionPolicy Bypass -File board_tester/build_exe.ps1`。

## 使用说明

关闭占用串口的 OpenPnP，选择串口后连接，等待约六秒。顺序测试归零、旋转、气路压力、飞达通信。手动点动不要求先归零，是否允许运动仍由固件决定。

XYZ 配置速度 200 mm/s；A/B 配置上限为 50000/秒，实际受控制板固件与加速度限制。百分比为零只阻止新点动，不能停止已发出的运动。

Z 联动两个吸嘴。R/L、左右气路与接线需实机核对。气路关闭/退出不会自动关闭输出，结束前使用按钮关闭。压力仅显示原始值，不换算 kPa。Photon 只查询已分配地址的飞达，不送料、不初始化、不改地址。日志用于诊断，串口打开不等于固件身份验证成功。

## 许可与来源

GPL-3.0-or-later，见 LICENSE。Photon 协议参考 OpenPnP（https://github.com/openpnp/openpnp）的 Packet、CRC8_107 与 GetFeederId 实现。机器配置来自本工程提供的 LumenPnP v4.1 配置，硬件文档见 https://docs.opulo.io/ 。

本仓库从当前源码创建单个初始提交，不含开发聊天、旧版本 EXE、构建缓存或先前 Git 历史。
