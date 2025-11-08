import os
import sys
import traceback
from typing import List, Optional

import requests
from PySide6 import QtCore, QtWidgets


DEFAULT_API_BASE = os.environ.get("TCBOT_API", "http://127.0.0.1:8000")


class APIClient:
    def __init__(self, base_url: str = DEFAULT_API_BASE):
        self.base_url = base_url.rstrip("/")

    def list_sessions(self) -> List[dict]:
        response = requests.get(f"{self.base_url}/sessions", timeout=5)
        response.raise_for_status()
        payload = response.json()
        return payload.get("sessions", [])

    def send_command(self, session_id: str, name: str, data: Optional[dict] = None):
        response = requests.post(
            f"{self.base_url}/sessions/{session_id}/commands",
            json={"name": name, "data": data or {}},
            timeout=5,
        )
        response.raise_for_status()
        return response.json()

    def send_bulk_command(self, name: str, data: Optional[dict] = None, session_ids: Optional[List[str]] = None):
        payload = {"command": {"name": name, "data": data or {}}}
        if session_ids:
            payload["session_ids"] = session_ids
        response = requests.post(
            f"{self.base_url}/sessions/commands/bulk",
            json=payload,
            timeout=5,
        )
        response.raise_for_status()
        return response.json()

class SessionTableModel(QtCore.QAbstractTableModel):
    HEADERS = ["Session ID", "Phone", "Status", "Last Post", "Groups", "Join State"]

    def __init__(self):
        super().__init__()
        self.sessions: List[dict] = []

    def update_sessions(self, sessions: List[dict]):
        self.beginResetModel()
        self.sessions = sessions
        self.endResetModel()

    def rowCount(self, parent=QtCore.QModelIndex()):
        return len(self.sessions)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return len(self.HEADERS)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid() or role not in {QtCore.Qt.DisplayRole, QtCore.Qt.ToolTipRole}:
            return None
        session = self.sessions[index.row()]
        column = index.column()
        if column == 0:
            return session.get("session_id")
        if column == 1:
            return session.get("phone_number")
        if column == 2:
            return "ON" if session.get("active") else "OFF"
        if column == 3:
            return session.get("last_successful_send_human")
        if column == 4:
            groups = session.get("groups", {})
            return f"{groups.get('active', 0)}/{groups.get('total', 0)}"
        if column == 5:
            join_state = session.get("join", {})
            if join_state.get("disabled"):
                return f"Disabled: {join_state.get('reason') or 'unknown'}"
            return "Ready"
        return None

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole:
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def session_at(self, row: int) -> Optional[dict]:
        if 0 <= row < len(self.sessions):
            return self.sessions[row]
        return None


class DashboardWindow(QtWidgets.QMainWindow):
    def __init__(self, api_base: str = DEFAULT_API_BASE):
        super().__init__()
        self.setWindowTitle("Telegram Bot Control Panel")
        self.resize(1200, 700)
        self.api_client = APIClient(api_base)
        self.model = SessionTableModel()
        self._build_ui()
        self._connect_signals()
        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_data)
        self.refresh_timer.start(5000)
        self.refresh_data()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QHBoxLayout(central)

        # Table section
        table_container = QtWidgets.QVBoxLayout()
        self.table = QtWidgets.QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        table_container.addWidget(self.table)

        table_button_row = QtWidgets.QHBoxLayout()
        refresh_btn = QtWidgets.QPushButton("Manual Refresh")
        refresh_btn.clicked.connect(self.refresh_data)
        self.start_all_btn = QtWidgets.QPushButton("Start All")
        self.stop_all_btn = QtWidgets.QPushButton("Stop All")
        table_button_row.addWidget(refresh_btn)
        table_button_row.addStretch()
        table_button_row.addWidget(self.start_all_btn)
        table_button_row.addWidget(self.stop_all_btn)
        table_container.addLayout(table_button_row)
        layout.addLayout(table_container, 2)

        # Detail / command panel
        panel = QtWidgets.QGroupBox("Session Controls")
        panel_layout = QtWidgets.QFormLayout(panel)

        self.selected_label = QtWidgets.QLabel("None selected")
        panel_layout.addRow("Selected Session:", self.selected_label)

        self.status_label = QtWidgets.QLabel("-")
        panel_layout.addRow("Status:", self.status_label)

        self.last_post_label = QtWidgets.QLabel("-")
        panel_layout.addRow("Last Post:", self.last_post_label)

        self.groups_label = QtWidgets.QLabel("-")
        panel_layout.addRow("Groups:", self.groups_label)

        # Action buttons
        button_row = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("Start")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.populate_btn = QtWidgets.QPushButton("Populate Groups")
        button_row.addWidget(self.start_btn)
        button_row.addWidget(self.stop_btn)
        button_row.addWidget(self.populate_btn)
        panel_layout.addRow(button_row)

        self.message_input = QtWidgets.QLineEdit()
        self.message_input.setPlaceholderText("New broadcast message…")
        self.message_btn = QtWidgets.QPushButton("Update Message")
        panel_layout.addRow(self.message_input, self.message_btn)

        self.invite_input = QtWidgets.QLineEdit()
        self.invite_input.setPlaceholderText("Invite link…")
        self.join_btn = QtWidgets.QPushButton("Join Group")
        panel_layout.addRow(self.invite_input, self.join_btn)

        time_layout = QtWidgets.QHBoxLayout()
        self.start_time_edit = QtWidgets.QTimeEdit(QtCore.QTime.fromString("10:00", "HH:mm"))
        self.end_time_edit = QtWidgets.QTimeEdit(QtCore.QTime.fromString("22:00", "HH:mm"))
        time_layout.addWidget(self.start_time_edit)
        time_layout.addWidget(self.end_time_edit)
        time_row = QtWidgets.QWidget()
        time_row.setLayout(time_layout)
        self.time_btn = QtWidgets.QPushButton("Set Time Window")
        panel_layout.addRow("Time Window:", time_row)
        panel_layout.addRow("", self.time_btn)

        limit_layout = QtWidgets.QHBoxLayout()
        self.limit_group_input = QtWidgets.QLineEdit()
        self.limit_group_input.setPlaceholderText("Group ID (optional)")
        self.limit_spin = QtWidgets.QSpinBox()
        self.limit_spin.setRange(30, 3600)
        self.limit_spin.setValue(180)
        limit_layout.addWidget(self.limit_group_input)
        limit_layout.addWidget(self.limit_spin)
        limit_row = QtWidgets.QWidget()
        limit_row.setLayout(limit_layout)
        self.limit_btn = QtWidgets.QPushButton("Set Limit")
        panel_layout.addRow("Rate Limit:", limit_row)
        panel_layout.addRow("", self.limit_btn)

        layout.addWidget(panel, 1)

    def _connect_signals(self):
        selection_model = self.table.selectionModel()
        selection_model.selectionChanged.connect(self._on_selection_changed)
        self.start_btn.clicked.connect(lambda: self._send_simple_command("start"))
        self.stop_btn.clicked.connect(lambda: self._send_simple_command("stop"))
        self.populate_btn.clicked.connect(lambda: self._send_simple_command("populate_groups"))
        self.message_btn.clicked.connect(self._send_message_update)
        self.join_btn.clicked.connect(self._send_join_command)
        self.time_btn.clicked.connect(self._send_time_update)
        self.limit_btn.clicked.connect(self._send_limit_update)
        self.start_all_btn.clicked.connect(lambda: self._send_bulk_command("start"))
        self.stop_all_btn.clicked.connect(lambda: self._send_bulk_command("stop"))

    def _current_session(self) -> Optional[dict]:
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return None
        return self.model.session_at(indexes[0].row())

    def _on_selection_changed(self, *_):
        session = self._current_session()
        if not session:
            self.selected_label.setText("None selected")
            self.status_label.setText("-")
            self.last_post_label.setText("-")
            self.groups_label.setText("-")
            return
        self.selected_label.setText(session.get("session_id", "-"))
        self.status_label.setText("ON" if session.get("active") else "OFF")
        self.last_post_label.setText(session.get("last_successful_send_human", "-"))
        groups = session.get("groups", {})
        self.groups_label.setText(f"{groups.get('active', 0)}/{groups.get('total', 0)} active")
        time_window = session.get("time_window", {})
        start = QtCore.QTime.fromString(time_window.get("start", "10:00"), "HH:mm")
        end = QtCore.QTime.fromString(time_window.get("end", "22:00"), "HH:mm")
        if start.isValid():
            self.start_time_edit.setTime(start)
        if end.isValid():
            self.end_time_edit.setTime(end)

    def refresh_data(self):
        try:
            sessions = self.api_client.list_sessions()
            self.model.update_sessions(sessions)
            if sessions and not self.table.selectionModel().hasSelection():
                index = self.model.index(0, 0)
                self.table.selectRow(0)
                self._on_selection_changed()
        except Exception as exc:  # pragma: no cover - UI feedback
            QtWidgets.QMessageBox.critical(self, "Refresh failed", str(exc))

    def _send_simple_command(self, name: str):
        session = self._current_session()
        if not session:
            QtWidgets.QMessageBox.warning(self, "No session", "Select a session first.")
            return
        try:
            self.api_client.send_command(session["session_id"], name)
            self.statusBar().showMessage(f"Command '{name}' queued.", 4000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Command failed", str(exc))

    def _send_message_update(self):
        session = self._current_session()
        if not session:
            QtWidgets.QMessageBox.warning(self, "No session", "Select a session first.")
            return
        text = self.message_input.text().strip()
        if not text:
            QtWidgets.QMessageBox.warning(self, "Missing text", "Enter a message first.")
            return
        try:
            self.api_client.send_command(session["session_id"], "set_message", {"text": text})
            self.statusBar().showMessage("Message update queued.", 4000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Command failed", str(exc))

    def _send_join_command(self):
        session = self._current_session()
        if not session:
            QtWidgets.QMessageBox.warning(self, "No session", "Select a session first.")
            return
        invite = self.invite_input.text().strip()
        if not invite:
            QtWidgets.QMessageBox.warning(self, "Missing invite", "Enter an invite link first.")
            return
        try:
            self.api_client.send_command(session["session_id"], "join", {"invite": invite})
            self.statusBar().showMessage("Join command queued.", 4000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Command failed", str(exc))

    def _send_time_update(self):
        session = self._current_session()
        if not session:
            QtWidgets.QMessageBox.warning(self, "No session", "Select a session first.")
            return
        start = self.start_time_edit.time().toString("HH:mm")
        end = self.end_time_edit.time().toString("HH:mm")
        try:
            self.api_client.send_command(session["session_id"], "set_time", {"start": start, "end": end})
            self.statusBar().showMessage("Time window update queued.", 4000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Command failed", str(exc))

    def _send_limit_update(self):
        session = self._current_session()
        if not session:
            QtWidgets.QMessageBox.warning(self, "No session", "Select a session first.")
            return
        limit_value = self.limit_spin.value()
        group_id_text = self.limit_group_input.text().strip()
        payload = {"limit": limit_value}
        if group_id_text:
            try:
                payload["group_id"] = int(group_id_text)
            except ValueError:
                QtWidgets.QMessageBox.warning(self, "Invalid group", "Group ID must be numeric.")
                return
        try:
            self.api_client.send_command(session["session_id"], "set_limit", payload)
            self.statusBar().showMessage("Limit update queued.", 4000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Command failed", str(exc))

    def _send_bulk_command(self, name: str):
        try:
            result = self.api_client.send_bulk_command(name)
            count = result.get("count", 0)
            self.statusBar().showMessage(f"Command '{name}' queued for {count} sessions.", 4000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Bulk command failed", str(exc))


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = DashboardWindow()
    window.show()
    try:
        sys.exit(app.exec())
    except Exception:  # pragma: no cover - graceful shutdown
        traceback.print_exc()


if __name__ == "__main__":
    main()
