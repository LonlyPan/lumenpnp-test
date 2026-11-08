"""LumenPnP board communication tester. GPL-3.0-or-later (Photon protocol derived from OpenPnP)."""
import re
import sys
import time
from pathlib import Path
import xml.etree.ElementTree as ET

import serial
from serial.tools import list_ports
from PySide6.QtCore import QThread, Signal, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QPlainTextEdit, QLabel, QSpinBox, QCheckBox, QGroupBox, QGridLayout, QSlider, QLineEdit, QScrollArea)


def crc8(data):
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ (7 if crc & 128 else 0)) & 255
    return crc


def request(address, sequence):
    header = bytes([address, 0, sequence, 1])
    return (header + bytes([crc8(header + b'\x01'), 1])).hex().upper()


def response(value, address, sequence):
    data = bytes.fromhex(value)
    if len(data) != 18 or data[:4] != bytes([0, address, sequence, 13]):
        raise ValueError('Photon 响应地址、序号或长度不匹配')
    if crc8(data[:4] + data[5:]) != data[4] or data[5] != 0:
        raise ValueError('Photon CRC 或错误码无效')
    return data[6:].hex().upper()


class Worker(QThread):
    log = Signal(str)
    done = Signal(bool)
    received = Signal(str)
    feeder = Signal(int, str)

    def __init__(self, owner, commands=None, scan=None):
        super().__init__()
        self.owner, self.commands, self.scan = owner, commands, scan

    def exchange(self, command, timeout=12):
        port = self.owner.port
        self.log.emit('TX  ' + command)
        port.write((command + '\n').encode('ascii'))
        port.flush()
        deadline, lines = time.monotonic() + timeout, []
        while time.monotonic() < deadline:
            if self.isInterruptionRequested():
                raise RuntimeError('测试已中断，连接状态需重新确认')
            line = port.readline().decode('ascii', errors='replace').strip()
            if not line:
                continue
            lines.append(line)
            self.received.emit(line)
            self.log.emit('RX  ' + line)
            if line.lower().startswith(('error', '!!', 'alarm')):
                raise RuntimeError(line)
            if re.match(r'^ok(?:\s|$)', line):
                return lines
        raise TimeoutError('等待确认超时；动作结果不确定，请重新连接并归零')

    def run(self):
        try:
            if self.scan:
                found = 0
                for address in range(self.scan[0], self.scan[1] + 1):
                    seq = address & 255
                    lines = self.exchange('M485 ' + request(address, seq))
                    replies = [line.split('rs485-reply:', 1)[1].strip()
                               for line in lines if 'rs485-reply:' in line]
                    for value in replies:
                        if value == 'TIMEOUT':
                            continue
                        uuid = response(value, address, seq)
                        found += 1
                        self.feeder.emit(address, uuid)
                        self.log.emit(f'找到飞达：地址 {address}，UUID {uuid}')
                self.log.emit(f'扫描完成：找到 {found} 个已分配地址的飞达')
            else:
                for command in self.commands:
                    self.exchange(command, 120 if command.startswith(('G28', 'M400')) else 12)
            self.done.emit(True)
        except Exception as exc:
            self.log.emit('失败：' + str(exc))
            self.done.emit(False)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.port = None
        self.worker = None
        self.stage = 0
        self.port_order = {}
        self.port_counter = 0
        self.manual_port = None
        self.setWindowTitle('LumenPnP 主板通信测试')
        self.resize(900, 700)
        root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
        cfg = ET.parse(root / 'LumenPnP-Config-v4-1/LumenPnP-Config-v4-1/machine.xml')
        self.axis_rates = {}
        self.axis_acceleration = {}
        for axis in cfg.findall('.//axes/axis'):
            letter = axis.get('letter')
            rate = axis.find('feedrate-per-second')
            acceleration = axis.find('acceleration-per-second-2')
            if letter and rate is not None and acceleration is not None:
                self.axis_rates[letter] = float(rate.get('value')) * 60
                self.axis_acceleration[letter] = float(acceleration.get('value'))
        self.commands = {(c.get('head-mountable-id'), c.get('type')):
                         [s.text.strip() for s in c.findall('text') if s.text]
                         for c in cfg.findall('.//drivers/driver/command')}
        self.layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.ports = QComboBox()
        self.ports.activated.connect(self.select_port)
        row.addWidget(self.ports)
        self.flow = QCheckBox('RTS/CTS（配置默认）')
        self.flow.setChecked(True)
        row.addWidget(self.flow)
        for title, fn in [('刷新串口', self.refresh), ('自动扫描连接', self.connect), ('断开', self.disconnect)]:
            b = QPushButton(title); b.clicked.connect(fn); row.addWidget(b)
        self.layout.addLayout(row)
        self.status = QLabel('未连接；先归零，再完成 R/L 测试，最后测试气路和飞达。')
        self.layout.addWidget(self.status)
        self.controls = []
        self.button('1. 归零 G28（机器将运动）', lambda: self.run(['G21', 'G90', 'G28', 'M400', 'M114'], 1), 0)
        motion = QGroupBox('手动移动（连接后可用，无需先归零）')
        grid = QGridLayout(motion)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setVerticalSpacing(3)
        self.linear_step = QComboBox()
        self.linear_step.addItems(['0.01', '0.1', '1', '10', '100'])
        self.linear_step.setCurrentText('1')
        self.rotation_step = self.linear_step
        grid.addWidget(QLabel('步进档位（OpenPnP）'), 0, 0)
        grid.addWidget(self.linear_step, 0, 1)
        grid.addWidget(QLabel('XYZ：mm　｜　R/L：°（共用档位）'), 0, 2, 1, 2)
        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(0, 100)
        self.speed.setValue(100)
        self.speed.setSingleStep(1)
        self.speed.setPageStep(25)
        self.speed.setTickInterval(25)
        self.speed.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.speed_label = QLabel('全局移动速度：100%')
        self.speed.valueChanged.connect(lambda value: self.speed_label.setText(f'全局移动速度：{value}%'))
        grid.addWidget(self.speed_label, 2, 0)
        grid.addWidget(self.speed, 2, 1, 1, 3)
        rates_label = QLabel('100% 按 machine.xml：XYZ 200 mm/s；R/L 50000 °/s（配置上限，实际受固件和加速度限制）。')
        rates_label.setWordWrap(True)
        grid.addWidget(rates_label, 3, 0, 1, 4)
        panels = QHBoxLayout()
        groups = [
            ('XY 平移', [('Y', 1, '↑\nY+', 0, 1), ('X', -1, '←  X−', 1, 0),
                         ('X', 1, 'X+  →', 1, 2), ('Y', -1, '↓\nY−', 2, 1)]),
            ('Z 升降', [('Z', 1, '↑\nZ+', 0, 0), ('Z', -1, '↓\nZ−', 2, 0)]),
            ('R 旋转 → B', [('R', 1, '↶\nR+', 0, 0), ('R', -1, '↷\nR−', 2, 0)]),
            ('L 旋转 → A', [('L', 1, '↶\nL+', 0, 0), ('L', -1, '↷\nL−', 2, 0)]),
        ]
        for title, buttons in groups:
            group = QGroupBox(title)
            pad = QGridLayout(group)
            pad.setSpacing(3)
            pad.setContentsMargins(5, 5, 5, 5)
            for axis, sign, label, row, column in buttons:
                b = QPushButton(label)
                b.setFixedSize(62, 36)
                b.setStyleSheet('QPushButton { font-size: 14px; font-weight: 600; padding: 1px; }')
                b.setObjectName(f'jog_{axis}_{"minus" if sign < 0 else "plus"}')
                b.setToolTip(f'{axis} 轴{"正" if sign > 0 else "负"}方向移动一个所选步进')
                b.clicked.connect(lambda checked=False, a=axis, s=sign: self.jog(a, s))
                pad.addWidget(b, row, column)
                self.controls.append((b, 0))
            if title == 'XY 平移':
                center = QLabel('XY'); center.setStyleSheet('font-size: 14px; padding: 2px;')
                pad.addWidget(center, 1, 1)
            panels.addWidget(group)
        grid.addLayout(panels, 1, 0, 1, 4)
        self.layout.addWidget(motion)
        self.button('2. 我已验证 R 和 L 旋转正常', lambda: self.advance(2), 1)
        air = QGroupBox('左右气路（气泵与电磁阀联动）')
        air_grid = QGridLayout(air)
        self.air_state = {1: None, 2: None}
        self.air_buttons = {}
        self.pressure_fields = {}
        for channel, side in [(1, '左路 VAC1'), (2, '右路 VAC2')]:
            air_grid.addWidget(QLabel(side), channel, 0)
            b = QPushButton('开启气泵＋电磁阀（当前状态未知）')
            b.clicked.connect(lambda checked=False, c=channel: self.actuate(c, self.air_state[c] is not True))
            self.air_buttons[channel] = b
            air_grid.addWidget(b, channel, 1); self.controls.append((b, 2))
            b = QPushButton('读取压力原始值')
            b.clicked.connect(lambda checked=False, c=channel: self.pressure(c))
            air_grid.addWidget(b, channel, 2); self.controls.append((b, 2))
            field = QLineEdit(); field.setReadOnly(True); field.setPlaceholderText('尚未读取')
            self.pressure_fields[channel] = field
            air_grid.addWidget(field, channel, 3)
        self.layout.addWidget(air)
        self.button('3. 我已验证气路和压力响应正常', lambda: self.advance(3), 2)
        row = QHBoxLayout()
        self.start = QSpinBox(); self.start.setRange(1, 254); self.start.setValue(1)
        self.end = QSpinBox(); self.end.setRange(1, 254); self.end.setValue(50)
        row.addWidget(QLabel('4. Photon 查询地址范围')); row.addWidget(self.start); row.addWidget(self.end)
        b = QPushButton('查找飞达（仅 GetFeederId）'); b.clicked.connect(self.scan)
        row.addWidget(b); self.controls.append((b, 3)); self.layout.addLayout(row)
        self.feeder_panel = QWidget()
        self.feeder_grid = QGridLayout(self.feeder_panel)
        self.feeder_labels = {}
        self.feeder_scroll = QScrollArea()
        self.feeder_scroll.setWidgetResizable(True)
        self.feeder_scroll.setWidget(self.feeder_panel)
        self.feeder_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layout.addWidget(self.feeder_scroll)
        self.start.valueChanged.connect(self.rebuild_feeders)
        self.end.valueChanged.connect(self.rebuild_feeders)
        self.rebuild_feeders()
        self.output = QPlainTextEdit(); self.output.setReadOnly(True); self.layout.addWidget(self.output)
        self.refresh(); self.update_controls()
        self.port_timer = QTimer(self)
        self.port_timer.timeout.connect(self.refresh)
        self.port_timer.start(1000)

    def rebuild_feeders(self):
        count = max(0, self.end.value() - self.start.value() + 1)
        rows = (count + 14) // 15
        self.feeder_grid.setContentsMargins(6, 6, 6, 6)
        self.feeder_grid.setSpacing(4)
        self.feeder_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.feeder_scroll.setFixedHeight(min(220, max(18, rows * 36 + 10)))
        while self.feeder_grid.count():
            item = self.feeder_grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.feeder_labels = {}
        for i, address in enumerate(range(self.start.value(), self.end.value() + 1)):
            label = QLabel(str(address)); label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedSize(32, 32)
            label.setStyleSheet('border: 1px solid #999; border-radius: 16px; background: transparent;')
            self.feeder_grid.addWidget(label, i // 15, i % 15)
            self.feeder_labels[address] = label

    def mark_feeder(self, address, uuid):
        label = self.feeder_labels.get(address)
        if label:
            label.setStyleSheet('border: 1px solid #268442; border-radius: 16px; background: #36bd66; color: black;')
            label.setToolTip('UUID: ' + uuid)

    def button(self, title, fn, stage):
        b = QPushButton(title); b.clicked.connect(fn); self.layout.addWidget(b); self.controls.append((b, stage))

    def select_port(self, index):
        self.manual_port = self.ports.itemData(index)

    def refresh(self):
        devices = list(list_ports.comports())
        present = {p.device for p in devices}
        # Forget absent ports so reinsertion receives a new arrival sequence.
        self.port_order = {key: value for key, value in self.port_order.items() if key in present}
        for p in devices:
            if p.device not in self.port_order:
                self.port_counter += 1
                self.port_order[p.device] = self.port_counter
        devices.sort(key=lambda p: self.port_order[p.device], reverse=True)
        selected = self.manual_port if self.manual_port in present else None
        self.ports.blockSignals(True)
        self.ports.clear()
        self.ports.addItem('自动选择（最新插入优先）', None)
        for p in devices:
            self.ports.addItem(f'{p.device} — {p.description}', p.device)
        self.ports.setCurrentIndex(max(0, self.ports.findData(selected)))
        self.ports.blockSignals(False)

    def connect(self):
        if self.port:
            return
        self.refresh()
        candidates = [self.ports.itemData(i) for i in range(self.ports.count()) if self.ports.itemData(i)]
        if self.manual_port in candidates:
            candidates.remove(self.manual_port)
            candidates.insert(0, self.manual_port)
        for device in candidates:
            try:
                self.output.appendPlainText(f'尝试连接 {device}…')
                self.port = serial.Serial(device, 115200, timeout=.2,
                                          write_timeout=2, rtscts=self.flow.isChecked())
                self.output.appendPlainText(f'{device} 已打开（尚未验证固件身份），等待控制板启动约 6 秒后再归零。')
                self.ready_at = time.monotonic() + 6
                self.stage = 0; self.update_controls()
                return
            except (serial.SerialException, OSError) as exc:
                self.output.appendPlainText(f'{device} 连接失败：{exc}；继续扫描下一串口。')
        self.output.appendPlainText('没有可连接的串口。请检查 USB 接线或串口占用。')

    def disconnect(self):
        if self.worker and self.worker.isRunning():
            self.output.appendPlainText('测试进行中，请等待完成；需要立即停止运动时使用硬件急停。'); return
        if self.port:
            self.port.close(); self.port = None
        self.stage = 0; self.update_controls()

    def update_controls(self):
        busy = bool(self.worker and self.worker.isRunning())
        self.start.setEnabled(not busy)
        self.end.setEnabled(not busy)
        for button, stage in self.controls:
            button.setEnabled(bool(self.port) and not busy and self.stage >= stage)
        self.status.setText(f'连接：{getattr(self.port, "port", "未连接")}；阶段：{self.stage}；忙碌：{busy}')

    def advance(self, stage):
        self.stage = stage; self.update_controls()

    def run(self, commands=None, next_stage=None, scan=None, on_success=None, on_line=None):
        if not self.port or (self.worker and self.worker.isRunning()):
            return
        if time.monotonic() < self.ready_at:
            self.output.appendPlainText('请等待控制板启动完成。'); return
        self.worker = Worker(self, commands, scan)
        self.worker.log.connect(self.output.appendPlainText)
        self.worker.feeder.connect(self.mark_feeder)
        if on_line:
            self.worker.received.connect(on_line)
        def complete(ok):
            if ok and on_success:
                on_success()
            if not ok:
                for c in self.air_state:
                    self.air_state[c] = None
                    self.air_buttons[c].setText('开启气泵＋电磁阀（当前状态未知）')
            if ok and next_stage is not None:
                self.stage = next_stage
            if not ok:
                self.stage = 0
                self.port.close(); self.port = None
        self.worker.done.connect(complete)
        self.worker.finished.connect(self.update_controls)
        self.worker.start(); self.update_controls()

    def jog(self, axis, sign):
        step = self.linear_step if axis in 'XYZ' else self.rotation_step
        self.move(axis, sign * float(step.currentText()))

    def move(self, axis, delta):
        # R/L are UI names for firmware A/B; no G92 coordinate changes.
        if self.speed.value() == 0:
            self.output.appendPlainText('全局速度为 0%，未发送移动指令。')
            return
        letter = {'R': 'B', 'L': 'A'}.get(axis, axis)
        scale = self.speed.value() / 100
        feed = self.axis_rates[letter] * scale
        acceleration = self.axis_acceleration[letter] * scale * scale
        self.run(['G91', f'M204 S{acceleration:.4f}', f'G1 {letter}{delta:.3f} F{feed:.2f}', 'M400', 'G90', 'M114'])

    def template(self, channel, kind, state=None):
        key = f'ACT171086{"c0660cfc3b" if channel == 1 else "f27295ab60"}'
        lines = self.commands[(key, kind)]
        if not lines:
            raise ValueError('配置缺少命令文本')
        result = []
        for line in lines:
            if state is not None:
                line = re.sub(r'\{(True|False):([^}]*)\}', lambda m: m[2] if (m[1] == 'True') == state else '', line)
            line = line.split(';', 1)[0].strip()
            if line: result.append(line)
        return result

    def actuate(self, channel, state):
        def confirmed():
            self.air_state[channel] = state
            self.air_buttons[channel].setText('关闭气泵＋电磁阀' if state else '开启气泵＋电磁阀')
        self.run(self.template(channel, 'ACTUATE_BOOLEAN_COMMAND', state), on_success=confirmed)

    def pressure(self, channel):
        # Sensor initialization copied as separate commands from supplied CONNECT_COMMAND.
        init = [f'M260 A112 B{channel} S1', 'M260 A109', 'M260 B48', 'M260 B27', 'M260 S1', 'G4 P100']
        self.pressure_fields[channel].setText('读取中…')
        def received(line):
            match = re.search(r'data:\s*(.*)', line)
            if match:
                self.pressure_fields[channel].setText(match[1])
        def completed():
            if self.pressure_fields[channel].text() == '读取中…':
                self.pressure_fields[channel].setText('未收到压力数据')
        self.run(init + self.template(channel, 'ACTUATOR_READ_COMMAND'), on_line=received, on_success=completed)

    def scan(self):
        if self.start.value() <= self.end.value():
            self.rebuild_feeders()
            self.run(scan=(self.start.value(), self.end.value()))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            event.ignore(); return
        self.disconnect(); event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    if sys.platform == 'win32':
        app.setFont(QFont('Microsoft YaHei UI', 10))
    window = Window(); window.show()
    sys.exit(app.exec())
