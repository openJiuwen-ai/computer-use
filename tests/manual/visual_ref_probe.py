# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Live probe for visual OCR references.

Prerequisites:
  - Windows 10 1903+ / Windows 11 or macOS 14+ with an interactive, unlocked desktop.
  - The platform driver built and installed.
  - Windows: an OCR language pack for the target text. macOS: no extra component.

Usage:
  python tests/manual/visual_fixture.py
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
                "\nNo visual elements were captured. Check the platform OCR support "
                "(Windows language pack or macOS Vision) and that the window is visible.",
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
