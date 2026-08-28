"""OS GUI observation provider used by SceneWorks WP17.

WP17 is observation-only. The provider can enumerate and capture windows for a
specific managed process id; it exposes no focus, click, keyboard or generic
host-desktop control surface.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol

from app.gui.png import encode_rgb


class GuiObservationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GuiWindow:
    window_id: str
    pid: int
    title: str
    class_name: str
    visible: bool
    enabled: bool
    left: int
    top: int
    right: int
    bottom: int
    is_dialog: bool
    owner_window_id: str | None = None

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


@dataclass(frozen=True)
class GuiCapture:
    data: bytes
    mime_type: str
    width: int
    height: int
    capture_method: str
    occlusion_safe: bool


class GuiObservationProvider(Protocol):
    def list_windows(self, pid: int) -> list[GuiWindow]: ...

    def capture_window(self, window: GuiWindow) -> GuiCapture: ...


class SystemGuiObservationProvider:
    """Windows top-level-window observation using Win32 APIs only.

    Capture uses a GDI screen-region copy and therefore records what is actually
    visible at the selected PCS window rectangle. It deliberately reports
    ``occlusion_safe=False`` instead of pretending an obscured/minimized window
    was independently rendered.
    """

    def _require_windows(self) -> None:
        if sys.platform != "win32":
            raise GuiObservationError(
                "GUI observation is currently supported only on the Windows SceneWorks host"
            )

    @staticmethod
    def _parse_window_id(window_id: str) -> int:
        value = (window_id or "").strip().lower()
        if not value.startswith("w:"):
            raise GuiObservationError("invalid GUI window id")
        try:
            return int(value[2:], 16)
        except ValueError as exc:
            raise GuiObservationError("invalid GUI window id") from exc

    def list_windows(self, pid: int) -> list[GuiWindow]:
        self._require_windows()
        if pid <= 0:
            raise GuiObservationError("managed PCS PID is invalid")

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        windows: list[GuiWindow] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            owner_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
            if int(owner_pid.value) != pid:
                return True

            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width <= 0 or height <= 0:
                return True

            title_length = int(user32.GetWindowTextLengthW(hwnd))
            title_buffer = ctypes.create_unicode_buffer(max(1, title_length + 1))
            user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))

            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
            class_name = class_buffer.value

            raw_handle = int(hwnd)
            owner = int(user32.GetWindow(hwnd, 4) or 0)  # GW_OWNER
            windows.append(
                GuiWindow(
                    window_id=f"w:{raw_handle:x}",
                    pid=pid,
                    title=title_buffer.value,
                    class_name=class_name,
                    visible=bool(user32.IsWindowVisible(hwnd)),
                    enabled=bool(user32.IsWindowEnabled(hwnd)),
                    left=int(rect.left),
                    top=int(rect.top),
                    right=int(rect.right),
                    bottom=int(rect.bottom),
                    is_dialog=(class_name == "#32770" or owner != 0),
                    owner_window_id=(f"w:{owner:x}" if owner else None),
                )
            )
            return True

        enum_callback = callback_type(callback)
        if not user32.EnumWindows(enum_callback, 0):
            raise GuiObservationError("Windows window enumeration failed")
        return sorted(
            windows,
            key=lambda item: (not item.visible, item.is_dialog, -(item.width * item.height)),
        )

    def capture_window(self, window: GuiWindow) -> GuiCapture:
        self._require_windows()
        if window.width <= 0 or window.height <= 0:
            raise GuiObservationError("selected window has no capturable area")

        import ctypes
        from ctypes import wintypes

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class RGBQUAD(ctypes.Structure):
            _fields_ = [
                ("rgbBlue", ctypes.c_ubyte),
                ("rgbGreen", ctypes.c_ubyte),
                ("rgbRed", ctypes.c_ubyte),
                ("rgbReserved", ctypes.c_ubyte),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hwnd = self._parse_window_id(window.window_id)
        if not user32.IsWindow(wintypes.HWND(hwnd)):
            raise GuiObservationError("selected PCS window no longer exists")

        width, height = window.width, window.height
        screen_dc = user32.GetDC(None)
        if not screen_dc:
            raise GuiObservationError("could not acquire desktop device context")
        memory_dc = None
        bitmap = None
        previous = None
        try:
            memory_dc = gdi32.CreateCompatibleDC(screen_dc)
            bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
            if not memory_dc or not bitmap:
                raise GuiObservationError("could not allocate screenshot bitmap")
            previous = gdi32.SelectObject(memory_dc, bitmap)
            SRCCOPY = 0x00CC0020
            CAPTUREBLT = 0x40000000
            if not gdi32.BitBlt(
                memory_dc,
                0,
                0,
                width,
                height,
                screen_dc,
                window.left,
                window.top,
                SRCCOPY | CAPTUREBLT,
            ):
                raise GuiObservationError("Windows screenshot capture failed")

            row_bytes = ((width * 24 + 31) // 32) * 4
            raw_size = row_bytes * height
            pixels = (ctypes.c_ubyte * raw_size)()
            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height  # top-down rows
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 24
            info.bmiHeader.biCompression = 0  # BI_RGB
            info.bmiHeader.biSizeImage = raw_size
            if not gdi32.GetDIBits(
                memory_dc,
                bitmap,
                0,
                height,
                ctypes.byref(pixels),
                ctypes.byref(info),
                0,
            ):
                raise GuiObservationError("could not read screenshot pixels")

            raw = bytes(pixels)
            rgb = bytearray(width * height * 3)
            out = 0
            for row in range(height):
                row_start = row * row_bytes
                for col in range(width):
                    offset = row_start + col * 3
                    blue, green, red = raw[offset : offset + 3]
                    rgb[out : out + 3] = bytes((red, green, blue))
                    out += 3
            png = encode_rgb(width, height, bytes(rgb))
            return GuiCapture(
                data=png,
                mime_type="image/png",
                width=width,
                height=height,
                capture_method="visible_screen_region_gdi",
                occlusion_safe=False,
            )
        finally:
            if previous and memory_dc:
                gdi32.SelectObject(memory_dc, previous)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if memory_dc:
                gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(None, screen_dc)
