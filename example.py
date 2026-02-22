import sys
import os
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QTextEdit, QFormLayout,
    QInputDialog, QMessageBox, QListWidget
)
from PySide6.QtCore import Qt
from safe_chunk_engine import SafeChunkEngine

class FullDemoGUI(QWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SafeChunkEngine Professional Demo")
        self.resize(1000, 750)

        # ------------------ Engine Init ------------------
        self.root_dir = "user_projects"
        self.engine = None
        
        # ------------------ UI State ------------------
        self.current_profile = {}
        self.current_settings = {}
        self.current_notes = {}

        # ------------------ Setup UI ------------------
        self.init_ui()
        
        # Try to open the last project or create a default one
        self.boot_engine("demo_project")

    def init_ui(self):
        main_layout = QVBoxLayout()

        # Project Management Header
        proj_header = QHBoxLayout()
        self.current_proj_label = QLabel("Project: None")
        self.current_proj_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #2c3e50;")
        
        self.btn_new_proj = QPushButton("Create New Project")
        self.btn_new_proj.clicked.connect(self.request_new_project)
        self.btn_new_proj.setStyleSheet("background-color: #4CAF50; color: white; padding: 5px;")
        
        proj_header.addWidget(self.current_proj_label)
        proj_header.addStretch()
        proj_header.addWidget(self.btn_new_proj)
        main_layout.addLayout(proj_header)

        # Form fields
        form_layout = QFormLayout()
        self.name_input = QLineEdit()
        self.theme_input = QLineEdit()
        self.notes_input = QTextEdit()
        form_layout.addRow("User Name:", self.name_input)
        form_layout.addRow("App Theme:", self.theme_input)
        form_layout.addRow("Notes Content:", self.notes_input)
        main_layout.addLayout(form_layout)

        # Button Groups
        button_layout = QHBoxLayout()
        self.save_profile_btn = QPushButton("Save Profile")
        self.save_settings_btn = QPushButton("Save Settings")
        self.save_notes_btn = QPushButton("Save Notes")
        self.force_sync_btn = QPushButton("Force Sync Now")
        self.checkpoint_btn = QPushButton("Create Checkpoint")
        self.delete_project_btn = QPushButton("Wipe Project")
        self.delete_project_btn.setStyleSheet("background-color: #f44336; color: white;")

        self.all_buttons = [
            self.save_profile_btn, self.save_settings_btn, self.save_notes_btn,
            self.force_sync_btn, self.checkpoint_btn, self.delete_project_btn
        ]

        for btn in self.all_buttons:
            button_layout.addWidget(btn)
        main_layout.addLayout(button_layout)

        # Output console
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("System logs appear here...")
        main_layout.addWidget(self.output)

        # Checkpoint list
        self.checkpoint_list = QListWidget()
        main_layout.addWidget(QLabel("Available Checkpoints (Double-click to restore):"))
        main_layout.addWidget(self.checkpoint_list)

        # Status
        self.status_label = QLabel("Status: Ready")
        self.status_label.setStyleSheet("font-weight: bold; color: blue;")
        main_layout.addWidget(self.status_label)

        self.setLayout(main_layout)

        # ------------------ Connect Signals ------------------
        self.save_profile_btn.clicked.connect(self.save_profile)
        self.save_settings_btn.clicked.connect(self.save_settings)
        self.save_notes_btn.clicked.connect(self.save_notes)
        self.force_sync_btn.clicked.connect(self.safe_force_sync)
        self.checkpoint_btn.clicked.connect(self.create_checkpoint)
        self.delete_project_btn.clicked.connect(self.delete_project)
        self.checkpoint_list.itemDoubleClicked.connect(self.restore_selected)

    # ------------------ Project Lifecycle ------------------

    def boot_engine(self, project_id):
        """Helper to switch or start the engine."""
        # Cleanup old engine if it exists
        if self.engine:
            self.engine.detach()

        self.engine, status = SafeChunkEngine.new(
            project_id, 
            base_dir=self.root_dir, 
            debounce_delay=1.5
        )

        if self.engine:
            self.setup_engine_callbacks()
            self.load_from_disk()
            self.update_checkpoint_list()
            self.current_proj_label.setText(f"Project: {self.engine.project_id}")
            self._toggle_controls(True)
            self.output.append(f"SYSTEM: Loaded project '{self.engine.project_id}'")
        else:
            self.update_status(f"Error: {status}")
            self._toggle_controls(False)

    def request_new_project(self):
        """Logic to create a brand new project folder via UI."""
        new_name, ok = QInputDialog.getText(self, "New Project", "Enter Project Name:")
        if ok and new_name.strip():
            self.boot_engine(new_name.strip())

    # ------------------ Engine Utilities ------------------

    def setup_engine_callbacks(self):
        if self.engine:
            self.engine.on_status = self.update_status
            self.engine.on_sync = self.on_sync_complete
            self.engine.on_fault = self.on_error

    def check_engine(self):
        if self.engine is None or not self.engine.is_active():
            QMessageBox.warning(self, "No Project", "No active project. Please create or open one.")
            return False
        return True

    def _toggle_controls(self, enabled: bool):
        for btn in self.all_buttons:
            btn.setEnabled(enabled)

    # ------------------ Actions ------------------

    def save_profile(self):
        if not self.check_engine(): return
        self.current_profile["name"] = self.name_input.text()
        self.engine.stage_update(self.current_profile, "user_profile")

    def save_settings(self):
        if not self.check_engine(): return
        self.current_settings["theme"] = self.theme_input.text()
        self.engine.stage_update(self.current_settings, "settings")

    def save_notes(self):
        if not self.check_engine(): return
        self.current_notes["text"] = self.notes_input.toPlainText()
        self.engine.stage_update(self.current_notes, "notes")

    def safe_force_sync(self):
        if not self.check_engine(): return
        self.engine.force_sync()

    def load_from_disk(self):
        if not self.check_engine(): return
        self.current_profile = self.engine.fetch_chunk("user_profile")
        self.current_settings = self.engine.fetch_chunk("settings")
        self.current_notes = self.engine.fetch_chunk("notes")

        self.name_input.setText(self.current_profile.get("name", ""))
        self.theme_input.setText(self.current_settings.get("theme", ""))
        self.notes_input.setPlainText(self.current_notes.get("text", ""))

    def create_checkpoint(self):
        if not self.check_engine(): return
        label, ok = QInputDialog.getText(self, "Checkpoint", "Enter label:")
        if ok:
            zip_name = self.engine.create_checkpoint(label=label)
            self.output.append(f"Checkpoint Created: {zip_name}")
            self.update_checkpoint_list()

    def update_checkpoint_list(self):
        self.checkpoint_list.clear()
        if not self.engine: return
        for cp in self.engine.list_checkpoints():
            self.checkpoint_list.addItem(cp["filename"])

    def restore_selected(self, item):
        if not self.check_engine(): return
        zip_name = item.text()
        confirm = QMessageBox.warning(
            self, "Restore", f"Restore {zip_name}? Current unsaved data will be lost.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm == QMessageBox.Yes:
            if self.engine.restore_checkpoint(zip_name):
                self.load_from_disk()
                QMessageBox.information(self, "Success", "Project Restored.")

    def delete_project(self):
        if not self.engine: return
        confirm = QMessageBox.critical(
            self, "Wipe Project", "Delete EVERYTHING? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm == QMessageBox.Yes:
            pid = self.engine.project_id
            if self.engine.delete_project(confirmed=True):
                self.engine = None
                self._toggle_controls(False)
                self.current_proj_label.setText("Project: None")
                self.checkpoint_list.clear()
                self.output.append(f"SYSTEM: Project '{pid}' deleted.")

    # ------------------ Callbacks ------------------

    def update_status(self, msg):
        self.status_label.setText(f"Status: {msg}")

    def on_sync_complete(self):
        self.output.append("Disk Sync: SUCCESS")

    def on_error(self, msg):
        QMessageBox.critical(self, "Engine Fault", msg)

    def closeEvent(self, event):
        if self.engine:
            self.engine.detach()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FullDemoGUI()
    window.show()
    sys.exit(app.exec())
