import sys
import can
import os
import main_window_logic as logic
import pyqtgraph as pg
from PyQt5.QtWidgets import QMainWindow, QFileDialog, QMessageBox, QLabel
from PyQt5.QtWidgets import QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout
from PyQt5.QtWidgets import QGridLayout, QScrollArea, QComboBox, QCheckBox
from PyQt5.QtWidgets import QListWidget, QListWidgetItem, QGroupBox, QWidget
from PyQt5.QtWidgets import QProgressDialog, QProgressBar, QSplitter
from PyQt5.QtCore import QTimer, Qt
from can_receiver import CANReceiver
from main_window_logic import handle_received_message
from bootloader_update import ProgressThread, StateMachine
from PyQt5.QtGui import QIcon
from custom_viewbox import ZoomableViewBox


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = None
        self.message_data = {}
        self.bus = None
        self.graph_data = {}
        self.pause_time_axis = False
        self.time_axis_timer = QTimer()
        self.message_data_dicts = {i: {} for i in range(32)}
        self.current_message_name = None
        self.debug_output = False
        self.mouse_pressed = False
        self.disconnecting = False
        self.can_receiver = CANReceiver(self)
        self.can_receiver.message_received.connect(self.handle_received_message)
        self.can_receiver.start()
        self.bus = None
        self.state_machine = None
        self.progress_thread = None
        self.update_in_progress = False
        self.progress_dialog = None
        self.uses_adjusted_id = False
        self.graph_plot_items = {}  # 시그널별 PlotDataItem 객체 저장용
        self.graph_start_time = None  # 상대 시간 기준 (0부터 시작)
        self.pause_can_updates = False

        self.initUI()

    def _set_control_mode_exclusive(self, source_checkbox, checked: bool):
        if not checked:
            return

        other_checkboxes = [self.pos_checkbox, self.vel_checkbox, self.torq_checkbox]
        for checkbox in other_checkboxes:
            if checkbox is source_checkbox:
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)

    def initUI(self):
        if getattr(sys, "frozen", False):
            application_path = sys._MEIPASS
        else:
            application_path = os.path.dirname(__file__)

        self.setWindowTitle("MSTG Drive module control SW Rev 1.36")
        icon_path = os.path.join(application_path, "HL.ico")
        self.setWindowIcon(QIcon(icon_path))

        central_widget = QWidget()
        main_layout = QVBoxLayout()

        self.setup_top_bar(main_layout)
        self.setup_main_panel(main_layout)
        self.setCentralWidget(central_widget)
        central_widget.setLayout(main_layout)

        self.send_timer = QTimer()
        self.send_timer.timeout.connect(lambda: logic.send_messages(self))

        self.statemachine_timer = QTimer()
        self.statemachine_timer.timeout.connect(self.run_next_state)

    def setup_top_bar(self, layout):
        top_layout = QHBoxLayout()

        self.device_combo = QComboBox()
        self.device_combo.addItems(["kvaser", "pcan"])
        top_layout.addWidget(QLabel("CAN Device:"))
        top_layout.addWidget(self.device_combo)

        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["125000", "250000", "500000", "1000000"])
        self.bitrate_combo.setCurrentText("1000000")
        top_layout.addWidget(QLabel("Baudrate:"))
        top_layout.addWidget(self.bitrate_combo)

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(lambda: logic.connect_device(self))
        top_layout.addWidget(self.connect_button)

        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(lambda: logic.disconnect_device(self))
        top_layout.addWidget(self.disconnect_button)

        layout.addLayout(top_layout)

    def setup_main_panel(self, layout):
        splitter = QSplitter(Qt.Horizontal)

        # 좌측: 메시지 리스트
        self.message_list = QListWidget()
        self.message_list.itemClicked.connect(
            lambda item: logic.select_message(self, item)
        )
        splitter.addWidget(self.message_list)

        # 중앙: 데이터 표시 (스크롤 영역 포함)
        self.data_group = QGroupBox("Message Data")
        self.data_layout = QGridLayout()
        self.data_group.setLayout(self.data_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.data_group)

        center_widget = QWidget()
        center_layout = QVBoxLayout()
        center_layout.addWidget(self.scroll_area)

        self.update_button = QPushButton("Update Message")
        self.update_button.clicked.connect(lambda: logic.update_message(self))
        center_layout.addWidget(self.update_button)

        center_widget.setLayout(center_layout)
        splitter.addWidget(center_widget)

        # 우측: 그래프 및 제어 패널
        right_widget = QWidget()
        graph_layout = QVBoxLayout()

        self.graph_widget = pg.PlotWidget(viewBox=ZoomableViewBox())
        graph_layout.addWidget(self.graph_widget)

        # ✅ 오른쪽 Y축 추가 (ViewBox + AxisItem)
        self.right_axis = pg.AxisItem("right")
        self.right_axis.setLabel(text="Right Y Axis")
        self.graph_widget.plotItem.layout.addItem(self.right_axis, 2, 2)  # row=2, col=2

        self.right_viewbox = pg.ViewBox()
        self.graph_widget.plotItem.scene().addItem(self.right_viewbox)

        # 오른쪽 Y축과 ViewBox 연결
        self.right_axis.linkToView(self.right_viewbox)
        self.right_viewbox.setXLink(
            self.graph_widget.plotItem
        )  # X축은 좌측과 동일하게 연동

        # 그래프 리사이즈 시 오른쪽 ViewBox 자동 정렬
        def update_right_view():
            self.right_viewbox.setGeometry(
                self.graph_widget.plotItem.vb.sceneBoundingRect()
            )

        self.graph_widget.plotItem.vb.sigResized.connect(update_right_view)

        self.graph_widget.setBackground("w")
        self.graph_widget.setLabel("left", "Value")
        self.graph_widget.setLabel("bottom", "Time", units="s")
        self.graph_widget.addLegend()
        self.graph_widget.enableAutoRange(axis="y")
        self.graph_widget.setMouseEnabled(x=True, y=True)
        self.graph_widget.scene().sigMouseMoved.connect(self.mouseMoved)
        self.graph_widget.scene().sigMouseClicked.connect(self.mouseReleaseEvent)

        self.clear_graph_button = QPushButton("Clear Graph")
        self.clear_graph_button.clicked.connect(lambda: logic.clear_graph(self))
        graph_layout.addWidget(self.clear_graph_button)

        self.auto_scale_button = QPushButton("Auto Scale")
        self.auto_scale_button.setCheckable(True)
        self.auto_scale_button.setChecked(True)
        self.auto_scale_button.clicked.connect(lambda: logic.toggle_auto_scale(self))
        graph_layout.addWidget(self.auto_scale_button)

        self.toggle_time_axis_button = QPushButton("Pause Time Axis")
        self.toggle_time_axis_button.setCheckable(True)
        self.toggle_time_axis_button.clicked.connect(
            lambda: logic.toggle_time_axis(self)
        )
        graph_layout.addWidget(self.toggle_time_axis_button)

        self.graph_data_combo = QComboBox()
        graph_layout.addWidget(self.graph_data_combo)
        self.graph_data_combo2 = QComboBox()
        graph_layout.addWidget(self.graph_data_combo2)

        self.pos_checkbox = QCheckBox("Position Control")
        self.vel_checkbox = QCheckBox("Velocity Control")
        self.torq_checkbox = QCheckBox("Torque Control")

        checkbox_layout = QHBoxLayout()
        checkbox_layout.addWidget(self.pos_checkbox)
        checkbox_layout.addWidget(self.vel_checkbox)
        checkbox_layout.addWidget(self.torq_checkbox)
        graph_layout.addLayout(checkbox_layout)

        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("Enter ID (1-31)")
        graph_layout.addWidget(self.id_input)

        self.scan_button = QPushButton("Scan CAN Bus")
        self.scan_button.clicked.connect(lambda: logic.scan_can_bus(self))
        graph_layout.addWidget(self.scan_button)

        self.load_dbc_button = QPushButton("Load DBC File")
        self.load_dbc_button.clicked.connect(lambda: logic.load_dbc_file(self))
        graph_layout.addWidget(self.load_dbc_button)

        self.bootstrap_update_button = QPushButton("BootStrap Update")
        self.bootstrap_update_button.clicked.connect(self.start_bootstrap_update)
        graph_layout.addWidget(self.bootstrap_update_button)

        self.normal_fw_update_button = QPushButton("Normal FW Update")
        self.normal_fw_update_button.clicked.connect(self.start_normalboot_update)
        graph_layout.addWidget(self.normal_fw_update_button)

        self.debug_checkbox = QCheckBox("Enable Debug Output")
        self.debug_checkbox.stateChanged.connect(
            lambda state: logic.toggle_debug_output(self, state)
        )
        graph_layout.addWidget(self.debug_checkbox)

        right_widget.setLayout(graph_layout)
        splitter.addWidget(right_widget)

        # 초기 크기 비율 설정 (선택사항)
        splitter.setSizes([200, 300, 500])

        layout.addWidget(splitter)

        # 제어 체크박스 연결
        self.pos_checkbox.stateChanged.connect(lambda: logic.control_mode_changed(self))
        self.vel_checkbox.stateChanged.connect(lambda: logic.control_mode_changed(self))
        self.torq_checkbox.stateChanged.connect(
            lambda: logic.control_mode_changed(self)
        )

        # Make control mode checkboxes mutually exclusive (only one can be checked).
        self.pos_checkbox.toggled.connect(
            lambda checked: self._set_control_mode_exclusive(self.pos_checkbox, checked)
        )
        self.vel_checkbox.toggled.connect(
            lambda checked: self._set_control_mode_exclusive(self.vel_checkbox, checked)
        )
        self.torq_checkbox.toggled.connect(
            lambda checked: self._set_control_mode_exclusive(self.torq_checkbox, checked)
        )

    def start_bootstrap_update(self):
        hex_file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Hex File", "", "Hex Files (*.hex)"
        )
        if hex_file_path:
            self.pause_can_updates = True
            self.state_machine = StateMachine(self, hex_file_path)
            self.create_progress_dialog()
            self.state_machine.start_bootstrap()
            self.statemachine_timer.start(10)

    def start_normalboot_update(self):
        hex_file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Hex File", "", "Hex Files (*.hex)"
        )
        if hex_file_path:
            self.pause_can_updates = True
            self.state_machine = StateMachine(self, hex_file_path)
            self.create_progress_dialog()
            self.state_machine.start_normalboot()
            self.statemachine_timer.start(10)

    def run_next_state(self):
        if self.state_machine:
            self.state_machine.run_next_state()

    def handle_received_message(self, msg):
        logic.handle_received_message(self, msg)

    def create_progress_dialog(self):
        self.progress_dialog = QProgressDialog(
            "Uploading Bootloader...", "Cancel", 0, 100, self
        )
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setWindowTitle("Bootloader Update Progress")
        self.progress_dialog.setAutoClose(False)
        self.progress_dialog.setAutoReset(False)
        self.progress_dialog.canceled.connect(self.cancel_update)
        self.progress_dialog.show()

        self.progress_thread = ProgressThread(self.state_machine.total_lines)
        self.progress_thread.progress_updated.connect(self.update_progress_window)
        self.progress_thread.start()
        self.update_in_progress = True

    def update_progress_window(self, value):
        if self.progress_dialog:
            self.progress_dialog.setValue(value)
            if value >= 100:
                self.finish_update()

    def finish_update(self):
        if self.update_in_progress:
            self.update_in_progress = False
            if self.progress_dialog:
                self.progress_dialog.close()
            if self.progress_thread:
                self.progress_thread.stop()
            QMessageBox.information(self, "BootStrap Update", "Update Complete")
        self.pause_can_updates = False

    def statemachine_completed(self):
        self.statemachine_timer.stop()
        if self.state_machine:
            self.state_machine.stop()
        self.state_machine = None
        self.finish_update()

    def cancel_update(self):
        if self.update_in_progress:
            self.update_in_progress = False
            if self.state_machine:
                self.state_machine.stop()
            self.statemachine_timer.stop()
            self.state_machine = None
            if self.progress_dialog:
                self.progress_dialog.close()
            if self.progress_thread:
                self.progress_thread.stop()
            QMessageBox.information(self, "BootStrap Update", "Update Canceled")
        self.pause_can_updates = False

    # def wheelEvent(self, event):
    #     if hasattr(event, "angleDelta"):
    #         pos = event.pos()
    #         if self.graph_widget.geometry().contains(
    #             self.graph_widget.mapFromScene(pos)
    #         ):
    #             zoom_factor = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
    #             center = self.graph_widget.mapToView(pos)
    #             if self.auto_scale_button.isChecked():
    #                 self.graph_widget.getViewBox().scaleBy((1, zoom_factor), center)
    #             else:
    #                 self.graph_widget.getViewBox().scaleBy(
    #                     (zoom_factor, zoom_factor), center
    #                 )
    #             event.accept()
    #             return
    #     super().wheelEvent(event)

    def mousePressEvent(self, evt):
        if (
            self.pause_time_axis
            and not self.auto_scale_button.isChecked()
            and evt.button() == Qt.LeftButton
        ):
            self.mouse_pressed = True
            self.prev_mouse_pos = evt.pos()
            evt.accept()
        else:
            super().mousePressEvent(evt)

    def mouseReleaseEvent(self, evt):
        logic.mouseReleaseEvent(self, evt)

    def mouseMoved(self, evt):
        logic.mouseMoved(self, evt)

    def closeEvent(self, event):
        self.time_axis_timer.stop()
        self.send_timer.stop()
        if self.state_machine:
            self.state_machine.stop()
        if self.progress_dialog:
            self.progress_dialog.close()
        if not self.disconnecting:
            try:
                logic.disconnect_device(self)
            except can.CanError:
                pass
        if self.progress_thread:
            self.progress_thread.stop()
        event.accept()
        super().closeEvent(event)
