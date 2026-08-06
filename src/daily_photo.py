from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

import cv2
from PIL import Image, ImageTk


APP_NAME = "DailyPhoto"
MUTEX_NAME = "Local\\DailyPhotoCaptureMutex"
MAX_PREVIEW_WIDTH = 800
MAX_PREVIEW_HEIGHT = 450
PREVIEW_ASPECT_RATIO = MAX_PREVIEW_WIDTH / MAX_PREVIEW_HEIGHT
WINDOW_HORIZONTAL_RESERVE = 48
WINDOW_VERTICAL_RESERVE = 170


class Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("rcMonitor", Rect),
        ("rcWork", Rect),
        ("dwFlags", ctypes.c_uint),
    ]


MONITOR_DEFAULTTONEAREST = 2


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


ROOT = app_root()
CONFIG_PATH = ROOT / "config.json"
PHOTOS_ROOT = ROOT / "photos"


def load_config() -> dict:
    defaults = {
        "capture_delay_seconds": 10,
        "confirmation_timeout_seconds": 15,
        "camera_index": 0,
        "mirror_image": True,
        "jpeg_quality": 95,
    }
    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        defaults.update(loaded)
    except (OSError, ValueError):
        pass
    return defaults


def today_has_photo(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    folder = PHOTOS_ROOT / now.strftime("%Y") / now.strftime("%m")
    return any(folder.glob(f"{now:%Y-%m-%d}_*.jpg")) if folder.exists() else False


def acquire_single_instance() -> object | None:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle or kernel32.GetLastError() == 183:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return handle


class DailyPhotoApp:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.root = tk.Tk()
        # Do not let Windows map the window at Tk's default top-left position.
        self.root.withdraw()
        self.root.title("DailyPhoto · 今日照片")
        self.root.configure(bg="#17191f")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        _, _, available_width, available_height = self.get_work_area()
        self.preview_width, self.preview_height = self.preview_size(
            available_width, available_height
        )

        # Canvas dimensions are always pixels. A Label without an image treats
        # width/height as text units, which made the initial window enormous.
        self.preview = tk.Canvas(
            self.root,
            bg="#090a0d",
            width=self.preview_width,
            height=self.preview_height,
            bd=0,
            highlightthickness=0,
        )
        self.preview.pack(padx=16, pady=(16, 8))
        self.preview_image = self.preview.create_image(
            self.preview_width // 2, self.preview_height // 2
        )

        self.status = tk.Label(
            self.root,
            text="正在打开摄像头…",
            fg="#f5f5f7",
            bg="#17191f",
            font=("Microsoft YaHei UI", 14),
        )
        self.status.pack(pady=(4, 8))

        self.buttons = tk.Frame(self.root, bg="#17191f")
        self.buttons.pack(pady=(0, 16))

        self.save_button = tk.Button(
            self.buttons,
            text="保存",
            width=12,
            font=("Microsoft YaHei UI", 11),
            bg="#2f7dff",
            fg="white",
            activebackground="#2466d4",
            relief="flat",
            command=self.save,
        )
        self.retake_button = tk.Button(
            self.buttons,
            text="重拍",
            width=12,
            font=("Microsoft YaHei UI", 11),
            relief="flat",
            command=self.start_countdown,
        )

        self.capture = None
        self.current_frame = None
        self.frozen_frame = None
        self.tk_image = None
        self.countdown_started = 0.0
        self.confirm_started = 0.0
        self.mode = "opening"
        self.root.after(100, self.open_camera)
        self.center_window()

    def get_work_area(self) -> tuple[int, int, int, int]:
        """Return the work area of the monitor containing the mouse pointer."""
        try:
            user32 = ctypes.windll.user32
            pointer = Point(self.root.winfo_pointerx(), self.root.winfo_pointery())
            monitor = user32.MonitorFromPoint(pointer, MONITOR_DEFAULTTONEAREST)
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(MonitorInfo)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                work = info.rcWork
                return (
                    work.left,
                    work.top,
                    work.right - work.left,
                    work.bottom - work.top,
                )
        except (AttributeError, OSError, tk.TclError):
            pass

        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    @staticmethod
    def preview_size(available_width: int, available_height: int) -> tuple[int, int]:
        """Fit a 16:9 preview inside the current monitor's usable work area."""
        max_width = min(
            MAX_PREVIEW_WIDTH,
            max(1, available_width - WINDOW_HORIZONTAL_RESERVE),
        )
        max_height = min(
            MAX_PREVIEW_HEIGHT,
            max(1, available_height - WINDOW_VERTICAL_RESERVE),
        )
        width = min(max_width, int(max_height * PREVIEW_ASPECT_RATIO))
        height = max(1, int(width / PREVIEW_ASPECT_RATIO))
        return max(1, width), height

    def center_window(self) -> None:
        self.root.update_idletasks()
        width = self.root.winfo_reqwidth()
        height = self.root.winfo_reqheight()

        left, top, available_width, available_height = self.get_work_area()

        x = left + max(0, (available_width - width) // 2)
        y = top + max(0, (available_height - height) // 2)
        position = f"+{x}+{y}"
        self.root.geometry(position)
        self.root.deiconify()
        self.root.lift()
        # Reapply after the first map because some Windows/Tk combinations
        # replace a pre-mainloop position with the default top-left placement.
        self.root.after_idle(lambda: self.root.geometry(position))
        self.root.attributes("-topmost", True)
        self.root.after(1500, lambda: self.root.attributes("-topmost", False))

    def open_camera(self) -> None:
        index = int(self.config["camera_index"])
        self.capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not self.capture.isOpened():
            self.capture.release()
            self.capture = cv2.VideoCapture(index)
        if not self.capture.isOpened():
            messagebox.showerror(
                APP_NAME,
                "无法打开摄像头。请确认摄像头未被其他程序独占，"
                "并检查 Windows 的摄像头隐私权限。",
            )
            self.root.destroy()
            return

        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
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
        if self.capture is None or not self.capture.isOpened():
            return

        ok, frame = self.capture.read()
        if ok:
            if bool(self.config["mirror_image"]):
                frame = cv2.flip(frame, 1)
            self.current_frame = frame

        if self.mode == "countdown":
            elapsed = time.monotonic() - self.countdown_started
            remaining = max(
                0, int(float(self.config["capture_delay_seconds"]) - elapsed + 0.999)
            )
            self.status.configure(text=f"{remaining} 秒后拍摄")
            if elapsed >= float(self.config["capture_delay_seconds"]):
                self.take_photo()
            elif self.current_frame is not None:
                self.show_frame(self.current_frame)
        elif self.mode == "confirm":
            elapsed = time.monotonic() - self.confirm_started
            remaining = max(
                0,
                int(
                    float(self.config["confirmation_timeout_seconds"])
                    - elapsed
                    + 0.999
                ),
            )
            self.status.configure(text=f"查看照片 · {remaining} 秒后自动保存")
            if elapsed >= float(self.config["confirmation_timeout_seconds"]):
                self.save()

        if self.root.winfo_exists():
            self.root.after(33, self.update_video)

    def take_photo(self) -> None:
        if self.current_frame is None:
            self.status.configure(text="正在等待摄像头画面…")
            self.countdown_started = time.monotonic()
            return
        self.frozen_frame = self.current_frame.copy()
        self.mode = "confirm"
        self.confirm_started = time.monotonic()
        self.show_frame(self.frozen_frame)
        self.save_button.pack(side="left", padx=8)
        self.retake_button.pack(side="left", padx=8)

    def show_frame(self, frame) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.thumbnail(
            (self.preview_width, self.preview_height), Image.Resampling.LANCZOS
        )
        canvas = Image.new(
            "RGB", (self.preview_width, self.preview_height), "#090a0d"
        )
        canvas.paste(
            image,
            (
                (self.preview_width - image.width) // 2,
                (self.preview_height - image.height) // 2,
            ),
        )
        self.tk_image = ImageTk.PhotoImage(canvas)
        self.preview.itemconfigure(self.preview_image, image=self.tk_image)

    def save(self) -> None:
        if self.mode != "confirm" or self.frozen_frame is None:
            return
        self.mode = "saving"
        now = datetime.now()
        folder = PHOTOS_ROOT / now.strftime("%Y") / now.strftime("%m")
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{now:%Y-%m-%d_%H-%M-%S}.jpg"
        temporary = destination.with_suffix(".jpg.tmp")

        quality = max(1, min(100, int(self.config["jpeg_quality"])))
        ok, encoded = cv2.imencode(
            ".jpg", self.frozen_frame, [cv2.IMWRITE_JPEG_QUALITY, quality]
        )
        try:
            if not ok:
                raise OSError("JPEG 编码失败")
            temporary.write_bytes(encoded.tobytes())
            os.replace(temporary, destination)
        except OSError as exc:
            self.mode = "confirm"
            messagebox.showerror(APP_NAME, f"保存照片失败：\n{exc}")
            return

        self.status.configure(text=f"已保存：{destination.name}")
        self.root.after(900, self.root.destroy)

    def on_close(self) -> None:
        if self.mode == "confirm" and self.frozen_frame is not None:
            self.save()
        else:
            self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            if self.capture is not None:
                self.capture.release()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force", action="store_true", help="即使今天已有照片也打开拍摄窗口"
    )
    args = parser.parse_args()

    mutex = acquire_single_instance()
    if mutex is None:
        return 0
    try:
        if not args.force and today_has_photo():
            return 0
        DailyPhotoApp(load_config()).run()
        return 0
    finally:
        ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
