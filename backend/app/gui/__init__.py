"""Provider-neutral GUI observation and controlled automation boundaries."""

from app.gui.automation import (
    GuiActionResult,
    GuiAutomationError,
    GuiAutomationProvider,
    GuiControl,
    WindowsUiaAutomationProvider,
    decode_control_id,
    encode_control_id,
)
from app.gui.provider import (
    GuiCapture,
    GuiObservationError,
    GuiObservationProvider,
    GuiWindow,
    SystemGuiObservationProvider,
)

__all__ = [
    "GuiActionResult",
    "GuiAutomationError",
    "GuiAutomationProvider",
    "GuiCapture",
    "GuiControl",
    "GuiObservationError",
    "GuiObservationProvider",
    "GuiWindow",
    "SystemGuiObservationProvider",
    "WindowsUiaAutomationProvider",
    "decode_control_id",
    "encode_control_id",
]
