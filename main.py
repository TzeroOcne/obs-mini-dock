from dataclasses import dataclass
from enum import StrEnum
from typing import cast
from windows_toasts import Toast, WindowsToaster
from PySide6 import QtCore, QtGui, QtWidgets
import sys
import obsws_python as obs
import obsws_python.util as obsutil
import tkinter as tk
# ANSI escape codes for yellow and bright yellow
YELLOW = "#ffff00"
GREEN = "#01ff00"

class OutputState(StrEnum):
    STARTING = "OBS_WEBSOCKET_OUTPUT_STARTING"
    STARTED = "OBS_WEBSOCKET_OUTPUT_STARTED"
    STOPPING = "OBS_WEBSOCKET_OUTPUT_STOPPING"
    STOPPED = "OBS_WEBSOCKET_OUTPUT_STOPPED"
    PAUSED = "OBS_WEBSOCKET_OUTPUT_PAUSED"
    RESUMED = "OBS_WEBSOCKET_OUTPUT_RESUMED"

@dataclass
class ReplayStatusData:
    output_active: bool

@dataclass
class RecordStatusData:
    output_active: bool
    output_paused: bool

@dataclass
class RecordStateData:
    output_active: bool 
    output_path: str|None 
    output_state: OutputState

@dataclass
class ReplayBufferSavedData:
    saved_replay_path: str

class MyWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        self.obs_client:obs.ReqClient = obs.ReqClient(host='localhost', password='haB8ihFOJ6Ig2wQ6')

        # Create buttons with specific colors
        self.record_button = self.create_colored_button("", "grey")
        self.pause_button = self.create_colored_button("", "yellow")
        self.replay_button = self.create_colored_button("", "grey")
        self.save_button = self.create_colored_button("", "yellow")
        self.exit_button = self.create_colored_button("", "blue")

        _ = self.record_button.clicked.connect(self.toggle_record)
        _ = self.pause_button.clicked.connect(self.toggle_record_pause)
        _ = self.replay_button.clicked.connect(self.toggle_replay_buffer)
        _ = self.save_button.clicked.connect(self.save_replay_buffer)
        _ = self.exit_button.clicked.connect(self.quit_obs_widget)

        # Create a horizontal layout for buttons
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addWidget(self.record_button)
        button_layout.addWidget(self.pause_button)
        button_layout.addWidget(self.replay_button)
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.exit_button)

        self.root_layout = QtWidgets.QVBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)
        self.root_layout.addLayout(button_layout)

        # Create layout and add widgets
        self.setLayout(self.root_layout)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint)
        self.setFixedSize(100, 20)
        self.move_widget()

    def quit_obs_widget(self):
        if cast(RecordStatusData, self.obs_client.get_record_status()).output_active:
            self.obs_client.stop_record()
        if cast(ReplayStatusData, self.obs_client.get_replay_buffer_status()).output_active:
            self.obs_client.stop_replay_buffer()
        QtWidgets.QApplication.quit()

    def toggle_record(self):
        if cast(RecordStateData, self.obs_client.get_record_status()).output_active:
            _ = self.obs_client.stop_record()
        else:
            self.obs_client.start_record()

    def toggle_record_pause(self):
        self.obs_client.toggle_record_pause()
        status = cast(RecordStatusData, self.obs_client.get_record_status())
        if status.output_paused:
            self.change_record_button("yellow")
        elif not status.output_paused and status.output_active:
            self.change_record_button("red")
        elif not status.output_paused and not status.output_active:
            self.change_record_button("grey")

    def toggle_replay_buffer(self):
        if cast(ReplayStatusData, self.obs_client.get_replay_buffer_status()).output_active:
            self.obs_client.stop_replay_buffer()
        else:
            self.obs_client.start_replay_buffer()

    def save_replay_buffer(self):
        record_status = cast(RecordStatusData, self.obs_client.get_record_status())
        replay_status = cast(ReplayStatusData, self.obs_client.get_replay_buffer_status())
        if replay_status.output_active and not record_status.output_paused:
            self.obs_client.save_replay_buffer()

    def create_colored_button(self, text: str, color: str):
        """Creates a button with the given text and background color."""
        button = QtWidgets.QPushButton(text)
        button.setAutoDefault(False)
        button.setFixedSize(20, 20)
        button.setStyleSheet(f"background-color: {color}; color: white; border-radius: 0px; border: none;")
        return button

    def move_widget(self, x:int=0, y:int=0):
        """Move the window to the top middle of the screen."""
        self.move(x, y)  # Move the window

    def change_record_button(self, color: str):
        self.record_button.setStyleSheet(f"background-color: {color}; color: white; border-radius: 0px;")

    def change_replay_button(self, color: str):
        self.replay_button.setStyleSheet(f"background-color: {color}; color: white; border-radius: 0px;")

def hex_to_ansi(hex_color: str, text: str) -> str:
    """
    Converts a hex color code to an ANSI escape sequence for True Color.
    Args:
        hex_color (str): The hex color code (e.g., "#FFD700").
        text (str): The text to colorize.
    Returns:
        str: The text wrapped in the ANSI color code.
    """
    # Remove the '#' if it exists
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[:2], 16), int(hex_color[2:4], 16), int(hex_color[4:], 16)
    return f"\033[38;2;{r};{g};{b}m{text}\033[0m"

def main():
    print(hex_to_ansi(YELLOW, "Initializing event client..."))
    client = obs.EventClient(host='127.0.0.1', password='haB8ihFOJ6Ig2wQ6')
    print(hex_to_ansi(GREEN, "Client initialized"))

    toaster = WindowsToaster('OBS Python Toast')
    toast = Toast()

    app = QtWidgets.QApplication([])
    width, _ = cast(tuple[int,int], app.primaryScreen().size().toTuple())
    widget = MyWidget()
    widget.move_widget(width // 2 - widget.width() // 2, 0)

    def show_toast(text: str):
        toaster.clear_toasts()
        toast.text_fields = [text]
        toaster.show_toast(toast)

    def on_record_state_changed(data: RecordStateData):
        if data.output_state == OutputState.STOPPED and data.output_path is not None:
            widget.change_record_button("grey")
            show_toast(f"Recording saved at:\n{data.output_path}")
        if data.output_state == OutputState.STARTED:
            widget.change_record_button("red")
            show_toast("Recording started")

    def on_replay_buffer_state_changed(data: ReplayStatusData):
        if data.output_active:
            widget.change_replay_button("green")
        else:
            widget.change_replay_button("grey")

    def on_replay_buffer_saved(data: ReplayBufferSavedData):
        show_toast(f"Replay buffer saved at:\n{data.saved_replay_path}")

    print(hex_to_ansi(YELLOW, "Registering callback..."))
    client.callback.register(on_record_state_changed)
    client.callback.register(on_replay_buffer_state_changed)
    client.callback.register(on_replay_buffer_saved)
    print(hex_to_ansi(GREEN, "Callback registered"))

    widget.resize(20, 100)
    widget.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
