// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Provider declarations mapped to the shared observation vocabulary.

pub(super) const ROLES: &[(&str, &str)] = &[
    ("Edit", "text_field"),
    ("Text", "static_text"),
    ("Pane", "group"),
    ("Group", "group"),
    ("DataGrid", "grid"),
    ("Table", "table"),
    ("DataItem", "row"),
    ("ListItem", "list_item"),
    ("Tree", "tree"),
    ("TreeItem", "tree_item"),
    ("Tab", "tab_group"),
    ("TabItem", "tab"),
    ("Hyperlink", "link"),
    ("SplitButton", "menu_button"),
    ("Spinner", "spinner"),
    ("ProgressBar", "progress_indicator"),
    ("ToolBar", "toolbar"),
    ("Separator", "separator"),
    ("CheckBox", "checkbox"),
    ("RadioButton", "radio_button"),
    ("ScrollBar", "scrollbar"),
    ("Header", "group"),
    ("HeaderItem", "header_item"),
    ("Custom", "unknown"),
    ("Thumb", "handle"),
];

pub(super) const TEXT_FIELDS: &[(&str, &str)] = &[
    ("Name", "name"),
    ("FullDescription", "description"),
    ("HelpText", "help"),
    ("AutomationId", "identifier"),
    ("LocalizedControlType", "role_description"),
];

pub(super) const STATE_FIELDS: &[(&str, &str)] = &[
    ("IsEnabled", "enabled"),
    ("HasKeyboardFocus", "focused"),
    ("IsKeyboardFocusable", "focusable"),
    ("IsPassword", "protected"),
    ("IsOffscreen", "offscreen"),
    ("SelectionItem.IsSelected", "selected"),
    ("Window.IsModal", "modal"),
];

pub(super) const STRUCTURE_FIELDS: &[(&str, &str)] = &[
    ("Grid.RowCount", "row_count"),
    ("Grid.ColumnCount", "column_count"),
    ("GridItem.Row", "row_index"),
    ("GridItem.Column", "column_index"),
    ("GridItem.RowSpan", "row_span"),
    ("GridItem.ColumnSpan", "column_span"),
    ("Selection.CanSelectMultiple", "can_select_multiple"),
    ("Selection.IsSelectionRequired", "selection_required"),
    ("PositionInSet", "index"),
    ("SizeOfSet", "set_size"),
    ("Level", "level"),
    ("HeadingLevel", "heading_level"),
    ("LocalizedLandmarkType", "landmark_description"),
];

pub(super) const NUMBER_FIELDS: &[(&str, &str)] = &[
    ("RangeValue.Value", "value"),
    ("RangeValue.Minimum", "minimum"),
    ("RangeValue.Maximum", "maximum"),
    ("RangeValue.SmallChange", "small_step"),
    ("RangeValue.LargeChange", "large_step"),
    ("RangeValue.IsReadOnly", "read_only"),
];

pub(super) const SUPPLEMENTS: &[(&str, &str)] = &[
    ("IsControlElement", "windows.is_control_element"),
    ("IsContentElement", "windows.is_content_element"),
    ("ItemType", "item_type"),
    ("ItemStatus", "item_status"),
    ("IsRequiredForForm", "required_for_form"),
    ("Window.WindowInteractionState", "window_interaction_state"),
    ("Table.RowOrColumnMajor", "table_traversal_order"),
    ("Text.StyleId", "text_style"),
    ("Win32.WindowContentScope", "window_content_scope"),
];

pub(super) const DISCARD_FIELDS: &[&str] = &[
    "ClassName",
    "FrameworkId",
    "NativeWindowHandle",
    "ProcessId",
    "RuntimeId",
    "Text.Source",
    "UIA.ReadSource",
    "UIA.ReferenceScope",
];

pub(super) const RELATIONS: &[(&str, &str)] = &[
    ("RawViewChildren", "children"),
    ("RawViewParent", "parent"),
    ("LabeledBy", "labelled_by"),
    ("Selection.Selection", "selected_children"),
    ("SelectionItem.SelectionContainer", "selection_container"),
    ("GridItem.ContainingGrid", "table"),
    ("Table.RowHeaders", "row_headers"),
    ("Table.ColumnHeaders", "column_headers"),
    ("TableItem.RowHeaderItems", "row_headers"),
    ("TableItem.ColumnHeaderItems", "column_headers"),
    ("Text.Children", "embedded_children"),
    ("ItemContainer.Items", "logical_items"),
    ("Grid.Cells", "cells"),
    ("DescribedBy", "described_by"),
    ("FlowsTo", "flows_to"),
    ("FlowsFrom", "flows_from"),
];

pub(super) const ACTION_KINDS: &[(&str, &str)] = &[
    ("Invoke.Invoke", "invoke"),
    ("Toggle.Toggle", "toggle"),
    ("SelectionItem.Select", "select"),
    ("SelectionItem.AddToSelection", "add_to_selection"),
    ("SelectionItem.RemoveFromSelection", "remove_from_selection"),
    ("ExpandCollapse.Expand", "expand"),
    ("ExpandCollapse.Collapse", "collapse"),
    ("ScrollItem.ScrollIntoView", "scroll_into_view"),
    ("Window.Close", "close"),
];
