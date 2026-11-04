# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Platform field names mapped to the shared observation vocabulary."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

ROLES = frozenset(
    """application window dialog alert sheet popover group split_group scroll_area toolbar separator
button toggle_button checkbox radio_button switch menu_button popup_button combo_box text_field text_area
slider spinner scrollbar progress_indicator static_text heading image link document web_area list list_item
tree tree_item row column cell table grid row_header column_header tab_group tab menu_bar menu_bar_item menu
menu_item unknown header_item value_indicator browser ruler handle color_well date_field time_field disclosure_triangle
grow_area dock_item menu_button increment_arrow decrement_arrow increment_page decrement_page""".split()
)

MAC_ROLES = {
    "AXCheckBox": "checkbox",
    "AXRadioButton": "radio_button",
    "AXPopUpButton": "popup_button",
    "AXTextField": "text_field",
    "AXTextArea": "text_area",
    "AXStaticText": "static_text",
    "AXScrollBar": "scrollbar",
    "AXOutline": "tree",
    "AXTabGroup": "tab_group",
    "AXIncrementor": "spinner",
    "AXSplitter": "separator",
    "AXSystemWide": "application",
    "AXMenuBarItem": "menu_bar_item",
}
WINDOWS_ROLES = {
    "Edit": "text_field",
    "Text": "static_text",
    "Pane": "group",
    "Group": "group",
    "DataGrid": "grid",
    "Table": "table",
    "DataItem": "row",
    "ListItem": "list_item",
    "Tree": "tree",
    "TreeItem": "tree_item",
    "Tab": "tab_group",
    "TabItem": "tab",
    "Hyperlink": "link",
    "SplitButton": "menu_button",
    "Spinner": "spinner",
    "ProgressBar": "progress_indicator",
    "ToolBar": "toolbar",
    "Separator": "separator",
    "CheckBox": "checkbox",
    "RadioButton": "radio_button",
    "ScrollBar": "scrollbar",
    "Header": "group",
    "HeaderItem": "header_item",
    "Custom": "unknown",
    "Thumb": "handle",
}
LINUX_ROLES = {
    "frame": "window",
    "window": "window",
    "dialog": "dialog",
    "alert": "alert",
    "file chooser": "dialog",
    "font chooser": "dialog",
    "color chooser": "dialog",
    "application": "application",
    "push button": "button",
    "button": "button",
    "push button menu": "menu_button",
    "toggle button": "toggle_button",
    "check box": "checkbox",
    "radio button": "radio_button",
    "text": "text_field",
    "entry": "text_field",
    "password text": "text_field",
    "label": "static_text",
    "static": "static_text",
    "heading": "heading",
    "document web": "web_area",
    "document text": "document",
    "document spreadsheet": "document",
    "document frame": "document",
    "document presentation": "document",
    "table": "table",
    "table cell": "cell",
    "table row": "row",
    "table column header": "column_header",
    "column header": "column_header",
    "table row header": "row_header",
    "row header": "row_header",
    "list": "list",
    "list box": "list",
    "list item": "list_item",
    "tree": "tree",
    "tree table": "tree",
    "tree item": "tree_item",
    "scroll pane": "scroll_area",
    "scroll bar": "scrollbar",
    "slider": "slider",
    "spin button": "spinner",
    "combo box": "combo_box",
    "menu": "menu",
    "popup menu": "menu",
    "menu bar": "menu_bar",
    "menu item": "menu_item",
    "check menu item": "menu_item",
    "radio menu item": "menu_item",
    "tool bar": "toolbar",
    "page tab list": "tab_group",
    "page tab": "tab",
    "separator": "separator",
    "image": "image",
    "icon": "image",
    "link": "link",
    "progress bar": "progress_indicator",
    "panel": "group",
    "section": "group",
    "filler": "group",
    "viewport": "group",
    "embedded": "group",
    "grouping": "group",
}

ROLE_FIELDS = {"macos": "AXRole", "windows": "ControlType", "linux": "role"}
ROLE_DETAILS = {
    "AXCloseButton": "close",
    "AXMinimizeButton": "minimize",
    "AXZoomButton": "zoom",
    "AXFullScreenButton": "fullscreen",
    "AXSearchField": "search",
    "AXSecureTextField": "password",
    "AXStandardWindow": "standard",
    "AXFloatingWindow": "floating",
    "AXSystemDialog": "system",
    "file chooser": "file_chooser",
    "font chooser": "font_chooser",
    "color chooser": "color_chooser",
    "password text": "password",
    "document spreadsheet": "spreadsheet",
    "document presentation": "presentation",
    "check menu item": "check",
    "radio menu item": "radio",
    "popup menu": "popup",
}


def role_name(backend: str, value: str) -> str:
    """Normalize a declared role while preserving unknown roles separately."""
    if backend == "linux":
        return LINUX_ROLES.get(value.casefold(), "unknown")
    name = value.removeprefix("AX") if backend == "macos" else value.removeprefix("ControlType.")
    explicit = MAC_ROLES.get(value) if backend == "macos" else WINDOWS_ROLES.get(name)
    if explicit:
        return explicit
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return normalized if normalized in ROLES else "unknown"


TEXT_FIELDS = {
    "macos": {
        "AXTitle": "name",
        "AXDescription": "description",
        "AXHelp": "help",
        "AXIdentifier": "identifier",
        "AXPlaceholderValue": "placeholder",
        "AXURL": "url",
        "AXRoleDescription": "role_description",
    },
    "windows": {
        "Name": "name",
        "FullDescription": "description",
        "HelpText": "help",
        "AutomationId": "identifier",
        "LocalizedControlType": "role_description",
    },
    "linux": {"name": "name", "description": "description", "accessibleId": "identifier"},
}


def _iter_key(_source_items: Iterator[Any]) -> Iterator[Any]:
    for key in _source_items:
        yield (key, key)


STATE_FIELDS = {
    "macos": {
        "AXEnabled": "enabled",
        "AXFocused": "focused",
        "AXSelected": "selected",
        "AXEditable": "editable",
        "AXElementBusy": "busy",
        "AXModal": "modal",
        "AXMain": "main_window",
        "AXHasPopup": "has_popup",
    },
    "windows": {
        "IsEnabled": "enabled",
        "HasKeyboardFocus": "focused",
        "IsKeyboardFocusable": "focusable",
        "IsPassword": "protected",
        "IsOffscreen": "offscreen",
        "SelectionItem.IsSelected": "selected",
        "Window.IsModal": "modal",
    },
    "linux": dict(
        _iter_key(
            iter(
                (
                    "enabled",
                    "focused",
                    "focusable",
                    "selectable",
                    "selected",
                    "editable",
                    "busy",
                    "modal",
                    "active",
                    "visible",
                    "showing",
                )
            )
        )
    ),
}
STRUCTURE_FIELDS = {
    "macos": {
        "AXDisclosureLevel": "level",
        "AXIndex": "index",
        "AXRowCount": "row_count",
        "AXColumnCount": "column_count",
    },
    "windows": {
        "Grid.RowCount": "row_count",
        "Grid.ColumnCount": "column_count",
        "GridItem.Row": "row_index",
        "GridItem.Column": "column_index",
        "GridItem.RowSpan": "row_span",
        "GridItem.ColumnSpan": "column_span",
        "Selection.CanSelectMultiple": "can_select_multiple",
        "Selection.IsSelectionRequired": "selection_required",
        "PositionInSet": "index",
        "SizeOfSet": "set_size",
        "Level": "level",
        "HeadingLevel": "heading_level",
        "LocalizedLandmarkType": "landmark_description",
    },
    "linux": {
        "Table.nRows": "row_count",
        "Table.nColumns": "column_count",
        "Selection.nSelectedChildren": "selected_count",
    },
}
NUMBER_FIELDS = {
    "macos": {
        "AXMinValue": "minimum",
        "AXMaxValue": "maximum",
        "AXValueIncrement": "small_step",
        "AXAllowedValues": "allowed_values",
    },
    "windows": {
        "RangeValue.Value": "value",
        "RangeValue.Minimum": "minimum",
        "RangeValue.Maximum": "maximum",
        "RangeValue.SmallChange": "small_step",
        "RangeValue.LargeChange": "large_step",
        "RangeValue.IsReadOnly": "read_only",
    },
    "linux": {
        "Value.currentValue": "value",
        "Value.minimumValue": "minimum",
        "Value.maximumValue": "maximum",
        "Value.minimumIncrement": "small_step",
        "Value.text": "description",
    },
}
SUPPLEMENTS = {
    "windows": {
        "IsControlElement": "windows.is_control_element",
        "IsContentElement": "windows.is_content_element",
        "ItemType": "item_type",
        "ItemStatus": "item_status",
        "IsRequiredForForm": "required_for_form",
        "Window.WindowInteractionState": "window_interaction_state",
        "Table.RowOrColumnMajor": "table_traversal_order",
        "Text.StyleId": "text_style",
        "Win32.WindowContentScope": "window_content_scope",
    },
    "macos": {"AXDisclosing": "disclosing", "AXDocument": "document_uri"},
    "linux": {"sensitive": "linux.sensitive", "multiLine": "multiline"},
}
DISCARD_FIELDS = {
    "macos": frozenset(("AXPID",)),
    "windows": frozenset(
        (
            "RuntimeId",
            "ProcessId",
            "NativeWindowHandle",
            "ClassName",
            "FrameworkId",
            "UIA.ReadSource",
            "UIA.ReferenceScope",
            "Text.Source",
        )
    ),
    "linux": frozenset(("Accessible.processId", "pid", "busName", "objectPath")),
}
RELATIONS = {
    "macos": {
        "AXChildren": "children",
        "AXParent": "parent",
        "AXTitleUIElement": "labelled_by",
        "AXServesAsTitleForUIElements": "labels",
        "AXDisclosedByRow": "disclosed_by",
        "AXWindow": "window",
        "AXVerticalScrollBar": "vertical_scrollbar",
        "AXHorizontalScrollBar": "horizontal_scrollbar",
        "AXRows": "rows",
        "AXVisibleRows": "visible_rows",
        "AXColumns": "columns",
        "AXVisibleColumns": "visible_columns",
        "AXVisibleCells": "visible_cells",
        "AXSelectedCells": "selected_cells",
        "AXSelectedRows": "selected_rows",
        "AXSelectedColumns": "selected_columns",
        "AXRowHeaderUIElements": "row_headers",
        "AXColumnHeaderUIElements": "column_headers",
        "AXDisclosedRows": "disclosed_rows",
        "AXHeader": "header",
        "AXOwns": "owns",
        "AXLinkedUIElements": "linked",
        "AXSelectedChildren": "selected_children",
        "AXSharedFocusElements": "shared_focus",
    },
    "windows": {
        "RawViewChildren": "children",
        "RawViewParent": "parent",
        "LabeledBy": "labelled_by",
        "Selection.Selection": "selected_children",
        "SelectionItem.SelectionContainer": "selection_container",
        "GridItem.ContainingGrid": "table",
        "Table.RowHeaders": "row_headers",
        "Table.ColumnHeaders": "column_headers",
        "TableItem.RowHeaderItems": "row_headers",
        "TableItem.ColumnHeaderItems": "column_headers",
        "Text.Children": "embedded_children",
        "ItemContainer.Items": "logical_items",
        "Grid.Cells": "cells",
        "DescribedBy": "described_by",
        "FlowsTo": "flows_to",
        "FlowsFrom": "flows_from",
    },
    "linux": {
        "children": "children",
        "parent": "parent",
        "labelled-by": "labelled_by",
        "label-for": "labels",
        "Selection.selectedChildren": "selected_children",
        "TableCell.table": "table",
        "TableCell.rowHeaderCells": "row_headers",
        "TableCell.columnHeaderCells": "column_headers",
        "Table.cells": "cells",
        "Table.viewportCandidates": "viewport_candidates",
        "described-by": "described_by",
        "description-for": "describes",
        "flows-to": "flows_to",
        "flows-from": "flows_from",
        "member-of": "member_of",
        "controller-for": "controls",
        "controlled-by": "controlled_by",
        "popup-for": "popup_for",
        "subwindow-of": "subwindow_of",
        "embeds": "embeds",
        "embedded-by": "embedded_by",
        "node-child-of": "parent",
        "node-parent-of": "children",
    },
}
ACTION_KINDS = {
    "macos": {
        "AXPress": "invoke",
        "AXShowMenu": "show_menu",
        "AXRaise": "raise",
        "AXIncrement": "increment",
        "AXDecrement": "decrement",
        "AXConfirm": "confirm",
        "AXCancel": "cancel",
        "AXScrollToVisible": "scroll_into_view",
        "AXScrollUp": "scroll",
        "AXScrollDown": "scroll",
        "AXScrollLeft": "scroll",
        "AXScrollRight": "scroll",
    },
    "windows": {
        "Invoke.Invoke": "invoke",
        "Toggle.Toggle": "toggle",
        "SelectionItem.Select": "select",
        "SelectionItem.AddToSelection": "add_to_selection",
        "SelectionItem.RemoveFromSelection": "remove_from_selection",
        "ExpandCollapse.Expand": "expand",
        "ExpandCollapse.Collapse": "collapse",
        "ScrollItem.ScrollIntoView": "scroll_into_view",
        "Window.Close": "close",
    },
    "linux": {
        "click": "invoke",
        "press": "invoke",
        "activate": "invoke",
        "toggle": "toggle",
        "select": "select",
        "expand": "expand",
        "collapse": "collapse",
        "showmenu": "show_menu",
        "show-menu": "show_menu",
    },
}
