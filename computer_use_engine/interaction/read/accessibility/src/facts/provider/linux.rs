// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Provider declarations mapped to the shared observation vocabulary.

pub(super) const ROLES: &[(&str, &str)] = &[
    ("frame", "window"),
    ("window", "window"),
    ("dialog", "dialog"),
    ("alert", "alert"),
    ("file chooser", "dialog"),
    ("font chooser", "dialog"),
    ("color chooser", "dialog"),
    ("application", "application"),
    ("push button", "button"),
    ("button", "button"),
    ("push button menu", "menu_button"),
    ("toggle button", "toggle_button"),
    ("check box", "checkbox"),
    ("radio button", "radio_button"),
    ("text", "text_field"),
    ("entry", "text_field"),
    ("password text", "text_field"),
    ("label", "static_text"),
    ("static", "static_text"),
    ("heading", "heading"),
    ("document web", "web_area"),
    ("document text", "document"),
    ("document spreadsheet", "document"),
    ("document frame", "document"),
    ("document presentation", "document"),
    ("table", "table"),
    ("table cell", "cell"),
    ("table row", "row"),
    ("table column header", "column_header"),
    ("column header", "column_header"),
    ("table row header", "row_header"),
    ("row header", "row_header"),
    ("list", "list"),
    ("list box", "list"),
    ("list item", "list_item"),
    ("tree", "tree"),
    ("tree table", "tree"),
    ("tree item", "tree_item"),
    ("scroll pane", "scroll_area"),
    ("scroll bar", "scrollbar"),
    ("slider", "slider"),
    ("spin button", "spinner"),
    ("combo box", "combo_box"),
    ("menu", "menu"),
    ("popup menu", "menu"),
    ("menu bar", "menu_bar"),
    ("menu item", "menu_item"),
    ("check menu item", "menu_item"),
    ("radio menu item", "menu_item"),
    ("tool bar", "toolbar"),
    ("page tab list", "tab_group"),
    ("page tab", "tab"),
    ("separator", "separator"),
    ("image", "image"),
    ("icon", "image"),
    ("link", "link"),
    ("progress bar", "progress_indicator"),
    ("panel", "group"),
    ("section", "group"),
    ("filler", "group"),
    ("viewport", "group"),
    ("embedded", "group"),
    ("grouping", "group"),
];

pub(super) const TEXT_FIELDS: &[(&str, &str)] = &[
    ("name", "name"),
    ("description", "description"),
    ("accessibleId", "identifier"),
];

pub(super) const STATE_FIELDS: &[(&str, &str)] = &[
    ("enabled", "enabled"),
    ("focused", "focused"),
    ("focusable", "focusable"),
    ("selectable", "selectable"),
    ("selected", "selected"),
    ("editable", "editable"),
    ("busy", "busy"),
    ("modal", "modal"),
    ("active", "active"),
    ("visible", "visible"),
    ("showing", "showing"),
];

pub(super) const STRUCTURE_FIELDS: &[(&str, &str)] = &[
    ("Table.nRows", "row_count"),
    ("Table.nColumns", "column_count"),
    ("Selection.nSelectedChildren", "selected_count"),
];

pub(super) const NUMBER_FIELDS: &[(&str, &str)] = &[
    ("Value.currentValue", "value"),
    ("Value.minimumValue", "minimum"),
    ("Value.maximumValue", "maximum"),
    ("Value.minimumIncrement", "small_step"),
    ("Value.text", "description"),
];

pub(super) const SUPPLEMENTS: &[(&str, &str)] = &[
    ("sensitive", "linux.sensitive"),
    ("multiLine", "multiline"),
];

pub(super) const DISCARD_FIELDS: &[&str] = &[
    "Accessible.processId",
    "busName",
    "objectPath",
    "pid",
];

pub(super) const RELATIONS: &[(&str, &str)] = &[
    ("children", "children"),
    ("parent", "parent"),
    ("labelled-by", "labelled_by"),
    ("label-for", "labels"),
    ("Selection.selectedChildren", "selected_children"),
    ("TableCell.table", "table"),
    ("TableCell.rowHeaderCells", "row_headers"),
    ("TableCell.columnHeaderCells", "column_headers"),
    ("Table.cells", "cells"),
    ("Table.viewportCandidates", "viewport_candidates"),
    ("described-by", "described_by"),
    ("description-for", "describes"),
    ("flows-to", "flows_to"),
    ("flows-from", "flows_from"),
    ("member-of", "member_of"),
    ("controller-for", "controls"),
    ("controlled-by", "controlled_by"),
    ("popup-for", "popup_for"),
    ("subwindow-of", "subwindow_of"),
    ("embeds", "embeds"),
    ("embedded-by", "embedded_by"),
    ("node-child-of", "parent"),
    ("node-parent-of", "children"),
];

pub(super) const ACTION_KINDS: &[(&str, &str)] = &[
    ("click", "invoke"),
    ("press", "invoke"),
    ("activate", "invoke"),
    ("toggle", "toggle"),
    ("select", "select"),
    ("expand", "expand"),
    ("collapse", "collapse"),
    ("showmenu", "show_menu"),
    ("show-menu", "show_menu"),
];
