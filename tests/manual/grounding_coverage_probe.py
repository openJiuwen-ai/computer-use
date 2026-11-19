# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Live probe for grounding candidate coverage and node geometry.

Answers two questions before any ax_ref fusion work starts:

1. How many nodes the accessibility tree exposes, how many carry no name, and
   how many of those look clickable by role alone.
2. Whether a node's rectangle is reachable from a retained observation, and
   under which field name.

Usage:
  python tests/manual/visual_fixture.py
  python tests/manual/grounding_coverage_probe.py --title "CUA Visual Fixture"
  python tests/manual/grounding_coverage_probe.py --title "Paint" --sample 40
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

from computer_use import Computer

# The native tree formatter localizes role names on non-English systems. Keep
# both English and Chinese names so a Chinese Windows session is not discarded.
ROW = re.compile(r"^(?P<indent>\s*)(?P<ref>\d+)\s+(?P<role>[^\s(]+)(?P<rest>.*)$")
_FIELD = re.compile(
    r"^(?:Value:|Description:|Secondary Actions:|Shortcut:|Minimum:|Maximum:|"
    r"Small step:|Large step:|Range value:|Position:|Elements \[|Region \[|Records|"
    r"Captured regions:|Text selection|Tree:|AX:|Image:|Screenshot:|Window \d|"
    r"Reported |Current window|Read notes|Collection|Document text)"
)

CLICKABLE_ROLES = {
    "button",
    "menuButton",
    "link",
    "tab",
    "checkbox",
    "radioButton",
    "comboBox",
    "menuItem",
    "row",
    "cell",
    "textField",
    "incrementor",
    "switch",
    "slider",
    "segmentedControl",
    "按钮",
    "菜单项",
    "菜单项目",
    "链接",
    "选项卡",
    "复选框",
    "单选按钮",
    "组合框",
    "编辑",
    "旋转图标",
    "滑块",
    "开关",
    "行",
    "单元格",
}

SILENT_ROLES = {
    "image",
    "staticText",
    "text",
    "group",
    "pane",
    "custom",
    "unknown",
    "图像",
    "文本",
    "容器",
    "窗口",
    "区域",
    "自定义",
    "未知",
    "分组",
    "窗格",
    "文档",
}


def _row_name(rest: str) -> str:
    """Extract the visible name from a formatted tree row's remainder."""
    rest = rest.strip()
    if rest.startswith("("):
        close = rest.find(")")
        rest = rest[close + 1 :].strip() if close != -1 else rest
    if not rest:
        return ""
    if rest.startswith('"'):
        end = rest.find('"', 1)
        return rest[1:end] if end != -1 else rest[1:]
    if _FIELD.match(rest):
        return ""
    return rest.split(",", 1)[0].strip()


def parse_rows(text: str):
    """Return (ref, role, name, depth) for each numbered tree row."""
    rows = []
    for line in text.splitlines():
        match = ROW.match(line)
        if match is None:
            continue
        indent = len(match.group("indent")) - len(match.group("indent").lstrip())
        rows.append((int(match.group("ref")), match.group("role"), _row_name(match.group("rest")), indent))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Probe accessibility candidate coverage on a live window")
    parser.add_argument("--title", required=True, help="Exact visible window title")
    parser.add_argument("--sample", type=int, default=20, help="How many unnamed clickable nodes to inspect")
    parser.add_argument("--details", type=int, default=2048, help="Characters of native detail per sampled node")
    args = parser.parse_args(argv)

    with Computer() as computer:
        app = computer.app(window_title=args.title)
        observation = app.read(view="tree", full=True, visual="never")
        rows = parse_rows(observation.text)

    if not rows:
        print("No numbered rows were parsed. Check the tree view and window title.", file=sys.stderr)
        return 2

    named = [row for row in rows if row[2].strip()]
    unnamed = [row for row in rows if not row[2].strip()]
    unnamed_clickable = [row for row in unnamed if row[1] in CLICKABLE_ROLES]
    unnamed_silent = [row for row in unnamed if row[1] in SILENT_ROLES]

    roles = Counter(row[1] for row in rows)
    unnamed_roles = Counter(row[1] for row in unnamed)

    print(f"window: {args.title!r}")
    print(f"parsed rows: {len(rows)}")
    print(f"  named: {len(named)}   unnamed: {len(unnamed)}")
    print(f"  unnamed and clickable by role: {len(unnamed_clickable)}")
    print(f"  unnamed and silent (image/text/group): {len(unnamed_silent)}")
    print(f"  unnamed share: {len(unnamed) / len(rows):.1%}")
    print()
    print("top roles:", ", ".join(f"{role}={count}" for role, count in roles.most_common(10)))
    print("top unnamed roles:", ", ".join(f"{role}={count}" for role, count in unnamed_roles.most_common(10)))
    print()

    sample = unnamed_clickable[: max(0, args.sample)]
    if not sample:
        print("No unnamed clickable nodes on this window, so role-based candidates would add nothing here.")
        return 0

    print(f"inspecting {len(sample)} unnamed clickable nodes for retained geometry:")
    found = 0
    for ref, role, _name, _depth in sample:
        details = observation.read_details(element=ref, length=args.details)
        text = details.text
        markers = [key for key in ("BoundingRectangle", "nativeBounds", "ClickablePoint", "IsOffscreen") if key in text]
        if markers:
            found += 1
        print(f"  ref {ref:>4} {role:<14} fields={markers or 'none'}")

    print()
    if found:
        print(f"{found}/{len(sample)} sampled nodes expose geometry through read_details; ax_ref fusion has its input.")
    else:
        print(
            "No sampled node exposed geometry. Node rectangles are not retained in the current read path, "
            "so a narrow native geometry query is required before fusion.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
