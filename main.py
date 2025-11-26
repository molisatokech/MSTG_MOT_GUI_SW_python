# main.py
import sys
from PyQt5.QtWidgets import QApplication
from main_window import MainWindow
import main_window_logic as logic

import psutil
import os
import sys


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    # 현재 프로세스 객체 가져오기
    p = psutil.Process(os.getpid())

    # 우선순위 설정 (예: HIGH_PRIORITY_CLASS)
    p.nice(
        psutil.REALTIME_PRIORITY_CLASS
    )  # 다른 옵션: IDLE_PRIORITY_CLASS, BELOW_NORMAL_PRIORITY_CLASS 등
    # 우선순위 확인
    print("Current process priority:", p.nice())

    # 로직 함수들을 메인 윈도우 클래스에 연결
    window.connect_device = lambda: logic.connect_device(window)
    window.disconnect_device = lambda: logic.disconnect_device(window)
    window.load_dbc_file = lambda: logic.load_dbc_file(window)
    window.show_message = lambda title, message: logic.show_message(
        window, title, message
    )
    window.scan_can_bus = lambda: logic.scan_can_bus(window)
    window.finish_scan = lambda: logic.finish_scan(window)
    window.update_message = lambda: logic.update_message(window)
    window.send_message = lambda message_name: logic.send_message(window, message_name)
    window.control_mode_changed = lambda: logic.control_mode_changed(window)
    window.send_messages = lambda: logic.send_messages(window)
    window.send_messages_containing = lambda keyword: logic.send_messages_containing(
        window, keyword
    )
    window.update_data_display = lambda: logic.update_data_display(window)
    window.update_data_fields = lambda decoded_data: logic.update_data_fields(
        window, decoded_data
    )
    window.update_message_list = lambda: logic.update_message_list(window)
    window.update_graph_data_combo = lambda: logic.update_graph_data_combo(window)
    window.update_graph = lambda: logic.update_graph(window)
    window.toggle_time_axis = lambda: logic.toggle_time_axis(window)
    window.clear_graph = lambda: logic.clear_graph(window)
    window.toggle_auto_scale = lambda: logic.toggle_auto_scale(window)

    window.message_list.itemClicked.connect(
        lambda item: logic.select_message(window, item)
    )

    window.show()
    sys.exit(app.exec_())
