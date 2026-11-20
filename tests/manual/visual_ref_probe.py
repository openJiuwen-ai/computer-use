# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Live probe for visual OCR references.

Prerequisites:
  - Windows 10 1903+ / Windows 11 with an interactive, unlocked desktop.
  - The Windows driver built and installed.
  - ``COMPUTER_USE_WINDOWS_OCR=1`` in the probe process environment.
  - A Windows OCR language pack for the target text.

Usage:
  python tests/manual/visual_fixture.py
  set COMPUTER_USE_WINDOWS_OCR=1
  python tests/manual/visual_ref_probe.py --title "CUA Visual Fixture" --click Export
"""

from __future__ import annotations

import argparse
import sys

from computer_use import Computer, VisualRef


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Probe visual OCR references on a live window")
    parser.add_argument("--title", required=True, help="Exact visible window title")
    parser.add_argument("--click", help="Click the first visual element whose label contains this text")
    args = parser.parse_args(argv)

    with Computer() as computer:
        app = computer.app(window_title=args.title)
        observation = app.read(view="both", visual="always")
        print(observation.text)

        visual = getattr(observation, "visual", None)
        if visual is None or not visual.elements:
            print(
                "\nNo visual elements were captured. Check COMPUTER_USE_WINDOWS_OCR=1, "
                "the Windows OCR language pack, and that the window is visible.",
                file=sys.stderr,
            )
            return 2

        print("\nVisual references:")
        for element in visual.elements:
            print(f"  v{element.ref}: {element.label!r} rect={element.rect} source={element.source}")

        if args.click:
            target = next(
                (item for item in visual.elements if args.click.lower() in item.label.lower()),
                None,
            )
            if target is None:
                print(f"\nNo visual element contains {args.click!r}.", file=sys.stderr)
                return 3
            result = app.element(VisualRef(target.ref)).click()
            print(f"\nClicked v{target.ref}: status={result.status} detail={result.detail}")
            after = app.read(view="tree", visual="never")
            print(after.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
