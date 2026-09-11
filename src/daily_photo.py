from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import winreg

import cv2
from PIL import Image, ImageDraw, ImageTk
import pystray

import create_timelapse


APP_NAME = "DailyPhoto"
MUTEX_NAME = "Local\\DailyPhotoCaptureMutex"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LEGACY_TASK_NAME = "DailyPhoto"
MAX_PREVIEW_WIDTH = 800
MAX_PREVIEW_HEIGHT = 450
PREVIEW_ASPECT_RATIO = MAX_PREVIEW_WIDTH / MAX_PREVIEW_HEIGHT
WINDOW_HORIZONTAL_RESERVE = 48
WINDOW_VERTICAL_RESERVE = 170


class Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("rcMonitor", Rect), ("rcWork", Rect), ("dwFlags", ctypes.c_uint)]


MONITOR_DEFAULTTONEAREST = 2
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WindowClass(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class SessionUnlockMonitor:
    """Receive Windows session-unlock notifications on a hidden window thread."""

    def __init__(self, on_unlock) -> None:
        self.on_unlock = on_unlock
        self.hwnd = None
        self.ready = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="DailyPhotoSessionMonitor", daemon=True
        )

    def start(self) -> None:
        self.thread.start()
        if not self.ready.wait(timeout=5):
            logging.error("Session unlock monitor did not start in time")

    def stop(self) -> None:
        if self.hwnd:
            ctypes.windll.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        wtsapi32 = ctypes.windll.wtsapi32
        class_name = f"DailyPhotoSessionMonitor_{os.getpid()}"
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        hinstance = kernel32.GetModuleHandleW(None)

        pointer_result = (
            ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
        )
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = pointer_result
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.UnregisterClassW.restype = wintypes.BOOL
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        wtsapi32.WTSRegisterSessionNotification.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
        ]
        wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL
        wtsapi32.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
        wtsapi32.WTSUnRegisterSessionNotification.restype = wintypes.BOOL

        @WNDPROC
        def window_proc(hwnd, message, wparam, lparam):
            if message == WM_WTSSESSION_CHANGE and wparam == WTS_SESSION_UNLOCK:
                logging.info("Windows session unlocked")
                self.on_unlock()
                return 0
            if message == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        window_class = WindowClass()
        window_class.lpfnWndProc = window_proc
        window_class.hInstance = hinstance
        window_class.lpszClassName = class_name
        atom = user32.RegisterClassW(ctypes.byref(window_class))
        if not atom:
            logging.error(
                "Unable to register session monitor window (error=%s)",
                kernel32.GetLastError(),
            )
            self.ready.set()
            return

        registered = False
        try:
            self.hwnd = user32.CreateWindowExW(
                0, class_name, class_name, 0, 0, 0, 0, 0, None, None, hinstance, None
            )
            if not self.hwnd:
                logging.error(
                    "Unable to create session monitor window (error=%s)",
                    kernel32.GetLastError(),
                )
                return
            registered = bool(
                wtsapi32.WTSRegisterSessionNotification(
                    self.hwnd, NOTIFY_FOR_THIS_SESSION
                )
            )
            if not registered:
                logging.error(
                    "Unable to register for session notifications (error=%s)",
                    kernel32.GetLastError(),
                )
                return
            logging.info("Session unlock monitor started")
            self.ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception:
            logging.exception("Session unlock monitor failed")
        finally:
            self.ready.set()
            if registered and self.hwnd:
                wtsapi32.WTSUnRegisterSessionNotification(self.hwnd)
            if self.hwnd and user32.IsWindow(self.hwnd):
                user32.DestroyWindow(self.hwnd)
            self.hwnd = None
            user32.UnregisterClassW(class_name, hinstance)


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


ROOT = app_root()
CONFIG_PATH = ROOT / "config.json"
LOG_PATH = ROOT / "daily_photo.log"


def setup_logging() -> None:
    handler = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def default_config() -> dict:
    return {
        "photos_directory": "photos",
        "capture_delay_seconds": 10,
        "confirmation_timeout_seconds": 15,
        "camera_index": 0,
        "mirror_image": True,
        "jpeg_quality": 95,
    }


def load_config() -> dict:
    config = default_config()
    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            config.update(loaded)
    except (OSError, ValueError):
        pass
    return config


def save_config(config: dict) -> None:
    temporary = CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, CONFIG_PATH)


def photos_root(config: dict) -> Path:
    configured = Path(str(config.get("photos_directory", "photos"))).expanduser()
    return configured if configured.is_absolute() else ROOT / configured


def today_has_photo(config: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    folder = photos_root(config) / now.strftime("%Y") / now.strftime("%m")
    return any(folder.glob(f"{now:%Y-%m-%d}_*.jpg")) if folder.exists() else False


def acquire_single_instance() -> object | None:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle or kernel32.GetLastError() == 183:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return handle


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        parts = [str(Path(sys.executable).resolve()), "--startup"]
    else:
        parts = [str(Path(sys.executable).resolve()), str(Path(__file__).resolve()), "--startup"]
    return subprocess.list2cmdline(parts)


def legacy_task_exists() -> bool:
    result = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", LEGACY_TASK_NAME],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    return result.returncode == 0


def remove_legacy_task() -> None:
    subprocess.run(
        ["schtasks.exe", "/Delete", "/TN", LEGACY_TASK_NAME, "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return legacy_task_exists()


def set_autostart(enabled: bool) -> None:
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
        remove_legacy_task()
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass
    remove_legacy_task()


def create_app_icon(size: int = 64) -> Image.Image:
    image = Image.new("RGBA", (size, size), (25, 28, 35, 255))
    draw = ImageDraw.Draw(image)
    scale = size / 64
    scaled = lambda box: tuple(int(value * scale) for value in box)
    draw.rounded_rectangle(scaled((8, 18, 56, 51)), radius=max(2, int(7 * scale)), fill=(47, 125, 255, 255))
    draw.rounded_rectangle(scaled((17, 12, 33, 21)), radius=max(1, int(3 * scale)), fill=(47, 125, 255, 255))
    draw.ellipse(scaled((22, 24, 46, 48)), fill=(245, 245, 247, 255))
    draw.ellipse(scaled((27, 29, 41, 43)), fill=(25, 28, 35, 255))
    return image


class CaptureWindow:
    def __init__(self, app: "DailyPhotoApp") -> None:
        self.app = app
        self.config = app.config
        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.title("DailyPhoto · 今日照片")
        self.window.configure(bg="#17191f")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.on_close)
        self.window.iconphoto(True, app.tk_icon)

        _, _, available_width, available_height = self.get_work_area()
        self.preview_width, self.preview_height = self.preview_size(available_width, available_height)
        self.preview = tk.Canvas(self.window, bg="#090a0d", width=self.preview_width, height=self.preview_height, bd=0, highlightthickness=0)
        self.preview.pack(padx=16, pady=(16, 8))
        self.preview_image = self.preview.create_image(self.preview_width // 2, self.preview_height // 2)
        self.status = tk.Label(self.window, text="正在打开摄像头…", fg="#f5f5f7", bg="#17191f", font=("Microsoft YaHei UI", 14))
        self.status.pack(pady=(4, 8))
        self.buttons = tk.Frame(self.window, bg="#17191f")
        self.buttons.pack(pady=(0, 16))
        self.save_button = tk.Button(self.buttons, text="保存", width=12, font=("Microsoft YaHei UI", 11), bg="#2f7dff", fg="white", activebackground="#2466d4", relief="flat", command=self.save)
        self.retake_button = tk.Button(self.buttons, text="重拍", width=12, font=("Microsoft YaHei UI", 11), relief="flat", command=self.start_countdown)

        self.capture = None
        self.current_frame = None
        self.frozen_frame = None
        self.tk_image = None
        self.countdown_started = 0.0
        self.confirm_started = 0.0
        self.mode = "opening"
        self.window.after(100, self.open_camera)
        self.center_window()

    def get_work_area(self) -> tuple[int, int, int, int]:
        try:
            user32 = ctypes.windll.user32
            pointer = Point(self.window.winfo_pointerx(), self.window.winfo_pointery())
            monitor = user32.MonitorFromPoint(pointer, MONITOR_DEFAULTTONEAREST)
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(MonitorInfo)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                work = info.rcWork
                return work.left, work.top, work.right - work.left, work.bottom - work.top
        except (AttributeError, OSError, tk.TclError):
            pass
        return 0, 0, self.window.winfo_screenwidth(), self.window.winfo_screenheight()

    @staticmethod
    def preview_size(available_width: int, available_height: int) -> tuple[int, int]:
        max_width = min(MAX_PREVIEW_WIDTH, max(1, available_width - WINDOW_HORIZONTAL_RESERVE))
        max_height = min(MAX_PREVIEW_HEIGHT, max(1, available_height - WINDOW_VERTICAL_RESERVE))
        width = min(max_width, int(max_height * PREVIEW_ASPECT_RATIO))
        height = max(1, int(width / PREVIEW_ASPECT_RATIO))
        return max(1, width), height

    def center_window(self) -> None:
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        left, top, available_width, available_height = self.get_work_area()
        x = left + max(0, (available_width - width) // 2)
        y = top + max(0, (available_height - height) // 2)
        position = f"+{x}+{y}"
        self.window.geometry(position)
        self.window.deiconify()
        self.window.lift()
        self.window.after_idle(lambda: self.window.geometry(position))
        self.window.attributes("-topmost", True)
        self.window.after(1500, lambda: self.window.attributes("-topmost", False))

    def open_camera(self) -> None:
        index = int(self.config["camera_index"])
        logging.info("Opening camera index %s", index)
        self.capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not self.capture.isOpened():
            logging.error("Unable to open camera index %s", index)
            self.capture.release()
            self.capture = cv2.VideoCapture(index)
        if not self.capture.isOpened():
            messagebox.showerror(APP_NAME, "无法打开摄像头。请确认摄像头未被其他程序独占，并检查 Windows 的摄像头隐私权限。", parent=self.window)
            self.close()
            return
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        logging.info("Camera opened")
        self.start_countdown()
        self.update_video()

    def start_countdown(self) -> None:
        self.mode = "countdown"
        self.frozen_frame = None
        self.countdown_started = time.monotonic()
        self.save_button.pack_forget()
        self.retake_button.pack_forget()
        self.status.configure(text="准备拍摄…")

    def update_video(self) -> None:
        if self.capture is None or not self.capture.isOpened() or not self.window.winfo_exists():
            return
        ok, frame = self.capture.read()
        if ok:
            if bool(self.config["mirror_image"]):
                frame = cv2.flip(frame, 1)
            self.current_frame = frame
        if self.mode == "countdown":
            elapsed = time.monotonic() - self.countdown_started
            remaining = max(0, int(float(self.config["capture_delay_seconds"]) - elapsed + 0.999))
            self.status.configure(text=f"{remaining} 秒后拍摄")
            if elapsed >= float(self.config["capture_delay_seconds"]):
                self.take_photo()
            elif self.current_frame is not None:
                self.show_frame(self.current_frame)
        elif self.mode == "confirm":
            elapsed = time.monotonic() - self.confirm_started
            remaining = max(0, int(float(self.config["confirmation_timeout_seconds"]) - elapsed + 0.999))
            self.status.configure(text=f"查看照片 · {remaining} 秒后自动保存")
            if elapsed >= float(self.config["confirmation_timeout_seconds"]):
                self.save()
        self.window.after(33, self.update_video)

    def take_photo(self) -> None:
        if self.current_frame is None:
            self.status.configure(text="正在等待摄像头画面…")
            self.countdown_started = time.monotonic()
            return
        self.frozen_frame = self.current_frame.copy()
        logging.info("Photo frame captured")
        self.mode = "confirm"
        self.confirm_started = time.monotonic()
        self.show_frame(self.frozen_frame)
        self.save_button.pack(side="left", padx=8)
        self.retake_button.pack(side="left", padx=8)

    def show_frame(self, frame) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.thumbnail((self.preview_width, self.preview_height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (self.preview_width, self.preview_height), "#090a0d")
        canvas.paste(image, ((self.preview_width - image.width) // 2, (self.preview_height - image.height) // 2))
        self.tk_image = ImageTk.PhotoImage(canvas)
        self.preview.itemconfigure(self.preview_image, image=self.tk_image)

    def save(self) -> None:
        if self.mode != "confirm" or self.frozen_frame is None:
            return
        self.mode = "saving"
        now = datetime.now()
        folder = photos_root(self.config) / now.strftime("%Y") / now.strftime("%m")
        destination = folder / f"{now:%Y-%m-%d_%H-%M-%S}.jpg"
        temporary = destination.with_suffix(".jpg.tmp")
        quality = max(1, min(100, int(self.config["jpeg_quality"])))
        ok, encoded = cv2.imencode(".jpg", self.frozen_frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        try:
            if not ok:
                raise OSError("JPEG 编码失败")
            folder.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(encoded.tobytes())
            os.replace(temporary, destination)
        except OSError as exc:
            self.mode = "confirm"
            messagebox.showerror(APP_NAME, f"保存照片失败：\n{exc}", parent=self.window)
            return
        self.status.configure(text=f"已保存：{destination.name}")
        logging.info("Photo saved to %s", destination)
        self.window.after(900, self.close)

    def on_close(self) -> None:
        if self.mode == "confirm" and self.frozen_frame is not None:
            self.save()
        else:
            self.close()

    def close(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        if self.window.winfo_exists():
            self.window.destroy()
        self.app.capture_window = None


class DailyPhotoApp:
    def __init__(self, config: dict, force_capture: bool) -> None:
        self.config = config
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.root.protocol("WM_DELETE_WINDOW", self.exit)
        self.icon_image = create_app_icon()
        self.tk_icon = ImageTk.PhotoImage(self.icon_image)
        self.root.iconphoto(True, self.tk_icon)
        self.capture_window: CaptureWindow | None = None
        self.settings_window: tk.Toplevel | None = None
        self.timelapse_window: tk.Toplevel | None = None
        self.timelapse_status: ttk.Label | None = None
        self.timelapse_button: ttk.Button | None = None
        self.timelapse_running = False
        self.actions: queue.Queue = queue.Queue()
        self.tray = pystray.Icon(
            APP_NAME,
            self.icon_image,
            "DailyPhoto · 每日照片",
            menu=pystray.Menu(
                pystray.MenuItem("立即拍照", self.enqueue_capture, default=True),
                pystray.MenuItem("生成延时影像…", self.enqueue_timelapse),
                pystray.MenuItem("参数设置…", self.enqueue_settings),
                pystray.MenuItem("打开照片目录", self.enqueue_open_folder),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("开机自启动", self.enqueue_toggle_autostart, checked=lambda item: is_autostart_enabled()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self.enqueue_exit),
            ),
        )
        self.tray.run_detached()
        self.session_monitor = SessionUnlockMonitor(self.enqueue_unlock)
        self.session_monitor.start()
        self.root.after(100, self.process_actions)
        if force_capture or not today_has_photo(self.config):
            self.root.after(250, self.open_capture)

    def enqueue_capture(self, icon=None, item=None) -> None:
        self.actions.put("capture")

    def enqueue_unlock(self) -> None:
        self.actions.put("unlock")

    def enqueue_settings(self, icon=None, item=None) -> None:
        self.actions.put("settings")

    def enqueue_timelapse(self, icon=None, item=None) -> None:
        self.actions.put("timelapse")

    def enqueue_open_folder(self, icon=None, item=None) -> None:
        self.actions.put("folder")

    def enqueue_toggle_autostart(self, icon=None, item=None) -> None:
        self.actions.put("autostart")

    def enqueue_exit(self, icon=None, item=None) -> None:
        self.actions.put("exit")

    def process_actions(self) -> None:
        try:
            while True:
                queued = self.actions.get_nowait()
                action, payload = queued if isinstance(queued, tuple) else (queued, None)
                if action == "capture":
                    self.open_capture()
                elif action == "unlock":
                    self.handle_session_unlock()
                elif action == "timelapse":
                    self.open_timelapse()
                elif action == "timelapse_progress":
                    self.update_timelapse_progress(payload)
                elif action == "timelapse_done":
                    self.finish_timelapse(payload)
                elif action == "settings":
                    self.open_settings()
                elif action == "folder":
                    self.open_photos_folder()
                elif action == "autostart":
                    self.toggle_autostart()
                elif action == "exit":
                    self.exit()
                    return
        except queue.Empty:
            pass
        self.root.after(100, self.process_actions)

    def handle_session_unlock(self) -> None:
        if today_has_photo(self.config):
            logging.info("Unlock capture skipped because today's photo already exists")
            return
        logging.info("Unlock triggered today's capture")
        self.open_capture()

    def open_capture(self) -> None:
        logging.info("Opening capture window")
        if self.capture_window is not None:
            self.capture_window.window.deiconify()
            self.capture_window.window.lift()
            return
        self.capture_window = CaptureWindow(self)

    def open_timelapse(self) -> None:
        if self.timelapse_window is not None and self.timelapse_window.winfo_exists():
            self.timelapse_window.deiconify()
            self.timelapse_window.lift()
            return
        window = tk.Toplevel(self.root)
        self.timelapse_window = window
        window.title("DailyPhoto · 生成延时影像")
        window.resizable(False, False)
        window.iconphoto(True, self.tk_icon)
        window.protocol("WM_DELETE_WINDOW", self.close_timelapse)
        frame = ttk.Frame(window, padding=18)
        frame.grid(sticky="nsew")

        crop = tk.StringVar(value="wide")
        make_mp4 = tk.BooleanVar(value=True)
        make_gif = tk.BooleanVar(value=False)
        timestamp = tk.BooleanVar(value=True)
        duration = tk.StringVar(value="250")

        ttk.Label(frame, text="构图范围").grid(row=0, column=0, sticky="nw", pady=5)
        crop_options = ttk.Frame(frame)
        crop_options.grid(row=0, column=1, sticky="w", padx=(14, 0), pady=5)
        ttk.Radiobutton(
            crop_options,
            text="完整头部与环境（16:9，推荐）",
            variable=crop,
            value="wide",
        ).pack(anchor="w")
        ttk.Radiobutton(
            crop_options, text="人脸特写（正方形）", variable=crop, value="face"
        ).pack(anchor="w", pady=(4, 0))

        ttk.Label(frame, text="输出格式").grid(row=1, column=0, sticky="nw", pady=5)
        format_options = ttk.Frame(frame)
        format_options.grid(row=1, column=1, sticky="w", padx=(14, 0), pady=5)
        ttk.Checkbutton(format_options, text="MP4", variable=make_mp4).pack(side="left")
        ttk.Checkbutton(format_options, text="GIF", variable=make_gif).pack(
            side="left", padx=(14, 0)
        )

        ttk.Label(frame, text="每帧时长").grid(row=2, column=0, sticky="w", pady=5)
        duration_row = ttk.Frame(frame)
        duration_row.grid(row=2, column=1, sticky="w", padx=(14, 0), pady=5)
        ttk.Entry(duration_row, textvariable=duration, width=8).pack(side="left")
        ttk.Label(duration_row, text="毫秒").pack(side="left", padx=(6, 0))
        ttk.Checkbutton(frame, text="在每帧左下角显示拍摄日期", variable=timestamp).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 5)
        )

        self.timelapse_status = ttk.Label(frame, text="输出到 generated 目录")
        self.timelapse_status.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 12))
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="关闭", command=self.close_timelapse).pack(side="right")

        def start() -> None:
            if not make_mp4.get() and not make_gif.get():
                messagebox.showerror(APP_NAME, "请至少选择一种输出格式。", parent=window)
                return
            try:
                duration_ms = int(duration.get())
                if not 20 <= duration_ms <= 60_000:
                    raise ValueError
            except ValueError:
                messagebox.showerror(APP_NAME, "每帧时长必须是 20–60000 毫秒。", parent=window)
                return
            output_format = "both" if make_mp4.get() and make_gif.get() else (
                "mp4" if make_mp4.get() else "gif"
            )
            crop_mode = crop.get()
            arguments = argparse.Namespace(
                input=photos_root(self.config),
                output=ROOT / "generated",
                format=output_format,
                gif_name="daily-photo.gif",
                video_name="daily-photo.mp4",
                crop=crop_mode,
                size=800 if crop_mode == "wide" else 600,
                duration=duration_ms,
                quality=92,
                confidence=0.75,
                background_blur=0,
                timestamp="date" if timestamp.get() else "none",
                strict=False,
                include_all=False,
                model=create_timelapse.resource_path(
                    Path("models") / create_timelapse.DEFAULT_MODEL_NAME
                ),
            )
            self.timelapse_running = True
            self.timelapse_button.configure(state="disabled")
            self.timelapse_status.configure(text="正在读取照片…")
            threading.Thread(
                target=self.generate_timelapse, args=(arguments,), daemon=True
            ).start()

        self.timelapse_button = ttk.Button(buttons, text="开始生成", command=start)
        self.timelapse_button.pack(side="right", padx=(0, 8))
        window.update_idletasks()
        x = max(0, (window.winfo_screenwidth() - window.winfo_reqwidth()) // 2)
        y = max(0, (window.winfo_screenheight() - window.winfo_reqheight()) // 2)
        window.geometry(f"+{x}+{y}")
        window.lift()

    def generate_timelapse(self, arguments: argparse.Namespace) -> None:
        def progress(current: int, total: int, relative: Path) -> None:
            self.actions.put(("timelapse_progress", (current, total, str(relative))))

        try:
            paths, results = create_timelapse.create_timelapse(arguments, progress=progress)
            skipped = sum(result.status != "aligned" for result in results)
            self.actions.put(("timelapse_done", (paths, len(results) - skipped, skipped, None)))
        except Exception as exc:
            logging.exception("Timelapse generation failed")
            self.actions.put(("timelapse_done", ([], 0, 0, str(exc))))

    def update_timelapse_progress(self, payload) -> None:
        if self.timelapse_status is None:
            return
        current, total, relative = payload
        self.timelapse_status.configure(text=f"正在对齐 {current}/{total}：{relative}")

    def finish_timelapse(self, payload) -> None:
        paths, aligned, skipped, error = payload
        self.timelapse_running = False
        if self.timelapse_button is not None:
            self.timelapse_button.configure(state="normal")
        parent = self.timelapse_window if self.timelapse_window is not None else self.root
        if error:
            if self.timelapse_status is not None:
                self.timelapse_status.configure(text="生成失败")
            messagebox.showerror(APP_NAME, f"生成延时影像失败：\n{error}", parent=parent)
            return
        if self.timelapse_status is not None:
            self.timelapse_status.configure(text=f"生成完成：成功 {aligned} 张，跳过 {skipped} 张")
        names = "、".join(path.name for path in paths)
        open_folder = messagebox.askyesno(
            APP_NAME,
            f"已生成 {names}\n成功 {aligned} 张，跳过 {skipped} 张。\n\n是否打开输出目录？",
            parent=parent,
        )
        if open_folder:
            os.startfile(ROOT / "generated")

    def close_timelapse(self) -> None:
        if self.timelapse_running:
            messagebox.showinfo(APP_NAME, "正在生成，请完成后再关闭此窗口。", parent=self.timelapse_window)
            return
        if self.timelapse_window is not None and self.timelapse_window.winfo_exists():
            self.timelapse_window.destroy()
        self.timelapse_window = None
        self.timelapse_status = None
        self.timelapse_button = None

    def open_settings(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            return
        window = tk.Toplevel(self.root)
        self.settings_window = window
        window.title("DailyPhoto · 参数设置")
        window.resizable(False, False)
        window.iconphoto(True, self.tk_icon)
        window.protocol("WM_DELETE_WINDOW", self.close_settings)
        frame = ttk.Frame(window, padding=18)
        frame.grid(sticky="nsew")
        values = {
            "photos_directory": tk.StringVar(value=str(self.config["photos_directory"])),
            "capture_delay_seconds": tk.StringVar(value=str(self.config["capture_delay_seconds"])),
            "confirmation_timeout_seconds": tk.StringVar(value=str(self.config["confirmation_timeout_seconds"])),
            "camera_index": tk.StringVar(value=str(self.config["camera_index"])),
            "jpeg_quality": tk.StringVar(value=str(self.config["jpeg_quality"])),
            "mirror_image": tk.BooleanVar(value=bool(self.config["mirror_image"])),
        }
        ttk.Label(frame, text="照片保存目录").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=values["photos_directory"], width=42).grid(row=0, column=1, padx=(12, 6), pady=5)

        def choose_folder() -> None:
            initial = photos_root({"photos_directory": values["photos_directory"].get()})
            selected = filedialog.askdirectory(parent=window, initialdir=initial)
            if selected:
                values["photos_directory"].set(selected)

        ttk.Button(frame, text="浏览…", command=choose_folder).grid(row=0, column=2, pady=5)
        fields = [("拍照倒计时（秒）", "capture_delay_seconds"), ("自动保存等待（秒）", "confirmation_timeout_seconds"), ("摄像头编号", "camera_index"), ("JPEG 质量（1–100）", "jpeg_quality")]
        for row, (label, key) in enumerate(fields, start=1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(frame, textvariable=values[key], width=12).grid(row=row, column=1, sticky="w", padx=12, pady=5)
        ttk.Checkbutton(frame, text="镜像预览和照片", variable=values["mirror_image"]).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 12))
        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="取消", command=self.close_settings).pack(side="right", padx=(8, 0))

        def apply_settings() -> None:
            try:
                directory = values["photos_directory"].get().strip()
                if not directory:
                    raise ValueError("照片保存目录不能为空")
                delay = float(values["capture_delay_seconds"].get())
                timeout = float(values["confirmation_timeout_seconds"].get())
                camera = int(values["camera_index"].get())
                quality = int(values["jpeg_quality"].get())
                if not 0 <= delay <= 3600:
                    raise ValueError("拍照倒计时必须在 0–3600 秒之间")
                if not 0 <= timeout <= 3600:
                    raise ValueError("自动保存等待必须在 0–3600 秒之间")
                if camera < 0:
                    raise ValueError("摄像头编号不能小于 0")
                if not 1 <= quality <= 100:
                    raise ValueError("JPEG 质量必须在 1–100 之间")
                updated = dict(self.config)
                updated.update(photos_directory=directory, capture_delay_seconds=delay, confirmation_timeout_seconds=timeout, camera_index=camera, jpeg_quality=quality, mirror_image=values["mirror_image"].get())
                save_config(updated)
            except (OSError, ValueError) as exc:
                messagebox.showerror(APP_NAME, f"无法保存设置：\n{exc}", parent=window)
                return
            self.config.clear()
            self.config.update(updated)
            self.close_settings()

        ttk.Button(buttons, text="保存", command=apply_settings).pack(side="right")
        window.update_idletasks()
        x = max(0, (window.winfo_screenwidth() - window.winfo_reqwidth()) // 2)
        y = max(0, (window.winfo_screenheight() - window.winfo_reqheight()) // 2)
        window.geometry(f"+{x}+{y}")
        window.lift()

    def close_settings(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.destroy()
        self.settings_window = None

    def open_photos_folder(self) -> None:
        folder = photos_root(self.config)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"无法打开照片目录：\n{exc}")

    def toggle_autostart(self) -> None:
        try:
            set_autostart(not is_autostart_enabled())
            self.tray.update_menu()
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"无法修改开机自启动设置：\n{exc}")

    def exit(self) -> None:
        if self.capture_window is not None:
            self.capture_window.close()
        self.close_settings()
        self.session_monitor.stop()
        self.tray.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="立即打开拍摄窗口")
    parser.add_argument("--startup", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    setup_logging()
    logging.info("Application starting (force_capture=%s, startup=%s)", args.force, args.startup)
    mutex = acquire_single_instance()
    if mutex is None:
        return 0
    try:
        DailyPhotoApp(load_config(), force_capture=args.force).run()
        return 0
    except Exception:
        logging.exception("Unhandled application error")
        try:
            messagebox.showerror(APP_NAME, f"程序遇到错误，详情已写入：\n{LOG_PATH}")
        except tk.TclError:
            pass
        return 1
    finally:
        ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
