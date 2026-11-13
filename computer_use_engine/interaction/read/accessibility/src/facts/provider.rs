// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! UIA and AT-SPI declarations for the shared capture builder.

mod linux;
mod windows;

use super::{any, integer, is_bool, is_text, macos, map, nonnegative, number, path, set, text, Builder, Value};

type Fields = &'static [(&'static str, &'static str)];

struct Provider {
    role: &'static str,
    roles: Fields,
    text: Fields,
    states: Fields,
    structure: Fields,
    numbers: Fields,
    supplements: Fields,
    discarded: &'static [&'static str],
    relations: Fields,
    actions: Fields,
}

const WINDOWS: Provider = Provider {
    role: "ControlType",
    roles: windows::ROLES,
    text: windows::TEXT_FIELDS,
    states: windows::STATE_FIELDS,
    structure: windows::STRUCTURE_FIELDS,
    numbers: windows::NUMBER_FIELDS,
    supplements: windows::SUPPLEMENTS,
    discarded: windows::DISCARD_FIELDS,
    relations: windows::RELATIONS,
    actions: windows::ACTION_KINDS,
};

const LINUX: Provider = Provider {
    role: "role",
    roles: linux::ROLES,
    text: linux::TEXT_FIELDS,
    states: linux::STATE_FIELDS,
    structure: linux::STRUCTURE_FIELDS,
    numbers: linux::NUMBER_FIELDS,
    supplements: linux::SUPPLEMENTS,
    discarded: linux::DISCARD_FIELDS,
    relations: linux::RELATIONS,
    actions: linux::ACTION_KINDS,
};

fn rules(backend: &str) -> &'static Provider {
    match backend {
        "windows" => &WINDOWS,
        "linux" => &LINUX,
        _ => unreachable!("portable provider"),
    }
}

pub(super) fn discarded(backend: &str, name: &str) -> bool {
    match backend {
        "macos" => name == "AXPID",
        _ => rules(backend).discarded.contains(&name),
    }
}

pub(super) fn relation_kind(backend: &str, name: &str) -> Option<&'static str> {
    macos::lookup(rules(backend).relations, name)
}

fn role(backend: &str, raw: &str) -> String {
    if backend == "windows" {
        let name = raw.strip_prefix("ControlType.").unwrap_or(raw);
        macos::lookup(WINDOWS.roles, name).map_or_else(|| macos::standard_role(name), str::to_owned)
    } else {
        macos::lookup(LINUX.roles, &raw.to_lowercase()).unwrap_or("unknown").to_owned()
    }
}

fn role_fields(node: &mut Builder<'_>, provider: &Provider) {
    let Some(value) = node.values.get(provider.role) else { return };
    match value {
        Value::Null => node.gap(&path(&["role"]), "absent", None),
        Value::Text(raw) => {
            let role = role(node.backend, raw);
            node.role = Some(role.clone());
            node.put(provider.role, &["role"], text(&role), any);
            if role == "unknown" {
                node.supplement(provider.role, value, Some("provider.role"), false);
            }
            let detail = match (node.backend, raw.as_str()) {
                ("windows", "Header" | "ControlType.Header") => "header",
                ("linux", "file chooser") => "file_chooser",
                ("linux", "font chooser") => "font_chooser",
                ("linux", "color chooser") => "color_chooser",
                ("linux", "password text") => {
                    node.put(provider.role, &["states", "protected"], Value::Bool(true), is_bool);
                    "password"
                }
                ("linux", "document spreadsheet") => "spreadsheet",
                ("linux", "document presentation") => "presentation",
                ("linux", "check menu item") => "check",
                ("linux", "radio menu item") => "radio",
                ("linux", "popup menu") => "popup",
                _ => return,
            };
            node.put(provider.role, &["role_detail"], text(detail), is_text);
        }
        _ => {
            node.gap(&path(&["role"]), "error", Some("Provider role is not a string."));
            node.supplement(provider.role, value, None, false);
        }
    }
}

fn destination(name: &str) -> super::Path {
    match name {
        "Value.Value" => path(&["control_value", "value"]),
        "Value.IsReadOnly" => path(&["control_value", "read_only"]),
        "Text.DocumentRange" | "Text.text" => path(&["text", "content"]),
        "Text.IsReadOnly" => path(&["text", "read_only"]),
        "Text.Selection" | "Text.selections" => path(&["text", "selections"]),
        "Text.VisibleRanges" => path(&["text", "visible_ranges"]),
        "Text.CaretRange" => path(&["text", "caret"]),
        "Text.LineRanges" => path(&["text", "line_ranges"]),
        "Text.ObjectRanges" => path(&["text", "embedded"]),
        "Text.characterCount" => path(&["text", "character_count"]),
        "Toggle.ToggleState" => path(&["states", "checked"]),
        "ExpandCollapse.ExpandCollapseState" => path(&["states", "expanded"]),
        "BoundingRectangle" | "Component.extents" => path(&["geometry", "bounds"]),
        _ => path(&["supplements", name]),
    }
}

pub(super) fn fields(node: &mut Builder<'_>) {
    let provider = rules(node.backend);
    role_fields(node, provider);
    let requested = node.row.get("requestedFields").and_then(Value::as_seq).unwrap_or_default();
    for name in requested.iter().filter_map(Value::as_str) {
        if node.has_method(name) || provider.discarded.contains(&name) {
            continue;
        }
        if name == provider.role {
            node.paths.entry(name.to_owned()).or_insert_with(|| path(&["role"]));
            continue;
        }
        let value = node.values.get(name);
        if let Some(field) = macos::lookup(provider.text, name) {
            put_field(node, name, &[field], value, is_text);
        } else if let Some(field) = macos::lookup(provider.states, name) {
            put_field(node, name, &["states", field], value, is_bool);
        } else if let Some(field) = macos::lookup(provider.structure, name) {
            let adjusted = match (name, value.and_then(integer)) {
                ("HeadingLevel", Some(80050)) | ("PositionInSet", Some(0)) => Some(Value::Null),
                ("HeadingLevel", Some(value @ 80051..=80059)) => Some(Value::Int(value - 80050)),
                ("PositionInSet", Some(value)) if value > 0 => Some(Value::Int(value - 1)),
                _ => None,
            };
            let expected: fn(&Value) -> bool = match field {
                "can_select_multiple" | "selection_required" => is_bool,
                "landmark_description" => is_text,
                "row_span" | "column_span" => |value| nonnegative(value) && integer(value) != Some(0),
                _ => nonnegative,
            };
            put_field(node, name, &["structure", field], adjusted.as_ref().or(value), expected);
        } else if let Some(field) = macos::lookup(provider.numbers, name) {
            let keys = ["numeric_value", field];
            let at = path(&keys);
            node.paths.insert(name.to_owned(), at.clone());
            if let Some(extra) = value.and_then(|value| node.extra(value, &at)) {
                let expected: fn(&Value) -> bool = match field {
                    "description" => is_text,
                    "read_only" => is_bool,
                    _ => |value| number(value).is_some(),
                };
                let typed = extra.typed_diagnostic_value();
                node.put_with_typed_origin(name, &keys, extra.into_value(), expected, typed);
            }
        } else if let Some(key) = macos::lookup(provider.supplements, name) {
            if let Some(value) = value {
                if matches!(name, "IsControlElement" | "IsContentElement" | "IsRequiredForForm" | "sensitive" | "multiLine")
                    && !is_bool(value)
                {
                    node.gap(&[text("supplements"), Value::Int(node.supplements.len() as i64), text("value")],
                        "error", Some("Provider declaration is not a boolean."));
                }
                node.supplement(name, value, Some(key), false);
            }
        } else if let Some(value) = value {
            if !special(node, name, value) {
                node.supplement(name, value, None, false);
            }
        }
    }
    for (name, _) in node.row.get("fieldStatus").and_then(Value::as_map).into_iter().flatten() {
        if !provider.discarded.contains(&name.as_str()) {
            node.paths.entry(name.clone()).or_insert_with(|| destination(name));
        }
    }
}

fn put_field(node: &mut Builder<'_>, name: &str, keys: &[&str], value: Option<&Value>, expected: fn(&Value) -> bool) {
    node.paths.insert(name.to_owned(), path(keys));
    if let Some(value) = value {
        node.put(name, keys, value.clone(), expected);
    }
}

fn has_action(node: &Builder<'_>, names: &[&str]) -> bool {
    node.row.get("actions").and_then(|catalogue| catalogue.get("items")).and_then(Value::as_seq)
        .into_iter().flatten().filter_map(|action| action.get("name").and_then(Value::as_str))
        .any(|name| names.iter().any(|item| name.eq_ignore_ascii_case(item)))
}

pub(crate) fn reference_scope(value: &Value) -> bool {
    value.as_map().is_some_and(|items| items.len() == 3)
        && matches!(value.get("ownership").and_then(Value::as_str), Some("confirmed" | "unknown" | "outside"))
        && matches!(value.get("input").and_then(Value::as_str), Some("liveValidation" | "observationOnly"))
        && value.get("detail").is_some_and(is_text)
}

fn special(node: &mut Builder<'_>, name: &str, value: &Value) -> bool {
    match name {
        "UIA.ReferenceScopeEvidence" if node.backend == "windows" && reference_scope(value)
            && node.row.get("fieldStatus").and_then(|status| status.get(name)).is_none() =>
            node.supplement(name, value, Some("observation_scope"), false),
        "Value.Value" => {
            let keys = ["control_value", "value"];
            if let Some(extra) = node.extra(value, &path(&keys)) {
                if extra.scalar() {
                    let typed = extra.typed_diagnostic_value();
                    node.put_with_typed_origin(name, &keys, extra.into_value(), any, typed);
                    return true;
                }
            }
            return false;
        }
        "Value.IsReadOnly" | "readOnly" => node.put(name, &["control_value", "read_only"], value.clone(), is_bool),
        "expanded" | "ExpandCollapse.ExpandCollapseState" => {
            if value == &Value::Bool(false) && name == "expanded"
                && !matches!(node.role.as_deref(), Some("tree" | "tree_item" | "combo_box" | "row" | "disclosure_triangle"))
                && !has_action(node, &["expand", "collapse"])
            {
                return false;
            }
            let state = match value {
                Value::Null => Value::Null,
                Value::Bool(value) if name == "expanded" => text(if *value { "expanded" } else { "collapsed" }),
                Value::Text(value) => text(match value.as_str() {
                    "Expanded" => "expanded",
                    "Collapsed" => "collapsed",
                    "PartiallyExpanded" => "partial",
                    "LeafNode" => "leaf",
                    _ => return false,
                }),
                _ => return false,
            };
            node.put(name, &["states", "expanded"], state, any);
        }
        "checked" | "indeterminate" | "Toggle.ToggleState" => {
            let mixed = node.values.get("indeterminate") == Some(&Value::Bool(true));
            if value == &Value::Bool(false) && name == "checked"
                && !matches!(node.role.as_deref(), Some("checkbox" | "radio_button" | "switch" | "toggle_button"))
                && !matches!(node.data.get("role_detail").and_then(Value::as_str), Some("check" | "radio"))
                && !has_action(node, &["toggle"]) && !mixed
            {
                return false;
            }
            let state = match value {
                Value::Null => Value::Null,
                Value::Bool(false) if name == "indeterminate" => return true,
                Value::Bool(true) if name == "indeterminate" => text("mixed"),
                Value::Bool(value) if name == "checked" => text(if *value { "on" } else { "off" }),
                Value::Text(value) => text(match value.as_str() {
                    "On" => "on",
                    "Off" => "off",
                    "Indeterminate" => "mixed",
                    _ => return false,
                }),
                _ => return false,
            };
            if name != "checked" || !mixed || matches!(state, Value::Null) {
                node.put(name, &["states", "checked"], state, any);
            }
        }
        "Window.WindowVisualState" => {
            let state = match value.as_str() {
                Some("Normal") => "normal",
                Some("Minimized") => "minimized",
                Some("Maximized") => "maximized",
                _ => return false,
            };
            node.put(name, &["states", "window_state"], text(state), any);
        }
        "Orientation" => {
            let keys = ["structure", "orientation"];
            match value.as_str() {
                Some("None") => {
                    node.paths.insert(name.to_owned(), path(&keys));
                    node.gap(&path(&keys), "absent", Some("Provider declares no orientation."));
                }
                Some("Horizontal") => node.put(name, &keys, text("horizontal"), any),
                Some("Vertical") => node.put(name, &keys, text("vertical"), any),
                _ => return false,
            }
        }
        "LandmarkType" => {
            let role = match integer(value) {
                Some(0) => Value::Null,
                Some(80000) => text("custom"),
                Some(80001) => text("form"),
                Some(80002) => text("main"),
                Some(80003) => text("navigation"),
                Some(80004) => text("search"),
                _ => return false,
            };
            node.put(name, &["structure", "landmark"], role, any);
        }
        "BoundingRectangle" | "Component.extents" | "Win32.ClientRectangle" if value.as_map().is_some() => {
            let kind = if name == "Win32.ClientRectangle" { "client" } else { "bounds" };
            let at = path(&["geometry", kind]);
            let bounds = node.bounds(value, &at);
            set(&mut node.geometry, kind, bounds);
            node.paths.insert(name.to_owned(), at);
        }
        "SupportedPatterns" | "interfaces" => (),
        _ if name.starts_with("settable.") => (),
        "AccessKey" | "AcceleratorKey" => {
            let Some(shortcut) = value.as_str() else { return false };
            if !shortcut.is_empty() {
                let kind = if name == "AccessKey" { "access_key" } else { "accelerator" };
                let mut previous = node.data.get("shortcuts").and_then(Value::as_seq).unwrap_or_default().to_vec();
                previous.push(map([("text", text(shortcut)), ("kind", text(kind))]));
                set(&mut node.data, "shortcuts", Value::Seq(previous));
            }
        }
        "attributes" if value.as_map().is_some() => attributes(node, name, value),
        "TableCell.rowColumnSpan" if value.as_map().is_some() => cell(node, name, value),
        "UIA.Collection" if value.as_map().is_some() => collection(node, name, value),
        _ if name.starts_with("Scroll.") => return scroll(node, name, value),
        _ => return super::text::provider_field(node, name, value),
    }
    true
}

fn attributes(node: &mut Builder<'_>, name: &str, value: &Value) {
    for (key, value) in value.as_map().into_iter().flatten() {
        let field = match key.as_str() {
            "level" => Some("level"),
            "posinset" => Some("index"),
            "setsize" => Some("set_size"),
            _ => None,
        };
        if matches!(key.as_str(), "placeholder-text" | "placeholder") && is_text(value) {
            node.put(name, &["placeholder"], value.clone(), is_text);
        } else if let Some((field, number)) = field.zip(value.as_str().and_then(|value| {
            (!value.is_empty() && value.bytes().all(|digit| digit.is_ascii_digit()))
                .then(|| value.parse::<i64>().ok()).flatten()
        })) {
            let number = number - i64::from(key == "posinset");
            if number >= 0 {
                node.put(name, &["structure", field], Value::Int(number), nonnegative);
            } else {
                node.gap(&path(&["structure", field]), "error", Some("Provider position-in-set must be positive."));
                node.supplement(&format!("attributes.{key}"), value, Some(&format!("provider.attribute.{key}")), false);
            }
        } else {
            node.supplement(&format!("attributes.{key}"), value, Some(&format!("provider.attribute.{key}")), false);
        }
    }
}

fn cell(node: &mut Builder<'_>, name: &str, value: &Value) {
    const MAX: i64 = 9_007_199_254_740_991;
    let fields = [("row", "row_index", 0), ("column", "column_index", 0),
                  ("rowSpan", "row_span", 1), ("columnSpan", "column_span", 1)];
    let numbers: Option<Vec<_>> = fields.iter().map(|(source, _, minimum)| {
        value.get(source).and_then(integer).filter(|item| (*minimum..=MAX).contains(item))
    }).collect();
    let Some(numbers) = numbers.filter(|items| value.as_map().is_some_and(|map| map.len() == 4)
        && items[0] + items[2] <= MAX && items[1] + items[3] <= MAX) else {
        node.supplement(name, value, None, false);
        node.gap(&path(&["structure"]), "error", Some("Provider cell position is not a complete valid row/column/span record."));
        return;
    };
    for ((_, field, _), value) in fields.into_iter().zip(numbers) {
        node.put(name, &["structure", field], Value::Int(value), nonnegative);
    }
    if !["visible", "showing"].iter().all(|key| node.values.get(key) == Some(&Value::Bool(true))
        && node.row.get("fieldStatus").and_then(|status| status.get(key)).is_none())
    {
        node.gap(&path(&["structure"]), "partial", Some("Cell viewport visibility: unknown."));
    }
}

fn collection(node: &mut Builder<'_>, name: &str, value: &Value) {
    let get = |key| value.get(key).filter(|value| !matches!(value, Value::Null));
    if let Some(value) = get("isVirtualizedItem").filter(|value| is_bool(value)) {
        node.put(name, &["states", "virtualized"], value.clone(), is_bool);
    }
    let may_virtualize = get("mayContainVirtualizedItems").and_then(Value::as_bool);
    let complete = get("logicalContentComplete").and_then(Value::as_bool);
    if may_virtualize == Some(true) {
        node.put(name, &["structure", "collection_mode"], text("virtualized"), is_text);
    } else if complete == Some(true) {
        node.put(name, &["structure", "collection_mode"], text("materialized"), is_text);
    }
    let declared = ["isVirtualizedItem", "mayContainVirtualizedItems", "logicalContentComplete"];
    let mut residual = map(value.as_map().into_iter().flatten().filter(|(key, _)|
        !matches!(key.as_str(), "view" | "isVirtualizedItem" | "mayContainVirtualizedItems" | "logicalContentComplete" | "detail")).cloned());
    for key in declared {
        if let Some(value) = get(key).filter(|value| !is_bool(value)) {
            set(&mut residual, key, value.clone());
            node.gap(&path(&["structure", "collection_mode"]), "error", Some("Collection coverage declaration is not a boolean."));
        }
    }
    if residual.as_map().is_some_and(|map| !map.is_empty()) {
        node.supplement(&format!("{name}.extra"), &residual, None, false);
    }
    if let Some(value) = get("view").filter(|value| value.as_str() != Some("raw")) {
        node.supplement(&format!("{name}.view"), value, Some("collection.view"), false);
    }
    let detail = get("detail");
    if may_virtualize == Some(true) || complete == Some(false) {
        node.issue(&path(&["structure", "collection_mode"]), "partial",
            detail.filter(|value| super::truthy(value)).cloned().or_else(|| Some(text("Logical item coverage: partial."))), None, Vec::new());
    } else if let Some(detail) = detail {
        node.supplement(&format!("{name}.detail"), detail, Some("collection.detail"), false);
    }
}

fn scroll(node: &mut Builder<'_>, name: &str, value: &Value) -> bool {
    let (axis, field) = match name.strip_prefix("Scroll.") {
        Some("HorizontallyScrollable") => ("scroll_x", "scrollable"),
        Some("VerticallyScrollable") => ("scroll_y", "scrollable"),
        Some("HorizontalScrollPercent") => ("scroll_x", "position"),
        Some("VerticalScrollPercent") => ("scroll_y", "position"),
        Some("HorizontalViewSize") => ("scroll_x", "viewport"),
        Some("VerticalViewSize") => ("scroll_y", "viewport"),
        _ => return false,
    };
    let at = path(&["structure", axis, field]);
    let mut current = node.group("structure").get(axis).cloned().unwrap_or_else(|| Value::Map(Vec::new()));
    if field == "scrollable" && is_bool(value) {
        set(&mut current, field, value.clone());
    } else if let Some(value) = number(value).filter(|value| field != "scrollable" && value.is_finite()) {
        if field == "position" && value == -1.0 {
            node.gap(&at, "absent", Some("No scroll position is declared on this axis."));
        } else {
            set(&mut current, field, Value::Float(value / 100.0));
            set(&mut current, "unit", text("fraction"));
        }
    } else {
        return false;
    }
    set(node.group("structure"), axis, current);
    node.paths.insert(name.to_owned(), at);
    true
}

fn action(node: &mut Builder<'_>, kind: &str, target: &[&str], available: Option<bool>) {
    let mut value = map([("kind", text(kind))]);
    if !target.is_empty() { set(&mut value, "target", Value::Seq(path(target))); }
    if let Some(available) = available { set(&mut value, "available", Value::Bool(available)); }
    set(&mut value, "shortcuts", Value::Seq(Vec::new()));
    node.actions.push(value);
}

pub(super) fn actions(node: &mut Builder<'_>) {
    macos::declared_actions(node, rules(node.backend).actions);
    match node.backend {
        "windows" => windows_actions(node),
        "linux" => linux_actions(node),
        _ => unreachable!("portable provider"),
    }
}

fn windows_actions(node: &mut Builder<'_>) {
    let patterns = node.values.get("SupportedPatterns");
    let items = patterns.and_then(Value::as_seq).unwrap_or_default();
    if let Some(patterns) = patterns.filter(|value| value.as_seq().is_none() || items.iter().any(|item| !is_text(item))) {
        node.supplement("SupportedPatterns", patterns, None, false);
    }
    let name = |item: &str| item.find("Pattern").unwrap_or(item.len());
    let has = |key| items.iter().filter_map(Value::as_str).any(|item| &item[..name(item)] == key);
    for (pattern, target, readonly) in [
        ("Value", ["control_value", "value"], "Value.IsReadOnly"),
        ("RangeValue", ["numeric_value", "value"], "RangeValue.IsReadOnly"),
    ] {
        if has(pattern) {
            action(node, "set_value", &target, node.values.get(readonly).and_then(Value::as_bool).map(|value| !value));
        }
    }
    if has("Scroll") { action(node, "scroll", &["structure"], None); }
    if has("SelectionItem") { node.put("SupportedPatterns", &["states", "selectable"], Value::Bool(true), is_bool); }
    if let Some(focusable) = node.values.get("IsKeyboardFocusable").and_then(Value::as_bool) {
        action(node, "focus", &[], Some(focusable));
    }
    let known = ["Invoke", "Toggle", "SelectionItem", "ExpandCollapse", "ScrollItem", "Window", "Value",
        "RangeValue", "Scroll", "Text", "Text2", "TextChild", "Grid", "GridItem", "Table", "TableItem",
        "Selection", "Selection2", "ItemContainer", "VirtualizedItem"];
    let unknown: Vec<_> = items.iter().filter_map(Value::as_str).filter(|item| !known.contains(&&item[..name(item)]))
        .map(text).collect();
    if !unknown.is_empty() {
        node.supplement("SupportedPatterns", &Value::Seq(unknown), Some("provider.windows.patterns"), false);
    }
}

fn linux_actions(node: &mut Builder<'_>) {
    let Some(raw) = node.values.get("interfaces") else {
        if node.values.get("focusable") == Some(&Value::Bool(true)) {
            action(node, "focus", &[], Some(true));
        }
        return;
    };
    let Some(items) = raw.as_seq() else {
        node.supplement("interfaces", raw, None, false);
        return;
    };
    let has = |name| items.iter().any(|item| item.as_str() == Some(name));
    if has("EditableText") {
        action(node, "insert_text", &["text", "content"], None);
        action(node, "set_value", &["text", "content"], None);
    }
    if has("Value") { action(node, "set_value", &["numeric_value", "value"], None); }
    if node.values.get("focusable") == Some(&Value::Bool(true)) {
        action(node, "focus", &[], Some(true));
    }
    let known = ["Accessible", "Action", "Component", "Text", "EditableText", "Value", "Selection", "Table",
        "TableCell", "Document", "Hypertext", "Hyperlink", "Image"];
    let unknown: Vec<_> = items.iter().filter(|value| value.as_str().is_none_or(|item| !known.contains(&item))).cloned().collect();
    if !unknown.is_empty() {
        node.supplement("interfaces", &Value::Seq(unknown), Some("provider.linux.interfaces"), false);
    }
}
