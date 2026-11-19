# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deterministic Windows fixture for visual OCR references.

Run this app, then run ``visual_ref_probe.py`` against its window title. The
canvas text is painted, not exposed as separate accessibility controls, so an
OCR visual reference is the intended way to click it.
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk
from datetime import datetime


class VisualFixture:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("CUA Visual Fixture")
        self.root.geometry("720x420")
        self.root.configure(bg="white")

        self.canvas = tk.Canvas(self.root, width=700, height=320, bg="white", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            120, 80, text="Export", font=("Segoe UI", 28, "bold"), fill="#1f2937", tags=("export",),
        )
        self.canvas.create_text(
            360, 240, text="Q3 Report", font=("Segoe UI", 22), fill="#374151", tags=("report",),
        )
        self.canvas.tag_bind("export", "<Button-1>", lambda event: self._clicked("Export", event))
        self.canvas.tag_bind("report", "<Button-1>", lambda event: self._clicked("Q3 Report", event))

        self.status = tk.Label(self.root, text="clicks: 0", font=("Segoe UI", 12), bg="white")
        self.status.pack(pady=4)
        self.log = os.path.join(tempfile.gettempdir(), "cua_visual_fixture.log")
        self.count = 0

    def _clicked(self, name: str, event) -> None:
        self.count += 1
        self.status.configure(text=f"clicks: {self.count} last={name} ({event.x},{event.y})")
        self.root.title(f"CUA Visual Fixture | clicks={self.count} | last={name}")
        with open(self.log, "a", encoding="utf-8") as stream:
            stream.write(
                f"{datetime.now().isoformat()} name={name} x={event.x} y={event.y}\n"
            )

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    VisualFixture().run()
