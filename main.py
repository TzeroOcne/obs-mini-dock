from dataclasses import dataclass
from enum import StrEnum
from logging import error
from typing import cast, override
from ahk import AHK
from windows_toasts import Toast, WindowsToaster
from PySide6 import QtCore, QtWidgets, QtGui
from fuzzyfinder import fuzzyfinder # pyright: ignore[reportAttributeAccessIssue, reportUnknownVariableType, reportMissingTypeStubs]
from ctypes import wintypes
import win32con
import win32gui
import sys
import obsws_python as obs # pyright: ignore[reportMissingTypeStubs]
import ctypes

# ANSI escape codes for yellow and bright yellow
YELLOW = "#ffff00"
GREEN = "#01ff00"

APP_BANNER = """
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│  ███████╗ █████╗ ███╗   ██╗ ██████╗██╗   ██╗████████╗██╗   ██╗██╗       │
│  ██╔════╝██╔══██╗████╗  ██║██╔════╝╚██╗ ██╔╝╚══██╔══╝╚██╗ ██╔╝██║       │
│  █████╗  ███████║██╔██╗ ██║██║      ╚████╔╝    ██║    ╚████╔╝ ██║       │
│  ██╔══╝  ██╔══██║██║╚██╗██║██║       ╚██╔╝     ██║     ╚██╔╝  ██║       │
│  ██║     ██║  ██║██║ ╚████║╚██████╗   ██║      ██║      ██║   ███████╗  │
│  ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝   ╚═╝      ╚═╝      ╚═╝   ╚══════╝  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
"""

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

# Load the DwmGetWindowAttribute function from dwmapi.dll
dwmapi = ctypes.windll.dwmapi

# Define constants
DWMWA_CLOAKED = 14

# Define the prototype for DwmGetWindowAttribute
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.POINTER(ctypes.c_int), wintypes.UINT]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

print(hex_to_ansi(YELLOW, "Initializing AHK..."))
ahk = AHK()
print(hex_to_ansi(GREEN, "AHK initialized"))

# Function to check if a window is cloaked
def is_window_cloaked(hwnd: int) -> bool:
    cloaked = ctypes.c_int(0)
    result:int = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    
    # Check if the result succeeded (SUCCEEDED result is 0)
    if result == 0:  # 0 means S_OK (success)
        return cloaked.value != 0
    return False

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

def focus_hwnd(hwnd: int):
    if win32gui.IsIconic(hwnd):
        _ = win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    else:
        _ = win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    win32gui.SetForegroundWindow(hwnd)

def frameless(self: QtWidgets.QWidget):
    self.setWindowFlag(QtCore.Qt.FramelessWindowHint) # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]

def stay_on_top(self: QtWidgets.QWidget):
    self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint) # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]

def tool(self: QtWidgets.QWidget):
    self.setWindowFlag(QtCore.Qt.Tool) # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]

def get_window_process_name(hwnd: int) -> str:
    return ahk.win_get_process_name(f"ahk_id {hwnd}") or ""

def is_window_in_taskbar(hwnd: int) -> bool:
    # Check if the window is visible
    if not win32gui.IsWindowVisible(hwnd):
        return False
    if not win32gui.IsWindow(hwnd):
        return False
    if is_window_cloaked(hwnd):
        return False

    # Get the extended window styles
    ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    # Check if it is a tool window (not in the taskbar)
    if ex_style & win32con.WS_EX_TOOLWINDOW:
        return False

    # Check if it has the app window style (should be in the taskbar)
    if ex_style & win32con.WS_EX_APPWINDOW:
        return True

    # Check parent and owner; if both are None, it might be in the taskbar
    parent = win32gui.GetParent(hwnd)
    owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER)

    return parent == 0 and owner == 0

def get_window_list():
    hwnd_list:list[tuple[int, str]] = []
    def register_window(hwnd: int, _:list[int]):
        if is_window_in_taskbar(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title != "":
                name = get_window_process_name(hwnd)
                hwnd_list.append((hwnd, f"{title} - {name}"))
    win32gui.EnumWindows(register_window, []) # pyright: ignore[reportUnknownArgumentType]
    return hwnd_list

class WindowFuzzyFinder(QtWidgets.QWidget):
    signal:QtCore.Signal = QtCore.Signal()
    title_list:list[tuple[int, str]] = []
    filtered_list:list[tuple[int, str]] = []

    def __init__(self):
        super().__init__()
        frameless(self)
        tool(self)
        stay_on_top(self)

        self.status_label:QtWidgets.QLabel = QtWidgets.QLabel("Status")
        exit_icon = QtGui.QIcon("exit.svg")
        self.exit_button:QtWidgets.QPushButton = QtWidgets.QPushButton(exit_icon, "")
        _ = self.exit_button.clicked.connect(self.hide_me)

        self.top_bar:QtWidgets.QHBoxLayout = QtWidgets.QHBoxLayout()
        self.top_bar.addWidget(self.status_label)
        self.top_bar.addWidget(self.exit_button)

        self.search_bar:QtWidgets.QLineEdit = QtWidgets.QLineEdit()

        self.window_list:QtWidgets.QListWidget = QtWidgets.QListWidget()
        self.window_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection) # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
        _ = self.window_list.itemClicked.connect(self.select_item)

        self.root_layout:QtWidgets.QVBoxLayout = QtWidgets.QVBoxLayout()
        self.root_layout.setContentsMargins(10, 10, 10, 10)
        self.root_layout.setSpacing(0)
        self.root_layout.addLayout(self.top_bar)
        self.root_layout.addWidget(self.search_bar)
        self.root_layout.addWidget(self.window_list)

        self.setLayout(self.root_layout)
        self.setWindowTitle("Windows Fuzzy Finder")

        # Timer for debouncing
        self.debounce_timer:QtCore.QTimer = QtCore.QTimer(self)
        self.debounce_timer.setSingleShot(True)  # Ensures it only triggers once
        _ = self.debounce_timer.timeout.connect(self.handle_search)

        # Connect textChanged signal to start the timer
        _ = self.search_bar.textChanged.connect(self.on_text_changed)

        self.setFixedSize(400, 400)

    @override
    def keyPressEvent(self, event: QtGui.QKeyEvent):
        """Handle key press events."""
        # Check if the pressed key is Esc
        if event.key() == QtCore.Qt.Key_Escape: # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
            self.hide_me()
        if event.key() == QtCore.Qt.Key_Return: # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
            self.select_item()
        else:
            super().keyPressEvent(event)

    def hide_me(self):
        self.window_list.clear()
        self.hide()

    def on_text_changed(self):
        # Restart the timer each time the text changes
        self.debounce_timer.start(300)

    def select_item(self, _:QtWidgets.QListWidgetItem|None = None):
        row = self.window_list.currentRow()
        if row < 0: row = 0
        hwnd, _1 = self.filtered_list[row]
        focus_hwnd(hwnd)
        self.hide_me()

    def handle_search(self):
        self.filter_list()
        self.show_title_list()

    def filter_list(self):
        text = self.search_bar.text().strip()
        if text != "":
            self.filtered_list = list(fuzzyfinder(text, self.title_list, accessor=lambda title: title[1])) # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
        else:
            self.filtered_list = self.title_list

    def move_widget_to_cursor_monitor_center(self):
        # Get the screen where the cursor is located
        cursor_pos = QtGui.QCursor.pos()
        current_screen = QtWidgets.QApplication.screenAt(cursor_pos)

        if current_screen is None: # pyright: ignore[reportUnnecessaryComparison]
            # Default to the primary screen if no screen found
            current_screen = QtWidgets.QApplication.primaryScreen()
        ratio = current_screen.devicePixelRatio()

        # Get target x and y positions
        screen_geometry = current_screen.geometry()
        center = screen_geometry.center()
        target_x = int((center.x() - 200) * ratio)
        target_y = int((center.y() - 200) * ratio)
        size = int(400 * ratio)

        winId = self.winId()
        win32gui.MoveWindow(winId, target_x, target_y, size, size, True)
        win32gui.MoveWindow(winId, target_x, target_y, size, size, True)

    def activate(self):
        self.raise_()
        self.show()
        self.search_bar.clear()
        self.search_bar.setFocus()
        self.activateWindow()

    def show_title_list(self):
        self.search_bar.setFocus()
        self.window_list.clear()
        self.window_list.addItems([title for _, title in self.filtered_list])
        self.search_bar.setFocus()

    def set_title_list(self):
        self.title_list = get_window_list()

class OBSWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        frameless(self)
        stay_on_top(self)
        tool(self)

        self.obs_client:obs.ReqClient = obs.ReqClient(host='localhost', password='haB8ihFOJ6Ig2wQ6')

        # Create buttons with specific colors
        self.record_button:QtWidgets.QPushButton = self.create_colored_button("", "grey")
        self.pause_button:QtWidgets.QPushButton = self.create_colored_button("", "yellow")
        self.replay_button:QtWidgets.QPushButton = self.create_colored_button("", "grey")
        self.save_button:QtWidgets.QPushButton = self.create_colored_button("", "yellow")
        self.exit_button:QtWidgets.QPushButton = self.create_colored_button("", "blue")
        self.hide_button:QtWidgets.QPushButton = self.create_colored_button("", "yellow")
        self.setup_button_color()

        _ = self.record_button.clicked.connect(self.toggle_record)
        _ = self.pause_button.clicked.connect(self.toggle_record_pause)
        _ = self.replay_button.clicked.connect(self.toggle_replay_buffer)
        _ = self.save_button.clicked.connect(self.save_replay_buffer)
        _ = self.exit_button.clicked.connect(self.quit_obs_widget)
        _ = self.hide_button.clicked.connect(self.hide)

        # Create a horizontal layout for buttons
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addWidget(self.record_button)
        button_layout.addWidget(self.pause_button)
        button_layout.addWidget(self.replay_button)
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.exit_button)
        button_layout.addWidget(self.hide_button)

        self.tray:QtWidgets.QSystemTrayIcon = QtWidgets.QSystemTrayIcon(QtGui.QIcon("icon.svg"))
        self.tray.setVisible(True)

        self.tray_menu:QtWidgets.QMenu = QtWidgets.QMenu(self)

        show_action = QtGui.QAction("Show", self)
        show_action.setIcon(QtGui.QIcon("control.svg"))
        _ = show_action.triggered.connect(self.show)
        self.tray_menu.addAction(show_action)
        quit_action = QtGui.QAction("Quit", self)
        quit_action.setIcon(QtGui.QIcon("exit.svg"))
        _ = quit_action.triggered.connect(self.quit_obs_widget)
        self.tray_menu.addAction(quit_action)

        self.tray.setContextMenu(self.tray_menu)
        self.tray.show()

        self.root_layout:QtWidgets.QVBoxLayout = QtWidgets.QVBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)
        self.root_layout.addLayout(button_layout)

        # Create layout and add widgets
        self.setLayout(self.root_layout)
        self.setFixedSize(120, 20)
        self.move_widget()

    def setup_button_color(self):
        record_status = cast(RecordStatusData, self.obs_client.get_record_status())
        replay_status = cast(ReplayStatusData, self.obs_client.get_replay_buffer_status())
        if record_status.output_active:
            self.change_record_button("red")
        if record_status.output_paused:
            self.change_record_button("yellow")
        if replay_status.output_active:
            self.change_replay_button("green")

    def quit_obs_widget(self):
        if cast(RecordStatusData, self.obs_client.get_record_status()).output_active:
            _ = self.obs_client.stop_record()
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

def main():
    global ahk
    print(hex_to_ansi(YELLOW, "Initializing event client..."))
    client = obs.EventClient(host='127.0.0.1', password='haB8ihFOJ6Ig2wQ6')
    print(hex_to_ansi(GREEN, "Client initialized"))

    print(hex_to_ansi(YELLOW, "Initializing toaster..."))
    toaster = WindowsToaster('OBS Python Toast')
    toast = Toast()
    print(hex_to_ansi(GREEN, "Toaster initialized"))

    app = QtWidgets.QApplication([])
    width, _ = cast(tuple[int,int], app.primaryScreen().size().toTuple())
    print(hex_to_ansi(YELLOW, "Initializing OBS widget..."))
    obs_widget = OBSWidget()
    obs_widget.move_widget(width // 2 - 60, 0)
    obs_widget.show()
    obs_widget.hide()
    print(hex_to_ansi(GREEN, "OBS widget initialized"))

    print(hex_to_ansi(YELLOW, "Initializing fuzzy finder..."))
    finder_widget = WindowFuzzyFinder()
    def toggle_fuzzy_window():
        finder_widget.set_title_list()
        finder_widget.move_widget_to_cursor_monitor_center()
        finder_widget.activate()
        finder_widget.filter_list()
        ahk.win_activate(f"ahk_id {finder_widget.winId()}")
        finder_widget.show_title_list()
    finder_widget.show()
    finder_widget.hide()
    _ = finder_widget.signal.connect(toggle_fuzzy_window)
    ahk.add_hotkey("^<#/", callback=finder_widget.signal.emit)
    ahk.start_hotkeys()
    print(hex_to_ansi(GREEN, "Fuzzy finder initialized"))

    print(hex_to_ansi(YELLOW, "Registering OBS callback..."))
    def show_toast(text: str):
        toaster.clear_toasts()
        toast.text_fields = [text]
        toaster.show_toast(toast)

    def on_record_state_changed(data: RecordStateData):
        if data.output_state == OutputState.STOPPED and data.output_path is not None:
            obs_widget.change_record_button("grey")
            show_toast(f"Recording saved at:\n{data.output_path}")
        if data.output_state == OutputState.STARTED:
            obs_widget.change_record_button("red")
            show_toast("Recording started")

    def on_replay_buffer_state_changed(data: ReplayStatusData):
        if data.output_active:
            obs_widget.change_replay_button("green")
        else:
            obs_widget.change_replay_button("grey")

    def on_replay_buffer_saved(data: ReplayBufferSavedData):
        show_toast(f"Replay buffer saved at:\n{data.saved_replay_path}")

    client.callback.register(on_record_state_changed) # pyright: ignore[reportUnknownMemberType]
    client.callback.register(on_replay_buffer_state_changed) # pyright: ignore[reportUnknownMemberType]
    client.callback.register(on_replay_buffer_saved) # pyright: ignore[reportUnknownMemberType]
    print(hex_to_ansi(GREEN, "OBS callback registered"))

    print(hex_to_ansi(GREEN, APP_BANNER))

    exit_code = app.exec()

    sys.exit(exit_code)

if __name__ == "__main__":
    try:
        main()
    except ConnectionRefusedError as e:
        error(hex_to_ansi(YELLOW, "Connection refused. Make sure OBS is running."))
