from PyQt5.QtCore import QThread, pyqtSignal
from queue import Queue, Empty, Full
import can
import threading
import time


class CANReceiver(QThread):
    message_received = pyqtSignal(object)

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.running = False
        self.scanning = False
        self.detected_ids = set()
        self.message_queue = Queue(maxsize=0)  # 무제한 큐
        self.queue_lock = threading.Lock()

    def run(self):
        print("[CANReceiver] Thread started")
        self.running = True
        while self.running:
            if self.main_window.bus is None:
                time.sleep(0.1)
                continue
            try:
                msg = self.main_window.bus.recv(timeout=0.0)
                if msg is not None and msg.arbitration_id != 0:
                    self.add_message(msg)
                    self.message_received.emit(msg)
                    # print(msg)
                    if self.scanning:
                        upper_5_bits_id = (msg.arbitration_id >> 6) & 0x1F
                        self.detected_ids.add(upper_5_bits_id)
            except can.CanError as e:
                self.log_debug(f"CAN Error: {e}")

    def add_message(self, msg):
        with self.queue_lock:
            before_size = self.message_queue.qsize()
            try:
                self.message_queue.put_nowait(msg)
                after_size = self.message_queue.qsize()
            except Full:
                self.log_debug("Queue is full, message discarded")

    def get_message(self):
        with self.queue_lock:
            if not self.message_queue.empty():
                msg = self.message_queue.get_nowait()
                size = self.message_queue.qsize()
                return msg
            else:
                # self.log_debug("Queue is empty")
                return None

    def queue_size(self):
        with self.queue_lock:
            size = self.message_queue.qsize()
            return size

    def stop(self):
        self.running = False
        self.wait()

    def log_debug(self, message):
        print(f"CANReceiver: {message}")

    def start_scan(self):
        """CAN ID 스캔을 시작합니다."""
        self.scanning = True
        self.detected_ids.clear()

    def stop_scan(self):
        """CAN ID 스캔을 중지하고, 탐지된 ID를 반환합니다."""
        self.scanning = False
        return self.detected_ids
