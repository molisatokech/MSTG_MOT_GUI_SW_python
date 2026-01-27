import can
import cantools
from PyQt5.QtWidgets import QFileDialog, QMessageBox, QLineEdit, QListWidgetItem
from PyQt5.QtWidgets import (
    QLabel,
    QMainWindow,
    QGraphicsSceneMouseEvent,
    QGraphicsScene,
)
from PyQt5.QtCore import QTimer, Qt, QEvent, QPoint
from PyQt5.QtCore import QThread, QMutex, QMutexLocker, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QWheelEvent, QMouseEvent
from PyQt5.QtWidgets import QMessageBox, QLineEdit
from intelhex import IntelHex
from bootloader_update import StateMachine
from PyQt5.QtCore import QTimer, Qt
import re
import time
import os
import pyqtgraph as pg


def parse_dbc_to_dict(dbc_file):
    db = cantools.database.load_file(dbc_file)
    message_data = {}

    for message in db.messages:
        message_id = message.frame_id
        message_name = message.name

        signals = {}
        for signal in message.signals:
            signal_name = signal.name
            signals[signal_name] = 0

        message_data[f"{message_name}"] = signals

    return message_data


def connect_device(window):
    device_type = window.device_combo.currentText()
    selected_bitrate = int(window.bitrate_combo.currentText())
    if (
        window.pos_checkbox.isChecked()
        or window.vel_checkbox.isChecked()
        or window.torq_checkbox.isChecked()
    ):
        window.pos_checkbox.setChecked(False)
        window.vel_checkbox.setChecked(False)
        window.torq_checkbox.setChecked(False)

    try:
        if device_type == "kvaser":
            window.bus = can.interface.Bus(
                bustype="kvaser", channel=0, bitrate=selected_bitrate
            )
        elif device_type == "pcan":
            window.bus = can.interface.Bus(
                bustype="pcan", channel="PCAN_USBBUS1", bitrate=selected_bitrate
            )
        window.can_receiver.start()
        show_message(
            window,
            "Connected",
            f"Connected to {device_type} at {selected_bitrate} bps.",
        )
    except can.CanError as e:
        window.bus = None  # 연결 실패 시 bus를 None으로 설정
        show_message(
            window,
            "Connection Error",
            f"Failed to connect to {device_type} device.\nError: {e}",
        )


def disconnect_device(window):
    if window.disconnecting:
        return

    if (
        window.pos_checkbox.isChecked()
        or window.vel_checkbox.isChecked()
        or window.torq_checkbox.isChecked()
    ):
        window.pos_checkbox.setChecked(False)
        window.vel_checkbox.setChecked(False)
        window.torq_checkbox.setChecked(False)
        pass

    window.disconnecting = True
    try:
        window.can_receiver.stop()
        if window.bus:
            window.bus.shutdown()
            window.bus = None  # shutdown 후 window.bus를 None으로 설정
            # show_message(window, "Disconnected", "CAN device disconnected.")
    except can.CanError as e:
        show_message(
            window,
            "Disconnection Error",
            f"Failed to disconnect the device.\nError: {e}",
        )
    finally:
        window.disconnecting = False


def load_dbc_file(window):
    options = QFileDialog.Options()
    file_name, _ = QFileDialog.getOpenFileName(
        window, "Open DBC File", "", "DBC Files (*.dbc);;All Files (*)", options=options
    )
    if file_name:
        try:
            window.db = cantools.database.load_file(file_name)
            window.db_filename = os.path.basename(file_name)
            window.message_data = parse_dbc_to_dict(file_name)
            update_message_list(window)
            update_graph_data_combo(window)
            detect_dbc_structure(window)
            show_message(
                window, "DBC File Loaded", f"DBC file {file_name} loaded successfully."
            )
        except Exception as e:
            window.db = None
            show_message(window, "Error", f"Failed to load DBC file.\nError: {e}")


def show_message(window, title, message):
    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Information)
    msg_box.setWindowTitle(title)
    msg_box.setText(message)
    msg_box.setStandardButtons(QMessageBox.Ok)
    msg_box.exec_()


def scan_can_bus(window):
    if window.bus is None:
        show_message(window, "Error", "CAN bus is not connected.")
        return

    window.can_receiver.start_scan()

    # Get_MotState 메시지 송신
    for i in range(16):
        try:
            message = window.db.get_message_by_name("ID00_04_CMD_FUNC_EN")
            # message = window.db.get_message_by_name("ID0_04_Func_En")

            # 메시지 초기화 (필요한 모든 신호 설정)
            data_dict = {signal.name: 0 for signal in message.signals}
            data_dict["GET_SRV_STATE"] = 1  # GET_SRV_STATE 신호
            # data_dict["Get_MotState"] = 1  # GET_SRV_STATE 신호 설정

            data = message.encode(data_dict)
            frame_id = (i << 6) | (
                message.frame_id & 0x3F
            )  # 상위 5비트를 입력 ID로 설정
            msg = can.Message(arbitration_id=frame_id, data=data, is_extended_id=False)
            window.bus.send(msg)
            if window.debug_output:
                print(f"GET_SRV_STATE message sent with ID: {hex(frame_id)}")
        except Exception as e:
            print(f"Failed to send GET_SRV_STATE message: {e}")
            return

    # ID 17번에 대해 BRAKE_STATUS 요청 메시지 송신
    try:
        message = window.db.get_message_by_name("ID17_BRAKE_STATUS")
        data_dict = {
            signal.name: 0 for signal in message.signals
        }  # 필요 시 기본값 설정
        data = message.encode(data_dict)
        frame_id = (17 << 6) | (message.frame_id & 0x3F)
        msg = can.Message(arbitration_id=frame_id, data=data, is_extended_id=False)
        window.bus.send(msg)
        if window.debug_output:
            print(f"ID17_BRAKE_STATUS message sent with ID: {hex(frame_id)}")
    except Exception as e:
        print(f"Failed to send ID17_BRAKE_STATUS message: {e}")

    # 1초 동안 응답 대기
    QTimer.singleShot(1000, lambda: finish_scan(window))


def finish_scan(window):
    detected_ids = window.can_receiver.stop_scan()
    detected_ids_str = "\n".join(f"ID: {did}" for did in detected_ids)
    show_message(window, "Detected CAN IDs", detected_ids_str)


def update_message(window):
    if window.bus is None:
        show_message(window, "Error", "CAN bus is not connected.")
        return
    user_id = int(window.id_input.text()) & 0x1F
    if window.current_message_name not in window.message_data_dicts[user_id]:
        window.message_data_dicts[user_id][window.current_message_name] = {}

    message_data = window.message_data_dicts[user_id][window.current_message_name]
    for signal_name in window.db.get_message_by_name(
        window.current_message_name
    ).signals:
        object_name = f"{window.current_message_name}.{signal_name.name}"
        field = window.findChild(QLineEdit, object_name)
        if field is not None:
            try:
                value = field.text()
                if "." in value:
                    message_data[signal_name.name] = float(value)
                else:
                    message_data[signal_name.name] = int(value)
            except ValueError:
                print(f"Invalid value for signal {signal_name.name}")

    # 업데이트된 메시지 데이터를 다시 저장
    window.message_data_dicts[user_id][window.current_message_name] = message_data
    send_message(window, window.current_message_name)


def _build_raw_payload(message, message_data):
    """Convert physical signal values to raw values before encoding."""
    raw_payload = {}

    for signal in message.signals:
        if signal.name not in message_data:
            continue

        value = message_data[signal.name]
        # Convert textual choice labels back to their numeric representation.
        if signal.choices and isinstance(value, str):
            reversed_choices = {v: k for k, v in signal.choices.items()}
            value = reversed_choices.get(value, value)

        scale = getattr(signal, "scale", None)
        offset = getattr(signal, "offset", 0)

        try:
            if scale in (None, 0):
                # For zero scale signals, fall back to the initial value (or 0)
                # because any raw value maps to the same physical value.
                if signal.initial is not None:
                    raw_value = signal.initial
                else:
                    raw_value = value if scale is None else 0
            else:
                raw_value = (value - offset) / scale
        except ZeroDivisionError:
            raw_value = signal.initial if signal.initial is not None else 0

        if not signal.is_float and isinstance(raw_value, float):
            raw_value = int(round(raw_value))

        raw_payload[signal.name] = raw_value

    return raw_payload


def send_message(window, message_name):
    if window.bus is None:
        if (
            window.pos_checkbox.isChecked()
            or window.vel_checkbox.isChecked()
            or window.torq_checkbox.isChecked()
        ):
            window.send_timer.stop()
            window.pos_checkbox.setChecked(False)
            window.vel_checkbox.setChecked(False)
            window.torq_checkbox.setChecked(False)

        if not hasattr(window, '_bus_error_shown') or not window._bus_error_shown:
            window._bus_error_shown = True
            show_message(window, 'Error', 'CAN bus is not connected.')

        return

    user_id = int(window.id_input.text()) & 0x1F
    message = window.db.get_message_by_name(message_name)
    if not message:
        return

    user_messages = window.message_data_dicts[user_id]
    message_data = user_messages.get(message_name)
    if message_data is None:
        message_data = {signal.name: 0 for signal in message.signals}
        user_messages[message_name] = message_data
    else:
        for signal in message.signals:
            message_data.setdefault(signal.name, 0)

    try:
        raw_payload = _build_raw_payload(message, message_data)
        encoded_data = message.encode(raw_payload, scaling=False)
        frame_id = (user_id << 6) | (message.frame_id & 0x3F)
        msg = can.Message(
            arbitration_id=frame_id, data=encoded_data, is_extended_id=False
        )
        window.bus.send(msg)
        if getattr(window, "debug_output", False):
            hex_payload = " ".join(f"{byte:02X}" for byte in msg.data)
            print(
                f"[TX] {message.name} (0x{frame_id:03X}) :: {hex_payload} | raw={raw_payload}"
            )
    except (ValueError, KeyError, cantools.database.errors.EncodeError) as e:
        print(f"Failed to send message: {e}")

def control_mode_changed(window):
    if (
        window.pos_checkbox.isChecked()
        or window.vel_checkbox.isChecked()
        or window.torq_checkbox.isChecked()
    ):
        print("send_timer start")
        window.send_timer.start(10)  # 10ms 간격으로 메시지 전송 시작
    else:
        window.send_timer.stop()  # 메시지 전송 중지
        print("send_timer stop")


def send_messages(window):
    if window.pos_checkbox.isChecked():
        send_messages_containing(window, "CTRL_POS")
    elif window.vel_checkbox.isChecked():
        send_messages_containing(window, "CTRL_VEL")
    elif window.torq_checkbox.isChecked():
        send_messages_containing(window, "CTRL_TORQ")


def send_messages_containing(window, keyword):
    if hasattr(window, "db_filename") and "common" in window.db_filename.lower():
        # DBC 이름에 common 포함 → 전체 메시지
        for message_name in window.message_data.keys():
            if keyword in message_name:
                send_message(window, message_name)
    else:
        # ID 기반 필터링
        user_id = int(window.id_input.text()) & 0x1F
        for message_name in window.message_data.keys():
            if keyword in message_name and f"ID{user_id:02d}_" in message_name:
                send_message(window, message_name)


def update_data_display(window):
    user_id = int(window.id_input.text()) & 0x1F
    if window.current_message_name in window.message_data_dicts[user_id]:
        message_data = window.message_data_dicts[user_id][window.current_message_name]
    else:
        message_data = {}

    # 기존 위젯 제거
    while window.data_layout.count():
        child = window.data_layout.takeAt(0)
        if child.widget():
            child.widget().deleteLater()

    # 위젯 생성
    if window.db and window.current_message_name:  # DBC 파일이 로드된 경우
        message = window.db.get_message_by_name(window.current_message_name)
        for i, signal in enumerate(message.signals):
            label = QLabel(signal.name)
            value = QLineEdit(str(message_data.get(signal.name, 0)))
            object_name = f"{window.current_message_name}.{signal.name}"
            value.setObjectName(object_name)
            window.data_layout.addWidget(label, i, 0)
            window.data_layout.addWidget(value, i, 1)


def update_data_fields(window, message_name, decoded_data):
    for signal_name, signal_value in decoded_data.items():
        object_name = f"{message_name}.{signal_name}"
        field = window.findChild(QLineEdit, object_name)
        if field is not None:
            field.setText(str(signal_value))


def update_message_list(window):
    window.message_list.clear()

    def extract_sort_key(name):
        # ex) ID00_01_CTRL_POS → (0, 1, 'CTRL_POS')
        match = re.match(r"ID(\d+)_([^_]+)(?:_(.*))?", name)
        if match:
            id_num = int(match.group(1))
            sub_num = match.group(2)
            try:
                sub_val = int(sub_num)
            except ValueError:
                sub_val = float("inf")
            rest = match.group(3) or ""
            return (0, id_num, sub_val, rest)
        else:
            return (1, name)  # ID가 없는 건 뒤쪽에 알파벳 순

    sorted_names = sorted(window.message_data.keys(), key=extract_sort_key)

    for name in sorted_names:
        window.message_list.addItem(QListWidgetItem(name))


def update_graph_data_combo(window):
    window.graph_data_combo.clear()
    window.graph_data_combo2.clear()

    # ✅ None 항목 먼저 삽입
    window.graph_data_combo.addItem("None")
    window.graph_data_combo2.addItem("None")

    items = []

    for message_name, signals in window.message_data.items():
        for signal_name in signals.keys():
            items.append(f"{message_name}.{signal_name}")

    def sort_key(item):
        # item 예: ID00_04_CTRL_POS.ANGLE
        match = re.match(r"ID(\d+)_([0-9]+)?_?([^.]*)\.(.*)", item)
        if match:
            id_num = int(match.group(1))
            sub_id = int(match.group(2)) if match.group(2) else 9999
            mid_name = match.group(3)
            signal = match.group(4)
            return (id_num, sub_id, mid_name, signal)
        return (float("inf"), item)

    items.sort(key=sort_key)

    for item in items:
        window.graph_data_combo.addItem(item)
        window.graph_data_combo2.addItem(item)


def update_graph(window):
    selected = window.graph_data_combo.currentText()
    selected2 = window.graph_data_combo2.currentText()

    # 둘 다 비었거나 "None" 선택이면 return
    if (not selected or selected == "None") and (not selected2 or selected2 == "None"):
        return

    now = time.time()
    if window.graph_start_time is None:
        window.graph_start_time = now
    timestamp = now - window.graph_start_time
    time_window = 10.0

    def process_signal(selection, color, use_right_yaxis=False):
        if not selection or selection == "None":
            return

        try:
            msg_name, sig_name = selection.split(".")
            user_id = int(window.id_input.text()) & 0x1F
            value = window.message_data_dicts[user_id][msg_name][sig_name]
            key = f"{msg_name}.{sig_name}"

            if key not in window.graph_data:
                window.graph_data[key] = ([], [])

            x_data, y_data = window.graph_data[key]
            x_data.append(timestamp)
            y_data.append(value)

            while x_data and (timestamp - x_data[0]) > time_window:
                x_data.pop(0)
                y_data.pop(0)

            if key not in window.graph_plot_items:
                if use_right_yaxis:
                    plot_item = pg.PlotCurveItem(pen=color, name=key)
                    window.right_viewbox.addItem(plot_item)
                else:
                    plot_item = window.graph_widget.plot(
                        [], [], pen=color, name=key, symbol=None
                    )
                window.graph_plot_items[key] = plot_item

            plot_item = window.graph_plot_items[key]
            plot_item.setData(x_data, y_data)
            plot_item.show()  # 다시 표시

            if window.debug_output:
                print(
                    f"[GRAPH] key={key}, value={value}, timestamp={timestamp:.2f}, points={len(x_data)}"
                )

        except Exception as e:
            print(f"[GRAPH] Failed to process {selection}: {e}")

    # ✅ 그래프 초기화 또는 숨김 처리
    for key, plot_item in window.graph_plot_items.items():
        plot_item.hide()  # 전부 숨겨놓고, 필요한 것만 다시 표시

    # 선택된 신호만 다시 그리기
    process_signal(selected, "b", use_right_yaxis=False)
    process_signal(selected2, "r", use_right_yaxis=True)

    # x축 고정 범위
    window.graph_widget.setXRange(timestamp - time_window, timestamp)

    # y축 오토스케일
    window.graph_widget.enableAutoRange(axis="y", enable=True)
    window.right_viewbox.enableAutoRange(axis="y", enable=True)


def toggle_time_axis(window):
    window.pause_time_axis = not window.pause_time_axis
    if window.pause_time_axis:
        window.toggle_time_axis_button.setText("Resume Time Axis")
    else:
        window.toggle_time_axis_button.setText("Pause Time Axis")


def clear_graph(window):
    # 데이터, PlotItem 관리 dict 초기화
    window.graph_data.clear()

    # PlotItem 제거
    for key, plot_item in window.graph_plot_items.items():
        try:
            window.graph_widget.removeItem(plot_item)
        except:
            pass
        try:
            window.right_viewbox.removeItem(plot_item)
        except:
            pass
    window.graph_plot_items.clear()

    # 전체 Plot 위젯 정리
    window.graph_widget.clear()
    window.graph_start_time = None

    if window.debug_output:
        print("[GRAPH] Graph cleared and time reset")


def toggle_auto_scale(window):
    if window.auto_scale_button.isChecked():
        window.graph_widget.enableAutoRange(axis="y")
    else:
        window.graph_widget.disableAutoRange(axis="y")


def mouseReleaseEvent(window, evt):
    if isinstance(evt, QMouseEvent):
        if window.pause_time_axis and not window.auto_scale_button.isChecked():
            if (
                hasattr(window, "mouse_pressed")
                and window.mouse_pressed
                and evt.button() == Qt.LeftButton
            ):
                window.mouse_pressed = False
                evt.accept()
            else:
                QMainWindow.mouseReleaseEvent(window, evt)
        else:
            QMainWindow.mouseReleaseEvent(window, evt)
    elif isinstance(evt, QGraphicsSceneMouseEvent):
        if window.pause_time_axis and not window.auto_scale_button.isChecked():
            if (
                hasattr(window, "mouse_pressed")
                and window.mouse_pressed
                and evt.button() == Qt.LeftButton
            ):
                window.mouse_pressed = False
                evt.accept()
            else:
                QGraphicsScene.mouseReleaseEvent(window, evt)
        else:
            QGraphicsScene.mouseReleaseEvent(window, evt)


def mouseMoved(window, evt):
    if (
        window.pause_time_axis
        and not window.auto_scale_button.isChecked()
        and hasattr(window, "mouse_pressed")
        and window.mouse_pressed
    ):
        diff = window.prev_mouse_pos - evt
        window.graph_widget.getViewBox().translateBy(diff.x(), diff.y())
        window.prev_mouse_pos = evt


def handle_received_message(window, msg):

    if getattr(window, "pause_can_updates", False):
        return

    if window.db is None:
        print("[handle_received_message] DBC not loaded.")
        return

    try:
        # DBC 구조에 따라 frame_id를 결정
        can_id = msg.arbitration_id
        frame_id = can_id & 0x3F if window.uses_adjusted_id else can_id
        try:
            message = window.db.get_message_by_frame_id(frame_id)

        except KeyError:
            print(
                f"[handle_received_message] No DBC message for frame ID: {hex(frame_id)}"
            )
            return

        try:
            decoded_data = window.db.decode_message(
                frame_id, msg.data, decode_choices=False
            )

        except Exception as e:
            print(
                f"[handle_received_message] Decode Error: ID: {hex(frame_id)}, len: {len(msg.data)}, error: {e}"
            )
            return

        message_name = message.name
        upper_5_bits_id = (can_id >> 6) & 0x1F
        user_id = int(window.id_input.text()) & 0x1F

        full_message_name = message.name

        if upper_5_bits_id == user_id:
            if full_message_name not in window.message_data_dicts[user_id]:
                window.message_data_dicts[user_id][full_message_name] = {}

            window.message_data_dicts[user_id][full_message_name].update(decoded_data)
            update_data_fields(window, full_message_name, decoded_data)
            update_graph(window)
            # print(full_message_name)
        else:
            print(
                f"[handle_received_message] ID mismatch: message from node {upper_5_bits_id}, expected {user_id}"
            )

    except Exception as e:
        print(f"[handle_received_message] Exception: {e}")


def select_message(main_window, item):
    main_window.current_message_name = item.text()
    main_window.update_data_display()

    # 현재 선택된 signal 텍스트 백업
    current_signal1 = main_window.graph_data_combo.currentText()
    current_signal2 = main_window.graph_data_combo2.currentText()

    # 콤보 박스 아이템 갱신
    main_window.update_graph_data_combo()

    # 콤보1: 이전 선택 항목 유지 또는 첫 번째로 fallback
    if current_signal1 in [
        main_window.graph_data_combo.itemText(i)
        for i in range(main_window.graph_data_combo.count())
    ]:
        main_window.graph_data_combo.setCurrentText(current_signal1)
    elif main_window.graph_data_combo.count() > 0:
        main_window.graph_data_combo.setCurrentIndex(0)

    # 콤보2: 이전 선택 항목 유지 또는 첫 번째로 fallback
    if current_signal2 in [
        main_window.graph_data_combo2.itemText(i)
        for i in range(main_window.graph_data_combo2.count())
    ]:
        main_window.graph_data_combo2.setCurrentText(current_signal2)
    elif main_window.graph_data_combo2.count() > 0:
        main_window.graph_data_combo2.setCurrentIndex(0)

    # 그래프 갱신
    main_window.update_graph()


def toggle_debug_output(main_window, state):
    main_window.debug_output = state == Qt.Checked


def detect_dbc_structure(window):
    """DBC 메시지 이름을 분석해 ID00_만 있는지 확인하여 uses_adjusted_id 설정"""
    all_message_names = [msg.name for msg in window.db.messages]
    has_multiple_ids = any(
        name.startswith("ID") and not name.startswith("ID00_")
        for name in all_message_names
    )
    window.uses_adjusted_id = not has_multiple_ids
    # print(f"[DBC] Adjusted ID mode: {window.uses_adjusted_id}")
    # print("detect_dbc_structure : ", has_multiple_ids, window.uses_adjusted_id)
