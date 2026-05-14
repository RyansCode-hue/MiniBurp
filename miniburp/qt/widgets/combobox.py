"""
QComboBox subclass that anchors its popup to the combo's current screen
position on every open.

Fusion on macOS exhibits a drift bug where successive showPopup() calls
don't re-resolve the combo's coordinate, so each open lands a few pixels
higher than the last. The fix is to override showPopup() and explicitly
move the popup window to mapToGlobal(rect().bottomLeft()) immediately
after Qt finishes placing it. On platforms where Fusion already does the
right thing this is a harmless no-op (we move the popup to where it
already is).
"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox


class MiniBurpComboBox(QComboBox):
    def showPopup(self) -> None:  # noqa: N802 - Qt override
        super().showPopup()
        # After Fusion has shown the popup, snap it under the combo so
        # the next open doesn't re-use a stale cached coordinate.
        view = self.view()
        if view is None:
            return
        popup = view.window()
        if popup is None:
            return
        target = self.mapToGlobal(self.rect().bottomLeft())
        # Preserve the popup's current width — Fusion calculates that
        # correctly even when the position is stale.
        popup.move(target)
