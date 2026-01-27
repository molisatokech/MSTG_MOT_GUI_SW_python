import sys
import can
import os
import main_window_logic as logic
import pyqtgraph as pg
import time
from collections import deque
from PyQt5.QtWidgets import QMainWindow, QFileDialog, QMessageBox, QLabel
from PyQt5.QtWidgets import QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout
from PyQt5.QtWidgets import QGridLayout, QScrollArea, QComboBox, QCheckBox
from PyQt5.QtWidgets import QListWidget, QListWidgetItem, QGroupBox, QWidget
from PyQt5.QtWidgets import QProgressDialog, QProgressBar, QSplitter
from PyQt5.QtWidgets import QTabWidget, QSizePolicy, QCompleter
from PyQt5.QtWidgets import QFormLayout
from PyQt5.QtWidgets import QSpinBox
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

        self.active_tab = "single"
        self.single_graph_active = True
        self.multi_graph_active = False

        self.multi_slots = []
        self._multi_active_slot_index = None

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
        self.setup_tabs(main_layout)
        self.setCentralWidget(central_widget)
        central_widget.setLayout(main_layout)

        self.send_timer = QTimer()
        self.send_timer.timeout.connect(lambda: logic.send_messages(self))

        self.multi_send_timer = QTimer()
        self.multi_send_timer.timeout.connect(lambda: logic.send_multi_messages(self))

        self.statemachine_timer = QTimer()
        self.statemachine_timer.timeout.connect(self.run_next_state)

    def setup_tabs(self, layout):
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.on_tab_changed)

        self.single_tab = QWidget()
        single_layout = QVBoxLayout()
        self.single_tab.setLayout(single_layout)
        self.setup_main_panel(single_layout)
        self.tabs.addTab(self.single_tab, "Single")

        self.multi_tab = QWidget()
        multi_layout = QVBoxLayout()
        self.multi_tab.setLayout(multi_layout)
        self.setup_multi_panel(multi_layout)
        self.tabs.addTab(self.multi_tab, "Multi")

        layout.addWidget(self.tabs)

    def on_tab_changed(self, index: int):
        label = self.tabs.tabText(index).lower()
        self.active_tab = "multi" if "multi" in label else "single"
        self.single_graph_active = self.active_tab == "single"
        self.multi_graph_active = self.active_tab == "multi"
        if self.single_graph_active:
            logic.update_graph(self)
        if self.multi_graph_active:
            self.refresh_multi_graphs()

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

    def setup_multi_panel(self, layout):
        top = QHBoxLayout()

        top.addWidget(QLabel("Period (ms):"))
        self.multi_period_ms = QSpinBox()
        self.multi_period_ms.setRange(1, 1000)
        self.multi_period_ms.setValue(10)
        self.multi_period_ms.valueChanged.connect(self._on_multi_period_changed)
        top.addWidget(self.multi_period_ms)

        self.multi_start_button = QPushButton("Start")
        self.multi_start_button.setCheckable(True)
        self.multi_start_button.toggled.connect(self._toggle_multi_sending)
        top.addWidget(self.multi_start_button)

        self.multi_add_slot_button = QPushButton("+ Add Slot")
        self.multi_add_slot_button.clicked.connect(self.add_multi_slot)
        top.addWidget(self.multi_add_slot_button)

        top.addStretch(1)
        layout.addLayout(top)

        self.multi_common_group = QGroupBox("Common (send once to all slot IDs)")
        common_layout = QVBoxLayout()
        common_row = QHBoxLayout()
        common_row.addWidget(QLabel("Message:"))
        self.multi_common_message_combo = QComboBox()
        self.multi_common_message_combo.setEditable(True)
        self.multi_common_message_combo.setInsertPolicy(QComboBox.NoInsert)
        self.multi_common_message_combo.activated[str].connect(
            lambda text: self._multi_common_set_message(text)
        )
        self.multi_common_message_combo.lineEdit().editingFinished.connect(
            self._multi_common_message_edit_finished
        )
        common_row.addWidget(self.multi_common_message_combo, 1)

        self.multi_common_send_button = QPushButton("Send")
        self.multi_common_send_button.clicked.connect(self._multi_common_send)
        common_row.addWidget(self.multi_common_send_button)
        common_layout.addLayout(common_row)

        self.multi_common_signals_container = QWidget()
        self.multi_common_signals_form = QFormLayout()
        self.multi_common_signals_container.setLayout(self.multi_common_signals_form)
        self.multi_common_signals_scroll = QScrollArea()
        self.multi_common_signals_scroll.setWidgetResizable(True)
        self.multi_common_signals_scroll.setWidget(self.multi_common_signals_container)
        self.multi_common_signals_scroll.setMinimumHeight(120)
        common_layout.addWidget(self.multi_common_signals_scroll)

        self.multi_common_group.setLayout(common_layout)
        layout.addWidget(self.multi_common_group)

        self.multi_cards_container = QWidget()
        self.multi_cards_layout = QHBoxLayout()
        self.multi_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.multi_cards_layout.setSpacing(10)
        self.multi_cards_container.setLayout(self.multi_cards_layout)

        self.multi_cards_scroll = QScrollArea()
        self.multi_cards_scroll.setWidgetResizable(True)
        self.multi_cards_scroll.setWidget(self.multi_cards_container)
        self.multi_cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.multi_cards_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(self.multi_cards_scroll)

        self._multi_message_names = []
        self._multi_message_name_set = set()

        self.add_multi_slot()

    def _setup_multi_browser(self):
        layout = QVBoxLayout()
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.multi_search = QLineEdit()
        self.multi_search.textChanged.connect(self.refresh_multi_message_list)
        search_row.addWidget(self.multi_search)
        layout.addLayout(search_row)

        self.multi_message_list = QListWidget()
        self.multi_message_list.itemClicked.connect(self._on_multi_message_chosen)
        layout.addWidget(self.multi_message_list)
        self.multi_browser.setLayout(layout)

    def _setup_multi_editor(self):
        layout = QVBoxLayout()
        header = QHBoxLayout()
        self.multi_back_button = QPushButton("← Back")
        self.multi_back_button.clicked.connect(
            lambda: self.multi_stack.setCurrentWidget(self.multi_browser)
        )
        header.addWidget(self.multi_back_button)
        self.multi_editor_title = QLabel("Message")
        header.addWidget(self.multi_editor_title)
        header.addStretch(1)
        layout.addLayout(header)

        self.multi_editor_form_container = QWidget()
        self.multi_editor_form_layout = QFormLayout()
        self.multi_editor_form_container.setLayout(self.multi_editor_form_layout)
        self.multi_editor_scroll = QScrollArea()
        self.multi_editor_scroll.setWidgetResizable(True)
        self.multi_editor_scroll.setWidget(self.multi_editor_form_container)
        layout.addWidget(self.multi_editor_scroll)

        self.multi_editor.setLayout(layout)

    def _on_multi_period_changed(self, value: int):
        self.multi_send_timer.setInterval(value)

    def _toggle_multi_sending(self, checked: bool):
        if checked:
            self.multi_start_button.setText("Stop")
            self.multi_send_timer.start(self.multi_period_ms.value())
        else:
            self.multi_start_button.setText("Start")
            self.multi_send_timer.stop()

    def _rebuild_multi_cards(self):
        while self.multi_cards_layout.count():
            item = self.multi_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        for idx, slot in enumerate(self.multi_slots):
            card = self._create_multi_slot_card(idx, slot)
            self.multi_cards_layout.addWidget(card)

        self.multi_cards_layout.addStretch(1)
        self.multi_add_slot_button.setEnabled(len(self.multi_slots) < 8)

        self.refresh_multi_message_list()
        if self.multi_graph_active:
            self.refresh_multi_graphs()

    def _create_multi_slot_card(self, slot_index: int, slot: dict):
        group = QGroupBox(f"Slot {slot_index + 1}")
        group.setMinimumWidth(360)
        group_layout = QVBoxLayout()

        plot = pg.PlotWidget()
        plot.setMinimumHeight(120)
        plot.setBackground("w")
        plot.showGrid(x=True, y=True, alpha=0.3)
        curve = plot.plot(pen=pg.mkPen(color=(0, 120, 215), width=1))
        group_layout.addWidget(plot)

        row = QHBoxLayout()
        enable_cb = QCheckBox("Enable")
        enable_cb.setChecked(bool(slot.get("enabled", True)))
        enable_cb.toggled.connect(
            lambda checked, i=slot_index: self._multi_set_slot_enabled(i, checked)
        )
        row.addWidget(enable_cb)

        row.addWidget(QLabel("ID:"))
        id_spin = QSpinBox()
        id_spin.setRange(1, 31)
        id_spin.setValue(int(slot.get("id", 1)))
        id_spin.valueChanged.connect(lambda v, i=slot_index: self._multi_set_slot_id(i, v))
        row.addWidget(id_spin)

        delete_btn = QPushButton("-")
        delete_btn.clicked.connect(lambda _, i=slot_index: self._multi_delete_slot(i))
        row.addWidget(delete_btn)
        row.addStretch(1)
        group_layout.addLayout(row)

        tx_row = QHBoxLayout()
        tx_row.addWidget(QLabel("TX Msg:"))
        tx_message_combo = QComboBox()
        tx_message_combo.setEditable(True)
        tx_message_combo.setInsertPolicy(QComboBox.NoInsert)
        tx_message_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tx_message_combo.activated[str].connect(
            lambda text, i=slot_index: self._multi_set_slot_tx_message(i, text)
        )
        tx_message_combo.lineEdit().editingFinished.connect(
            lambda i=slot_index: self._multi_tx_message_edit_finished(i)
        )
        tx_row.addWidget(tx_message_combo, 1)
        tx_apply_btn = QPushButton("Update")
        tx_apply_btn.clicked.connect(lambda _, i=slot_index: self._multi_apply_slot_tx(i))
        tx_row.addWidget(tx_apply_btn)
        group_layout.addLayout(tx_row)

        graph_row = QHBoxLayout()
        graph_row.addWidget(QLabel("Graph:"))
        graph_item_combo = QComboBox()
        graph_item_combo.setEditable(True)
        graph_item_combo.setInsertPolicy(QComboBox.NoInsert)
        graph_item_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        graph_item_combo.activated[str].connect(
            lambda text, i=slot_index: self._multi_set_slot_graph_item(i, text)
        )
        graph_item_combo.currentTextChanged.connect(
            lambda text, i=slot_index: self._multi_graph_item_text_changed(i, text)
        )
        graph_item_combo.lineEdit().editingFinished.connect(
            lambda i=slot_index: self._multi_graph_item_edit_finished(i)
        )
        graph_row.addWidget(graph_item_combo, 1)
        group_layout.addLayout(graph_row)

        signals_container = QWidget()
        signals_form = QFormLayout()
        signals_container.setLayout(signals_form)

        signals_scroll = QScrollArea()
        signals_scroll.setWidgetResizable(True)
        signals_scroll.setWidget(signals_container)
        signals_scroll.setMinimumHeight(160)
        group_layout.addWidget(signals_scroll)

        group.setLayout(group_layout)

        slot["_ui"] = {
            "group": group,
            "curve": curve,
            "tx_message_combo": tx_message_combo,
            "tx_apply_btn": tx_apply_btn,
            "graph_item_combo": graph_item_combo,
            "signals_form": signals_form,
            "signal_fields": {},
        }

        if self.db is not None and slot.get("tx_message_name"):
            self._multi_rebuild_slot_tx_ui(slot_index)
        if self.db is not None and slot.get("graph_message_name") and slot.get("graph_signal"):
            graph_item_combo.setCurrentText(
                f"{slot.get('graph_message_name')}.{slot.get('graph_signal')}"
            )

        return group

    def _multi_tx_message_edit_finished(self, slot_index: int):
        self._multi_validate_tx_message_combo(slot_index)

    def _multi_validate_tx_message_combo(self, slot_index: int):
        if not (0 <= slot_index < len(self.multi_slots)):
            return
        slot = self.multi_slots[slot_index]
        ui = slot.get("_ui", {})
        combo = ui.get("tx_message_combo")
        if combo is None:
            return

        text = combo.currentText().strip()
        if text == "":
            self._multi_set_slot_tx_message(slot_index, "")
            return

        if text in self._multi_message_name_set:
            self._multi_set_slot_tx_message(slot_index, text)
            return

        prev = slot.get("tx_message_name")
        combo.blockSignals(True)
        combo.setCurrentText(prev or "")
        combo.blockSignals(False)

    def _multi_set_slot_tx_message(self, slot_index: int, message_name: str):
        if not (0 <= slot_index < len(self.multi_slots)):
            return
        slot = self.multi_slots[slot_index]
        name = (message_name or "").strip()
        if name == "":
            slot["tx_message_name"] = None
        elif name in self._multi_message_name_set:
            slot["tx_message_name"] = name
        else:
            return
        slot["tx_pending_values"] = {}
        slot["tx_applied_values"] = {}
        slot["tx_ready"] = False
        self._multi_rebuild_slot_tx_ui(slot_index)

    def _multi_graph_item_edit_finished(self, slot_index: int):
        if not (0 <= slot_index < len(self.multi_slots)):
            return
        combo = self.multi_slots[slot_index].get("_ui", {}).get("graph_item_combo")
        if combo is None:
            return

        text = combo.currentText().strip()
        if text == "":
            self._multi_set_slot_graph_item(slot_index, "")
            return
        if text in getattr(self, "_multi_graph_items_set", set()):
            self._multi_set_slot_graph_item(slot_index, text)
            return

        prev_msg = self.multi_slots[slot_index].get("graph_message_name")
        prev_sig = self.multi_slots[slot_index].get("graph_signal")
        combo.blockSignals(True)
        combo.setCurrentText(f"{prev_msg}.{prev_sig}" if prev_msg and prev_sig else "")
        combo.blockSignals(False)

    def _multi_graph_item_text_changed(self, slot_index: int, text: str):
        if not (0 <= slot_index < len(self.multi_slots)):
            return
        value = (text or "").strip()
        if value == "":
            self._multi_set_slot_graph_item(slot_index, "")
            return
        if value in getattr(self, "_multi_graph_items_set", set()):
            self._multi_set_slot_graph_item(slot_index, value)

    def _multi_set_slot_graph_item(self, slot_index: int, item: str):
        if not (0 <= slot_index < len(self.multi_slots)):
            return
        slot = self.multi_slots[slot_index]
        text = (item or "").strip()
        if text == "":
            slot["graph_message_name"] = None
            slot["graph_signal"] = None
            slot["time"].clear()
            slot["values"].clear()
            slot["start"] = None
            return
        if "." not in text:
            return
        msg_name, sig_name = text.split(".", 1)
        if msg_name not in self._multi_message_name_set or self.db is None:
            return
        message = self.db.get_message_by_name(msg_name)
        if not message or sig_name not in [s.name for s in message.signals]:
            return

        slot["graph_message_name"] = msg_name
        slot["graph_signal"] = sig_name
        slot["time"].clear()
        slot["values"].clear()
        slot["start"] = None

    def _multi_rebuild_slot_tx_ui(self, slot_index: int):
        slot = self.multi_slots[slot_index]
        ui = slot.get("_ui", {})
        signals_form = ui.get("signals_form")
        if signals_form is None:
            return

        while signals_form.rowCount():
            signals_form.removeRow(0)
        ui["signal_fields"] = {}

        name = slot.get("tx_message_name")
        if self.db is None or not name:
            return

        message = self.db.get_message_by_name(name)
        if not message:
            return

        pending = slot.get("tx_pending_values", {})
        applied = slot.get("tx_applied_values", {})
        for sig in message.signals:
            if sig.name not in pending:
                pending[sig.name] = applied.get(sig.name, 0)
            field = QLineEdit(str(pending.get(sig.name, 0)))
            signals_form.addRow(sig.name, field)
            ui["signal_fields"][sig.name] = field

    def _multi_apply_slot_tx(self, slot_index: int):
        if not (0 <= slot_index < len(self.multi_slots)):
            return
        slot = self.multi_slots[slot_index]
        ui = slot.get("_ui", {})
        fields = ui.get("signal_fields", {})
        if not fields:
            return

        new_values = {}
        for name, field in fields.items():
            text = field.text().strip()
            if text == "":
                continue
            try:
                new_values[name] = float(text) if "." in text else int(text)
            except ValueError:
                logic.show_message(self, "Error", f"Invalid value: {name} = '{text}'")
                return

        slot["tx_applied_values"] = {**slot.get("tx_applied_values", {}), **new_values}
        slot["tx_pending_values"] = {**slot.get("tx_pending_values", {}), **new_values}
        slot["tx_ready"] = True

    def add_multi_slot(self):
        if len(self.multi_slots) >= 8:
            return

        slot = {
            "enabled": True,
            "id": 1,
            "tx_message_name": None,
            "tx_pending_values": {},
            "tx_applied_values": {},
            "tx_ready": False,
            "graph_message_name": None,
            "graph_signal": None,
            "time": deque(maxlen=500),
            "values": deque(maxlen=500),
            "start": None,
        }
        self.multi_slots.append(slot)
        self._rebuild_multi_cards()

    def _multi_set_slot_enabled(self, slot_index: int, enabled: bool):
        if 0 <= slot_index < len(self.multi_slots):
            self.multi_slots[slot_index]["enabled"] = enabled

    def _multi_set_slot_id(self, slot_index: int, value: int):
        if 0 <= slot_index < len(self.multi_slots):
            self.multi_slots[slot_index]["id"] = value

    def _multi_set_graph_signal(self, slot_index: int, text: str):
        self._multi_set_slot_graph_item(slot_index, text)

    def _multi_edit_slot(self, slot_index: int):
        return

    def _multi_delete_slot(self, slot_index: int):
        if not (0 <= slot_index < len(self.multi_slots)):
            return
        self.multi_slots.pop(slot_index)
        self._rebuild_multi_cards()

    def _multi_common_message_edit_finished(self):
        combo = getattr(self, "multi_common_message_combo", None)
        if combo is None:
            return
        text = combo.currentText().strip()
        if text == "":
            self._multi_common_set_message("")
            return
        if text in self._multi_message_name_set:
            self._multi_common_set_message(text)
            return
        combo.blockSignals(True)
        combo.setCurrentText("")
        combo.blockSignals(False)

    def _multi_common_set_message(self, message_name: str):
        name = (message_name or "").strip()

        while self.multi_common_signals_form.rowCount():
            self.multi_common_signals_form.removeRow(0)
        self._multi_common_signal_fields = {}

        if self.db is None or not name:
            self.multi_common_message_name = None
            return
        if name not in self._multi_message_name_set:
            return

        self.multi_common_message_name = name
        message = self.db.get_message_by_name(name)
        if not message:
            return

        for sig in message.signals:
            field = QLineEdit("0")
            self.multi_common_signals_form.addRow(sig.name, field)
            self._multi_common_signal_fields[sig.name] = field

    def _multi_common_send(self):
        if self.bus is None or self.db is None:
            return
        message_name = getattr(self, "multi_common_message_name", None)
        if not message_name:
            return

        values = {}
        for name, field in getattr(self, "_multi_common_signal_fields", {}).items():
            text = field.text().strip()
            if text == "":
                continue
            try:
                values[name] = float(text) if "." in text else int(text)
            except ValueError:
                logic.show_message(self, "Error", f"Invalid value: {name} = '{text}'")
                return

        target_ids = sorted(
            {int(s.get("id", 0)) & 0x1F for s in self.multi_slots if s.get("enabled", False)}
        )
        if not target_ids:
            return

        logic.send_common_message_to_ids(self, message_name, values, target_ids)

    def refresh_multi_message_list(self):
        self._multi_message_names = []
        self._multi_message_name_set = set()
        if self.db is not None:
            self._multi_message_names = [m.name for m in self.db.messages]
            self._multi_message_name_set = set(self._multi_message_names)

        graph_items = []
        if self.db is not None:
            for msg in self.db.messages:
                for sig in msg.signals:
                    graph_items.append(f"{msg.name}.{sig.name}")
        self._multi_graph_items = graph_items
        self._multi_graph_items_set = set(graph_items)

        for slot_index, slot in enumerate(self.multi_slots):
            ui = slot.get("_ui", {})
            tx_combo = ui.get("tx_message_combo")
            graph_combo = ui.get("graph_item_combo")
            if tx_combo is None or graph_combo is None:
                continue

            for combo, current, items in [
                (tx_combo, slot.get("tx_message_name") or "", self._multi_message_names),
                (
                    graph_combo,
                    (
                        f"{slot.get('graph_message_name')}.{slot.get('graph_signal')}"
                        if slot.get("graph_message_name") and slot.get("graph_signal")
                        else ""
                    ),
                    self._multi_graph_items,
                ),
            ]:
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("")
                combo.addItems(items)
                combo.setEnabled(bool(items))

                completer = QCompleter(items)
                completer.setCaseSensitivity(Qt.CaseInsensitive)
                completer.setFilterMode(Qt.MatchContains)
                combo.setCompleter(completer)

                if current and current in set(items):
                    combo.setCurrentText(current)
                else:
                    combo.setCurrentIndex(0)
                combo.blockSignals(False)

            if slot.get("tx_message_name"):
                self._multi_rebuild_slot_tx_ui(slot_index)

        if hasattr(self, "multi_common_message_combo"):
            self.multi_common_message_combo.blockSignals(True)
            self.multi_common_message_combo.clear()
            self.multi_common_message_combo.addItem("")
            self.multi_common_message_combo.addItems(self._multi_message_names)
            self.multi_common_message_combo.setEnabled(bool(self._multi_message_names))
            completer = QCompleter(self._multi_message_names)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            self.multi_common_message_combo.setCompleter(completer)
            self.multi_common_message_combo.blockSignals(False)

    def _on_multi_message_chosen(self, item: QListWidgetItem):
        if self._multi_active_slot_index is None:
            return
        if not (0 <= self._multi_active_slot_index < len(self.multi_slots)):
            return

        name = item.data(Qt.UserRole)
        slot = self.multi_slots[self._multi_active_slot_index]
        slot["message_name"] = name

        ui = slot["_ui"]
        ui["msg_label"].setText(f"Message: {name}")
        ui["sig_combo"].blockSignals(True)
        ui["sig_combo"].clear()
        message = self.db.get_message_by_name(name)
        if message:
            for sig in message.signals:
                ui["sig_combo"].addItem(sig.name)
            if ui["sig_combo"].count() > 0:
                ui["sig_combo"].setCurrentIndex(0)
                slot["graph_signal"] = ui["sig_combo"].currentText()
        ui["sig_combo"].blockSignals(False)

        self._populate_multi_editor_for_slot(self._multi_active_slot_index)
        self.multi_stack.setCurrentWidget(self.multi_editor)

    def _populate_multi_editor_for_slot(self, slot_index: int):
        slot = self.multi_slots[slot_index]
        name = slot.get("message_name")
        self.multi_editor_title.setText(name or "Message")

        while self.multi_editor_form_layout.rowCount():
            self.multi_editor_form_layout.removeRow(0)

        if self.db is None or not name:
            return

        message = self.db.get_message_by_name(name)
        if not message:
            return

        values = slot["signal_values"]
        for sig in message.signals:
            values.setdefault(sig.name, 0)

            field = QLineEdit()
            field.setText(str(values[sig.name]))
            field.textChanged.connect(
                lambda text, s=sig.name, i=slot_index: self._multi_set_signal_text(
                    i, s, text
                )
            )
            self.multi_editor_form_layout.addRow(sig.name, field)

    def _multi_set_signal_text(self, slot_index: int, signal_name: str, text: str):
        if not (0 <= slot_index < len(self.multi_slots)):
            return
        slot = self.multi_slots[slot_index]
        try:
            if "." in text:
                slot["signal_values"][signal_name] = float(text)
            else:
                slot["signal_values"][signal_name] = int(text)
        except ValueError:
            pass

    def refresh_multi_graphs(self):
        if not self.multi_graph_active:
            return
        for slot in self.multi_slots:
            ui = slot.get("_ui")
            if not ui:
                continue
            ui["curve"].setData(list(slot["time"]), list(slot["values"]))

    def multi_graph_on_rx(self, node_id: int, message_name: str, decoded_data: dict):
        for slot in self.multi_slots:
            if slot.get("graph_message_name") != message_name:
                continue
            if int(slot.get("id", 0)) != int(node_id):
                continue

            signal_name = slot.get("graph_signal")
            if not signal_name or signal_name not in decoded_data:
                continue

            now = time.monotonic()
            if slot["start"] is None:
                slot["start"] = now
            t = now - slot["start"]
            slot["time"].append(t)
            slot["values"].append(decoded_data[signal_name])

            if self.multi_graph_active:
                ui = slot.get("_ui")
                if ui:
                    ui["curve"].setData(list(slot["time"]), list(slot["values"]))

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
