from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtCore import QThread, pyqtSignal
from itertools import islice
import can
import time
import queue

# Response constants
MSTG_BOOT_RSP_UPDATE_BEGIN = 0x01
MSTG_BOOT_RSP_UPDATE_END = 0x02
MSTG_BOOT_RSP_SECTOR_ERASE_END = 0x03
MSTG_BOOT_RSP_CMD_BEGIN = 0x04
MSTG_BOOT_RSP_CMD_END = 0x05
MSTG_BOOT_RSP_ENDOFFILE = 0x06
MSTG_BOOT_RSP_CRCERROR = 0x07
MSTG_BOOT_RSP_STRAP_ON = 0x08

# BOOT Define
MSTG_BOOT_START = 0x3F
MSTG_BOOT_RECORD = 0x01
MSTG_BOOT_DATA = 0x02
MSTG_BOOT_CRC = 0x03
MSTG_BOOT_RSP = 0x04
MSTG_BOOT_STRAP = 0x05


class DataSplitter:
    def __init__(self, data):
        self.data_iter = iter(data)

    def get_next_chunk(self, chunk_size=8):
        chunk = list(islice(self.data_iter, chunk_size))
        return chunk if chunk else None


class StateMachine:
    def __init__(self, window, hex_file_path):
        self.window = window
        self.hex_file_path = hex_file_path
        self.record_list = self.parse_hex_file(hex_file_path)
        self.state = "INIT"
        self.chunk_counter = 0
        self.data_iter = None
        self.current_record = None
        self.retry_count = 0
        self.max_retries = 5
        self.current_line = 0
        self.nodeid = 0
        self.cmdid = 0
        self.transid = 0
        self.start_time = None
        self.max_strap_duration = 8  # 최대 시도 시간 설정 (초)
        self.is_running = True

        self.total_lines = len(self.record_list)

        self.strap_start_time = None
        self.strap_timer = QTimer()
        self.strap_timer.timeout.connect(self.send_strap_message)

        self.response_timer = QTimer()
        self.response_timer.timeout.connect(self.check_response)

        self.can_receiver = window.can_receiver

        # False is Normal Update, True is Bootstrap Update
        self.update_mode = False

    def send_can_message(self, arbitration_id, data0, data1):
        if self.window.bus is None:
            self.log_debug(
                f"[SEND] Bus not connected. ID: {hex(arbitration_id)}, data0: {hex(data0)}, data1: {hex(data1)}"
            )
            return
        try:
            data = self.format_can_data(data0, data1)
            msg = can.Message(
                arbitration_id=arbitration_id, data=data, is_extended_id=False
            )
            self.window.bus.send(msg)
            self.log_debug(f"[SEND] Sent ID: {hex(arbitration_id)}, Data: {data}")
        except can.CanError as e:
            self.log_debug(f"[SEND] Failed to send CAN message: {e}")

    def update_progress(self):
        if not self.is_running:
            return
        # self.current_line += 1
        self.window.progress_thread.update_progress(self.current_line)

    def stop(self):
        self.is_running = False
        if self.strap_timer.isActive():
            self.strap_timer.stop()
        if self.response_timer.isActive():
            self.response_timer.stop()
        if self.window.progress_dialog:
            self.window.progress_dialog.close()
        # 기타 실행 중인 타이머나 스레드가 있다면 여기서 중지
        self.log_debug("StateMachine stopped.")

    def get_id_input(self):
        # id_input 값을 가져오는 메서드
        try:
            id_value = int(self.window.id_input.text())
            if 1 <= id_value <= 31:
                return id_value
            else:
                self.log_debug(f"Invalid ID input: {id_value}.")
                self.stop()
                return -1
        except ValueError:
            self.log_debug("Invalid ID input. Using default value 1.")
            QMessageBox.information(self.window, "BootStrap Update", "Invalid ID input")
            self.stop()
            return -1

    def start_bootstrap(self):
        self.update_mode = True
        self.record_iter = iter(self.record_list)
        self.state = "SEND_STRAP"
        self.strap_start_time = None
        self.log_debug("StateMachine started.")
        self.response_timer.start(1)  # 1ms마다 체크
        self.window.statemachine_timer.start(1)
        QMessageBox.information(self.window, "BootStrap Update", "Turn ON Motor")

    def start_normalboot(self):
        self.update_mode = False
        self.record_iter = iter(self.record_list)
        self.state = "WAIT_FOR_UPDATE_BEGIN_RESPONSE"
        self.strap_start_time = None
        self.log_debug("StateMachine started.")
        self.response_timer.start(1)  # 1ms마다 체크
        self.window.statemachine_timer.start(1)
        self.send_bootstart()

    def run_next_state(self):
        # Running Check Code
        if not self.is_running:
            return

        if self.state == "SEND_STRAP":
            if not self.strap_timer.isActive():
                self.strap_timer.start(100)  # 100ms 간격으로 strap 메시지 전송
            self.send_strap_message()
            return  # SEND_STRAP 상태에서는 여기서 종료

        elif self.state == "INIT_HEX_FORMAT_TASK":
            self.run_hex_format_task()

        elif self.state == "SEND_RECORD":
            record_data, data, checksum = self.current_record
            self.send_record(record_data, data, checksum)
            self.data_splitter = DataSplitter(data)  # 데이터 스플리터 재초기화
            self.state = "SEND_DATA_CHUNKS"

        elif self.state == "SEND_DATA_CHUNKS":
            data_chunk = self.data_splitter.get_next_chunk()
            if data_chunk:
                self.log_debug(
                    f"Data chunk: {[hex(x) for x in data_chunk]}, Line: {self.current_line}"
                )
                self.send_data(data_chunk)
            else:
                record_data, data, checksum = self.current_record
                self.log_debug(f"CRC: {hex(checksum)}")
                self.send_crc(checksum)
                self.state = "WAIT_FOR_CRC_RESPONSE"

        elif self.state in [
            "WAIT_FOR_SECTOR_ERASE_END",
            "WAIT_FOR_CMD_BEGIN",
            "WAIT_FOR_CRC_RESPONSE",
            "WAIT_FOR_CMD_BEGIN_AFTER_CRC_ERROR",
            "WAIT_FOR_ENDOFFILE",
            "WAIT_FOR_UPDATE_END",
            "WAIT_FOR_UPDATE_BEGIN_RESPONSE",
        ]:
            # 이 상태들에서는 응답을 기다리므로 아무 작업도 수행하지 않습니다.
            # 응답은 check_response 메서드에서 처리됩니다.
            pass

        elif self.state == "COMPLETE":
            self.log_debug("StateMachine completed.")
            self.stop()
            self.window.statemachine_completed()

        else:
            self.log_debug(f"Unexpected state: {self.state}")

        # 대기 상태가 아닌 경우 다음 상태로 즉시 진행
        if self.state not in [
            "WAIT_FOR_SECTOR_ERASE_END",
            "WAIT_FOR_CMD_BEGIN",
            "WAIT_FOR_CRC_RESPONSE",
            "WAIT_FOR_CMD_BEGIN_AFTER_CRC_ERROR",
            "WAIT_FOR_ENDOFFILE",
            "WAIT_FOR_UPDATE_END",
            "COMPLETE",
        ]:
            QTimer.singleShot(0, self.run_next_state)

    def send_record(self, record_data, data, checksum):
        data_to_send = record_data + [0] * (8 - len(record_data))
        data0 = int.from_bytes(bytes(data_to_send[:4]), byteorder="big")
        data1 = int.from_bytes(bytes(data_to_send[4:]), byteorder="big")
        # self.send_can_message(MSTG_BOOT_RECORD, data0, data1)
        if not self.update_mode:
            self.transid = self.get_id_input()
            msg_id = (self.transid << 6) | MSTG_BOOT_RECORD
        else:
            msg_id = MSTG_BOOT_RECORD

        self.send_can_message(msg_id, data0, data1)

    def send_data(self, data):
        data_to_send = data + [0] * (8 - len(data))
        data0 = int.from_bytes(bytes(data_to_send[:4]), byteorder="big")
        data1 = int.from_bytes(bytes(data_to_send[4:]), byteorder="big")
        # self.send_can_message(MSTG_BOOT_DATA, data0, data1)

        if not self.update_mode:
            self.transid = self.get_id_input()
            msg_id = (self.transid << 6) | MSTG_BOOT_DATA
        else:
            msg_id = MSTG_BOOT_DATA

        self.send_can_message(msg_id, data0, data1)

    def send_crc(self, checksum):
        # self.send_can_message(MSTG_BOOT_CRC, checksum, 0x00000000)
        if not self.update_mode:
            self.transid = self.get_id_input()
            msg_id = (self.transid << 6) | MSTG_BOOT_CRC
        else:
            msg_id = MSTG_BOOT_CRC

        self.send_can_message(msg_id, checksum, 0x00000000)

    def format_can_data(self, data0, data1):
        return [
            (data0 >> 24) & 0xFF,
            (data0 >> 16) & 0xFF,
            (data0 >> 8) & 0xFF,
            data0 & 0xFF,
            (data1 >> 24) & 0xFF,
            (data1 >> 16) & 0xFF,
            (data1 >> 8) & 0xFF,
            data1 & 0xFF,
        ]

    def check_response(self):
        # Running Check Code
        if not self.is_running:
            return

        if self.window.bus is None:
            self.log_debug("CAN bus is not connected. Simulating response.")
            return self.simulate_response()

        try:
            message = self.can_receiver.get_message()
            if message is None:
                return None
            else:
                can_id = message.arbitration_id
                frame_id = can_id & 0x3F if not self.window.uses_adjusted_id else can_id

                # self.nodeid = (message.arbitration_id >> 6) & 0x1F
                # self.cmdid = message.arbitration_id & 0x1F
                self.nodeid = (frame_id >> 6) & 0x1F
                self.cmdid = frame_id & 0x1F
            # print(message)
            if self.cmdid in [
                MSTG_BOOT_START,
                MSTG_BOOT_RECORD,
                MSTG_BOOT_DATA,
                MSTG_BOOT_CRC,
                MSTG_BOOT_RSP,
                MSTG_BOOT_STRAP,
            ]:
                response_code = message.data[
                    3
                ]  # 응답 코드는 4번째 바이트에 있다고 가정
                self.handle_response(response_code)
                self.log_debug(
                    f"Message type: {hex(message.arbitration_id)}, Response code: {hex(response_code)}"
                )
                return response_code
            else:
                self.log_debug(
                    f"Skipping message with unexpected ID: {hex(message.arbitration_id)}"
                )

        except Exception as e:
            self.log_debug(f"Error in check_response: {e}")

        new_queue_size = self.can_receiver.queue_size()
        self.log_debug(f"Queue size after processing: {new_queue_size}")
        return None

    def handle_response(self, response_code):
        # Running Check Code
        if not self.is_running:
            return

        if self.state == "SEND_STRAP":
            if response_code == MSTG_BOOT_RSP_UPDATE_BEGIN:
                self.log_debug("Received MSTG_BOOT_RSP_UPDATE_BEGIN.")
                self.retry_count = 0
                self.state = "WAIT_FOR_SECTOR_ERASE_END"
                self.strap_timer.stop()
                self.strap_start_time = None

        elif self.state == "WAIT_FOR_UPDATE_BEGIN_RESPONSE":
            if response_code == MSTG_BOOT_RSP_UPDATE_BEGIN:
                self.log_debug("Received MSTG_BOOT_RSP_UPDATE_BEGIN.")
                self.retry_count = 0
                self.state = "WAIT_FOR_SECTOR_ERASE_END"
                self.strap_start_time = None

        elif self.state == "WAIT_FOR_SECTOR_ERASE_END":
            if response_code == MSTG_BOOT_RSP_SECTOR_ERASE_END:
                self.log_debug("Received MSTG_BOOT_RSP_SECTOR_ERASE_END.")
                self.state = "WAIT_FOR_CMD_BEGIN"
                self.start_time = time.time()

        elif self.state == "WAIT_FOR_CMD_BEGIN":
            if response_code == MSTG_BOOT_RSP_CMD_BEGIN:
                self.log_debug("Received MSTG_BOOT_RSP_CMD_BEGIN.")
                self.state = "INIT_HEX_FORMAT_TASK"
                self.start_time = time.time()

        elif self.state == "WAIT_FOR_CRC_RESPONSE":
            if response_code == MSTG_BOOT_RSP_CMD_END:
                self.log_debug("Received MSTG_BOOT_RSP_CMD_END, line write complete")
                self.retry_count = 0
                self.state = "WAIT_FOR_CMD_BEGIN"
            elif response_code == MSTG_BOOT_RSP_CMD_BEGIN:
                self.log_debug(
                    "Received MSTG_BOOT_RSP_CMD_BEGIN, continuing to next line..."
                )
                self.retry_count = 0
                self.state = "INIT_HEX_FORMAT_TASK"
            elif response_code == MSTG_BOOT_RSP_CRCERROR:
                self.retry_count += 1
                if self.retry_count > self.max_retries:
                    self.state = "COMPLETE"
                    self.log_debug("Failed after maximum retries.")
                else:
                    self.log_debug(
                        f"CRC Error detected. Retry {self.retry_count}/{self.max_retries} for record: {self.format_record_hex(self.current_record)}"
                    )
                    self.state = "WAIT_FOR_CMD_BEGIN_AFTER_CRC_ERROR"
            elif response_code == MSTG_BOOT_RSP_ENDOFFILE:
                self.log_debug(
                    "Received MSTG_BOOT_RSP_ENDOFFILE, waiting for MSTG_BOOT_RSP_UPDATE_END..."
                )
                self.state = "WAIT_FOR_UPDATE_END"

        elif self.state == "WAIT_FOR_CMD_BEGIN_AFTER_CRC_ERROR":
            if response_code == MSTG_BOOT_RSP_CMD_BEGIN:
                self.log_debug(
                    "Received MSTG_BOOT_RSP_CMD_BEGIN after CRC error, retrying record..."
                )
                self.state = "SEND_RECORD"

        elif self.state == "WAIT_FOR_ENDOFFILE":
            if response_code == MSTG_BOOT_RSP_ENDOFFILE:
                self.log_debug(
                    "Received MSTG_BOOT_RSP_ENDOFFILE, waiting for MSTG_BOOT_RSP_UPDATE_END..."
                )
                self.state = "WAIT_FOR_UPDATE_END"
                self.start_time = time.time()

        elif self.state == "WAIT_FOR_UPDATE_END":
            if response_code == MSTG_BOOT_RSP_UPDATE_END:
                self.log_debug("Received MSTG_BOOT_RSP_UPDATE_END, update complete.")
                self.state = "COMPLETE"

        else:
            self.log_debug(
                f"Unexpected response {hex(response_code)} in state {self.state}"
            )

        # 상태 변경 후 다음 상태 실행
        if self.state != "COMPLETE":
            QTimer.singleShot(0, self.run_next_state)
        else:
            self.log_debug("StateMachine completed.")
            # 타이머 중지
            self.response_timer.stop()
            self.window.statemachine_timer.stop()

    def simulate_response(self):
        # 가상의 응답을 반환합니다 (디버그 모드용).
        if self.state == "WAIT_FOR_UPDATE_BEGIN_RESPONSE":
            return MSTG_BOOT_RSP_UPDATE_BEGIN
        elif self.state == "WAIT_FOR_SECTOR_ERASE_END":
            return MSTG_BOOT_RSP_SECTOR_ERASE_END
        elif self.state == "WAIT_FOR_CMD_BEGIN":
            return MSTG_BOOT_RSP_CMD_BEGIN
        elif self.state == "WAIT_FOR_CRC_RESPONSE":
            return MSTG_BOOT_RSP_CMD_BEGIN  # 가상의 응답으로 성공 응답을 반환합니다.
        elif self.state == "WAIT_FOR_ENDOFFILE":
            return MSTG_BOOT_RSP_ENDOFFILE
        elif self.state == "WAIT_FOR_UPDATE_END":
            return MSTG_BOOT_RSP_UPDATE_END
        return None

    def run_hex_format_task(self):
        # self.log_debug("Running Intel Hex Format Task.")
        try:
            self.current_record = next(self.record_iter)
            record_data, data, checksum = self.current_record
            self.current_line += 1  # 현재 라인 번호 증가
            self.update_progress()
            print(f"Line {self.current_line}/{self.total_lines}")
            self.data_splitter = DataSplitter(data)  # 데이터 부분만 DataSplitter로 생성
            self.state = "SEND_RECORD"
        except StopIteration:
            self.state = "WAIT_FOR_ENDOFFILE"

    def log_debug(self, message):
        if self.window.debug_output:
            print(message)
            pass

    def send_strap(self):
        self.send_can_message(MSTG_BOOT_STRAP, 0x00000000, 0x00000000)

    def send_strap_message(self):
        # Running Check Code
        if not self.is_running:
            return

        current_time = time.time()
        if self.strap_start_time is None:
            self.strap_start_time = current_time

        if current_time - self.strap_start_time >= self.max_strap_duration:
            self.log_debug("Max strap duration reached without response.")
            self.state = "COMPLETE"
            self.strap_timer.stop()
            self.window.statemachine_timer.stop()
            QMessageBox.information(
                self.window, "BootStrap Update", "Update Failed: No response"
            )
        else:
            self.send_strap()
            response = self.check_response()
            if response is not None:
                self.log_debug(f"Received response: {hex(response)}")
                if response == MSTG_BOOT_RSP_UPDATE_BEGIN:
                    self.log_debug("Received MSTG_BOOT_RSP_UPDATE_BEGIN.")
                    self.retry_count = 0
                    self.state = "WAIT_FOR_SECTOR_ERASE_END"
                    self.strap_timer.stop()
                else:
                    self.log_debug(f"Received unexpected response: {hex(response)}")
            else:
                self.log_debug(
                    f"No valid response, continuing SEND_STRAP. Time elapsed: {current_time - self.strap_start_time:.2f}s"
                )

        # SEND_STRAP 상태를 벗어났다면 run_next_state를 호출
        if self.state != "SEND_STRAP":
            QTimer.singleShot(0, self.run_next_state)

        if self.window.bus is None:
            self.log_debug("CAN bus is not connected. Would send MSTG_BOOT_STRAP.")
            return
        try:

            msg = can.Message(
                arbitration_id=MSTG_BOOT_STRAP,
                data=[0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
                is_extended_id=False,
            )
            self.window.bus.send(msg)

            self.window.bus.send(msg)
        except can.CanError as e:
            self.log_debug(f"Failed to send CAN message: {e}")

    def send_bootstart(self):
        cmd_id = self.get_id_input()
        if cmd_id == -1:
            self.log_debug("The ID entered is incorrect.")
            return
        user_id = (cmd_id << 6) + MSTG_BOOT_START
        self.send_can_message(user_id, 0x00000000, 0x00000000)

    def format_record_hex(self, record):
        record_data, data, checksum = record
        # record_data를 16진수로 변환
        hex_record_data = " ".join([f"{x:02X}" for x in record_data])
        # data를 16진수로 변환
        hex_data = " ".join([f"{x:02X}" for x in data])
        # checksum을 16진수로 변환
        hex_checksum = f"{checksum:02X}"
        return f"Record Data: [{hex_record_data}], Data: [{hex_data}], Checksum: {hex_checksum}"

    def parse_hex_file(self, file_path):
        records = []
        with open(file_path, "r") as hex_file:
            for line in hex_file.readlines():
                line = line.strip()
                if not line or line[0] != ":":
                    continue
                byte_count = int(line[1:3], 16)
                address = int(line[3:7], 16)
                record_type = int(line[7:9], 16)
                data = [
                    int(line[i : i + 2], 16) for i in range(9, 9 + byte_count * 2, 2)
                ]
                checksum = int(line[9 + byte_count * 2 : 11 + byte_count * 2], 16)
                record_data = [
                    byte_count,
                    (address >> 8) & 0xFF,
                    address & 0xFF,
                    record_type,
                ]
                records.append((record_data, data, checksum))
        return records


class ProgressThread(QThread):
    progress_updated = pyqtSignal(int)

    def __init__(self, total_lines, parent=None):
        super(ProgressThread, self).__init__(parent)
        self.total_lines = total_lines
        self.current_line = 0
        self.is_running = True

    def run(self):
        while self.is_running:
            progress = int((self.current_line / self.total_lines) * 100)
            self.progress_updated.emit(progress)
            self.msleep(100)  # 100ms마다 업데이트

    def update_progress(self, current_line):
        self.current_line = current_line

    def stop(self):
        self.is_running = False
        self.wait()  # 스레드가 완전히 종료될 때까지 기다림
