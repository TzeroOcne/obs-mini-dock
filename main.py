from dataclasses import dataclass
from enum import StrEnum
from logging import error
from typing import cast
from obsws_python.events import threading
from ahk import AHK
import win32api
import win32con
import win32gui
from windows_toasts import Toast, WindowsToaster
from PySide6 import QtCore, QtWidgets, QtGui
from ctypes import wintypes
from fuzzyfinder import fuzzyfinder # pyright: ignore[reportAttributeAccessIssue]
import ctypes
import keyboard
import sys
import obsws_python as obs # pyright: ignore[reportMissingTypeStubs]

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

def focus_hwnd(hwnd: int):
    _ = win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    win32gui.SetForegroundWindow(hwnd)

def frameless(self: QtWidgets.QWidget):
    self.setWindowFlag(QtCore.Qt.FramelessWindowHint) # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]

def stay_on_top(self: QtWidgets.QWidget):
    self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint) # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]

def tool(self: QtWidgets.QWidget):
    self.setWindowFlag(QtCore.Qt.Tool) # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]

def get_monitor_info(monitor: int):
    # Get monitor info (position and size) using the monitor handle.
    monitor_info = ctypes.windll.user32.EnumDisplayMonitors(monitor, None)
    return monitor_info

def move_window(hwnd, monitor_index):
    # Get the monitor info
    monitor_info = get_monitor_info(monitor_index)
    
    # Assume monitors are arranged side by side.
    # You'll need to adjust based on your specific arrangement.
    if monitor_info:
        x, y, width, height = monitor_info[monitor_index]
        win32gui.MoveWindow(hwnd, x, y, width, height, True)

def is_window_in_taskbar(hwnd: int):
    # Check if the window is visible
    if not win32gui.IsWindowVisible(hwnd):
        return False

    # Get the extended window styles
    ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)

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
                hwnd_list.append((hwnd, title))
    win32gui.EnumWindows(register_window, [])
    return hwnd_list

class StatusWindow(QtWidgets.QWidget):
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
        self.window_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection) # pyright: ignore[reportAttributeAccessIssue]
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

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        """Handle key press events."""
        # Check if the pressed key is Esc
        if event.key() == QtCore.Qt.Key_Escape: # pyright: ignore[reportAttributeAccessIssue]
            self.hide_me()
        if event.key() == QtCore.Qt.Key_Return: # pyright: ignore[reportAttributeAccessIssue]
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
            self.filtered_list = list(fuzzyfinder(text, self.title_list, accessor=lambda title: title[1]))
        else:
            self.filtered_list = self.title_list

    def move_widget_to_cursor_monitor_center(self):
        # Get the screen where the cursor is located
        cursor_pos = QtGui.QCursor.pos()
        # print(cursor_pos)
        current_screen = QtWidgets.QApplication.screenAt(cursor_pos)
        # print(current_screen.geometry(), current_screen.virtualGeometry(), current_screen.size())

        if current_screen is None:
            # Default to the primary screen if no screen found
            current_screen = QtWidgets.QApplication.primaryScreen()

        ratio = current_screen.devicePixelRatio()

        # Get the cursor position
        cursor_pos = win32gui.GetCursorPos()

        # Find the monitor containing the cursor
        monitor_handle = ctypes.windll.user32.MonitorFromPoint(
            ctypes.wintypes.POINT(cursor_pos[0], cursor_pos[1]),
            win32con.MONITOR_DEFAULTTONEAREST
        )
        monitor_info = win32api.GetMonitorInfo(monitor_handle)
        left, top, right, bottom = monitor_info['Monitor']
        width = right - left
        height = bottom - top
        center_x = left + width // 2
        center_y = top + height // 2
        target_x = int(center_x - 200 * ratio)
        target_y = int(center_y - 200 * ratio)
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
        # for _, title in self.filtered_list:
        #     self.window_list.addItem(title)
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
        _ = show_action.triggered.connect(self.show)
        self.tray_menu.addAction(show_action)
        quit_action = QtGui.QAction("Quit", self)
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

# Define the callback function signature
WinEventProcType = ctypes.WINFUNCTYPE(
    None,  # Return type: void
    wintypes.HANDLE,  # hWinEventHook
    wintypes.DWORD,   # event
    wintypes.HWND,    # hwnd
    wintypes.LONG,    # idObject
    wintypes.LONG,    # idChild
    wintypes.DWORD,   # dwEventThread
    wintypes.DWORD    # dwmsEventTime
)

hook_thread_id:int|None = None  # Global variable to store the hook thread ID

def hook_thread():
    """Run the hook and message loop in a background thread."""
    global hook_thread_id
    ole32 = ctypes.windll.ole32
    user32 = ctypes.windll.user32

    ole32.CoInitialize(None)  # Initialize the COM library

    # Store the thread ID for posting WM_QUIT
    hook_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

    WINEVENT_OUTOFCONTEXT = 0x0000

    # event_callback = WinEventProcType(win_event_proc)

    # hook:int = user32.SetWinEventHook(
    #     win32con.EVENT_OBJECT_SHOW,  # Min event constant
    #     win32con.EVENT_OBJECT_HIDE,  # Max event constant
    #     0,                           # DLL handle (0 means not in a DLL)
    #     event_callback,              # Callback function
    #     # win_event_proc,              # Callback function
    #     0,                           # Process ID (0 for all processes)
    #     0,                           # Thread ID (0 for all threads)
    #     WINEVENT_OUTOFCONTEXT        # Hook flags
    # )
    #
    # if not hook:
    #     print("Failed to set hook")
    #     ole32.CoUninitialize()
    #     return
    #
    # try:
    #     print("Hook thread running... Listening for new windows.")
    #     msg = wintypes.MSG()
    #     while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
    #         user32.TranslateMessage(ctypes.byref(msg))
    #         user32.DispatchMessageW(ctypes.byref(msg))
    # finally:
    #     user32.UnhookWinEvent(hook)
    #     ole32.CoUninitialize()
    #     print("Hook thread exited.")

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

    print(hex_to_ansi(YELLOW, "Initializing AHK..."))
    ahk = AHK()
    print(hex_to_ansi(GREEN, "AHK initialized"))

    print(hex_to_ansi(YELLOW, "Initializing toaster..."))
    toaster = WindowsToaster('OBS Python Toast')
    toast = Toast()
    print(hex_to_ansi(GREEN, "Toaster initialized"))


    # Start the hook thread
    # print(hex_to_ansi(YELLOW, "Initializing hook thread..."))
    # hook_thread_instance = threading.Thread(target=hook_thread, daemon=True)
    # hook_thread_instance.start()
    # # Wait until the hook thread has initialized
    # while hook_thread_id is None:
    #     pass        
    # print(hex_to_ansi(GREEN, "Hook thread initialized"))

    # def kill_hook_thread():
    #     """Post WM_QUIT to the hook thread."""
    #     ctypes.windll.user32.PostThreadMessageW(hook_thread_id, win32con.WM_QUIT, 0, 0)

    app = QtWidgets.QApplication([])
    width, _ = cast(tuple[int,int], app.primaryScreen().size().toTuple())
    obs_widget = OBSWidget()
    obs_widget.move_widget(width // 2 - 60, 0)

    status_widget = StatusWindow()

    def toggle_fuzzy_window():
        status_widget.set_title_list()
        status_widget.move_widget_to_cursor_monitor_center()
        status_widget.activate()
        status_widget.filter_list()
        ahk.win_activate(f"ahk_id {status_widget.winId()}")
        status_widget.show_title_list()

    status_widget.show()
    status_widget.hide()

    ahk.add_hotkey("^<#/", callback=toggle_fuzzy_window)

    ahk.start_hotkeys()

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

    print(hex_to_ansi(YELLOW, "Registering callback..."))
    client.callback.register(on_record_state_changed) # pyright: ignore[reportUnknownMemberType]
    client.callback.register(on_replay_buffer_state_changed) # pyright: ignore[reportUnknownMemberType]
    client.callback.register(on_replay_buffer_saved) # pyright: ignore[reportUnknownMemberType]
    print(hex_to_ansi(GREEN, "Callback registered"))

    # obs_widget.resize(20, 120)
    obs_widget.show()
    obs_widget.hide()

    exit_code = app.exec()

    # kill_hook_thread()

    sys.exit(exit_code)

if __name__ == "__main__":
    try:
        main()
    except ConnectionRefusedError as e:
        error(hex_to_ansi(YELLOW, "Connection refused. Make sure OBS is running."))
