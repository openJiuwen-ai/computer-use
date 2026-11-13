// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

use super::{any, append, integer, is_bool, is_text, map, nonnegative, number, path, set, text, truthy, Builder, Value};

const MAC_ROLES: &[(&str, &str)] = &[
    ("AXCheckBox", "checkbox"),
    ("AXRadioButton", "radio_button"),
    ("AXPopUpButton", "popup_button"),
    ("AXTextField", "text_field"),
    ("AXTextArea", "text_area"),
    ("AXStaticText", "static_text"),
    ("AXScrollBar", "scrollbar"),
    ("AXOutline", "tree"),
    ("AXTabGroup", "tab_group"),
    ("AXIncrementor", "spinner"),
    ("AXSplitter", "separator"),
    ("AXSystemWide", "application"),
    ("AXMenuBarItem", "menu_bar_item"),
];

const ROLE_DETAILS: &[(&str, &str)] = &[
    ("AXCloseButton", "close"),
    ("AXMinimizeButton", "minimize"),
    ("AXZoomButton", "zoom"),
    ("AXFullScreenButton", "fullscreen"),
    ("AXSearchField", "search"),
    ("AXSecureTextField", "password"),
    ("AXStandardWindow", "standard"),
    ("AXFloatingWindow", "floating"),
    ("AXSystemDialog", "system"),
];

const TEXT_FIELDS: &[(&str, &str)] = &[
    ("AXTitle", "name"),
    ("AXDescription", "description"),
    ("AXHelp", "help"),
    ("AXIdentifier", "identifier"),
    ("AXPlaceholderValue", "placeholder"),
    ("AXURL", "url"),
    ("AXRoleDescription", "role_description"),
];

const STATE_FIELDS: &[(&str, &str)] = &[
    ("AXEnabled", "enabled"),
    ("AXFocused", "focused"),
    ("AXSelected", "selected"),
    ("AXEditable", "editable"),
    ("AXElementBusy", "busy"),
    ("AXModal", "modal"),
    ("AXMain", "main_window"),
    ("AXHasPopup", "has_popup"),
];

const STRUCTURE_FIELDS: &[(&str, &str)] = &[
    ("AXDisclosureLevel", "level"),
    ("AXIndex", "index"),
    ("AXRowCount", "row_count"),
    ("AXColumnCount", "column_count"),
];

const NUMBER_FIELDS: &[(&str, &str)] = &[
    ("AXMinValue", "minimum"),
    ("AXMaxValue", "maximum"),
    ("AXValueIncrement", "small_step"),
    ("AXAllowedValues", "allowed_values"),
];

const SUPPLEMENTS: &[(&str, &str)] = &[
    ("AXDisclosing", "disclosing"),
    ("AXDocument", "document_uri"),
];

const RELATIONS: &[(&str, &str)] = &[
    ("AXChildren", "children"),
    ("AXParent", "parent"),
    ("AXTitleUIElement", "labelled_by"),
    ("AXServesAsTitleForUIElements", "labels"),
    ("AXDisclosedByRow", "disclosed_by"),
    ("AXWindow", "window"),
    ("AXVerticalScrollBar", "vertical_scrollbar"),
    ("AXHorizontalScrollBar", "horizontal_scrollbar"),
    ("AXRows", "rows"),
    ("AXVisibleRows", "visible_rows"),
    ("AXColumns", "columns"),
    ("AXVisibleColumns", "visible_columns"),
    ("AXVisibleCells", "visible_cells"),
    ("AXSelectedCells", "selected_cells"),
    ("AXSelectedRows", "selected_rows"),
    ("AXSelectedColumns", "selected_columns"),
    ("AXRowHeaderUIElements", "row_headers"),
    ("AXColumnHeaderUIElements", "column_headers"),
    ("AXDisclosedRows", "disclosed_rows"),
    ("AXHeader", "header"),
    ("AXOwns", "owns"),
    ("AXLinkedUIElements", "linked"),
    ("AXSelectedChildren", "selected_children"),
    ("AXSharedFocusElements", "shared_focus"),
];

const ACTION_KINDS: &[(&str, &str)] = &[
    ("AXPress", "invoke"),
    ("AXShowMenu", "show_menu"),
    ("AXRaise", "raise"),
    ("AXIncrement", "increment"),
    ("AXDecrement", "decrement"),
    ("AXConfirm", "confirm"),
    ("AXCancel", "cancel"),
    ("AXScrollToVisible", "scroll_into_view"),
    ("AXScrollUp", "scroll"),
    ("AXScrollDown", "scroll"),
    ("AXScrollLeft", "scroll"),
    ("AXScrollRight", "scroll"),
];

const ROLES: &str = "application window dialog alert sheet popover group split_group scroll_area toolbar separator button toggle_button checkbox radio_button switch menu_button popup_button combo_box text_field text_area slider spinner scrollbar progress_indicator static_text heading image link document web_area list list_item tree tree_item row column cell table grid row_header column_header tab_group tab menu_bar menu_bar_item menu menu_item unknown header_item value_indicator browser ruler handle color_well date_field time_field disclosure_triangle grow_area dock_item increment_arrow decrement_arrow increment_page decrement_page";

pub(super) fn lookup<'a>(table: &'a [(&str, &str)], key: &str) -> Option<&'a str> {
    table.iter().find(|(name, _)| *name == key).map(|(_, value)| *value)
}

fn role(value: &str) -> String {
    if let Some(role) = lookup(MAC_ROLES, value) { return role.to_owned(); }
    standard_role(value.strip_prefix("AX").unwrap_or(value))
}

pub(super) fn standard_role(value: &str) -> String {
    let mut role = String::new();
    for (index, item) in value.chars().enumerate() {
        if index != 0 && item.is_ascii_uppercase() { role.push('_'); }
        role.extend(item.to_lowercase());
    }
    if ROLES.split(' ').any(|item| item == role) { role } else { "unknown".to_owned() }
}

fn ranged(values: &Value) -> bool {
    ["AXMinValue", "AXMaxValue", "AXValueIncrement"].iter().any(|field| values.get(field).is_some())
}

fn destination(node: &Builder<'_>, name: &str) -> Option<(&'static str, &'static str)> {
    if name == "AXValueDescription" {
        let group = if ranged(node.values) { "numeric_value" } else { "control_value" };
        return Some((group, "description"));
    }
    if let Some(field) = lookup(STATE_FIELDS, name) { return Some(("states", field)); }
    if let Some(field) = lookup(STRUCTURE_FIELDS, name) { return Some(("structure", field)); }
    if let Some(field) = lookup(NUMBER_FIELDS, name) { return Some(("numeric_value", field)); }
    None
}

fn special_path(name: &str) -> super::Path {
    match name {
        "AXValue" => path(&["control_value", "value"]),
        "AXSelectedTextRange" | "AXSelectedTextRanges" => path(&["text", "selections"]),
        "AXSelectedText" => path(&["text", "selected_text"]),
        "AXPosition" | "AXSize" => path(&["geometry", "bounds"]),
        _ => path(&["supplements", name]),
    }
}

pub(super) fn fields(node: &mut Builder<'_>) {
    if let Some(value) = node.values.get("AXRole") {
        if let Some(raw) = value.as_str() {
            let role = role(raw);
            node.role = Some(role.clone());
            node.put("AXRole", &["role"], text(&role), any);
            if role == "unknown" && raw != "AXUnknown" {
                node.supplement("AXRole", value, Some("provider.role"), false);
            }
        } else if matches!(value, Value::Null) {
            node.gap(&path(&["role"]), "absent", None);
        } else {
            node.gap(&path(&["role"]), "error", Some("Provider role is not a string."));
            node.supplement("AXRole", value, None, false);
        }
    }
    for provider in node.row.get("requestedFields").and_then(Value::as_seq).into_iter().flatten() {
        let Some(name) = provider.as_str() else { continue };
        if node.properties.iter().any(|(ordinal, property)| !property.arguments.is_empty()
            && property.key.as_str().is_some_and(|key| format!("{}::{key}#{ordinal}", property.key_namespace) == name))
        { continue; }
        if name == "AXRole" {
            node.paths.entry(name.to_owned()).or_insert_with(|| path(&["role"]));
            continue;
        }
        if name == "AXPID" { continue; }
        let value = node.values.get(name);
        if let Some(field) = lookup(TEXT_FIELDS, name) {
            node.paths.insert(name.to_owned(), path(&[field]));
            if let Some(value) = value { node.put(name, &[field], value.clone(), is_text); }
        } else if let Some((group, field)) = destination(node, name) {
            let keys = [group, field];
            let at = path(&keys);
            node.paths.insert(name.to_owned(), at.clone());
            if let Some(value) = value {
                match group {
                    "states" => node.put(name, &keys, value.clone(), is_bool),
                    "structure" => node.put(name, &keys, value.clone(), nonnegative),
                    _ if field == "description" => node.put(name, &keys, value.clone(), is_text),
                    _ => if let Some(converted) = node.extra(value, &at) {
                        let valid = field != "allowed_values" || converted.allowed_values();
                        let expected: fn(&Value) -> bool = match (field, valid) {
                            ("allowed_values", true) => any,
                            ("allowed_values", false) => |_| false,
                            _ => |value| number(value).is_some(),
                        };
                        let typed = converted.typed_diagnostic_value();
                        node.put_with_typed_origin(name, &keys, converted.into_value(), expected, typed);
                    },
                }
            }
        } else if let Some(key) = lookup(SUPPLEMENTS, name) {
            if let Some(value) = value { node.supplement(name, value, Some(key), false); }
        } else if let Some(value) = value {
            if !special(node, name, value) { node.supplement(name, value, None, false); }
        }
    }
    for (name, _) in node.row.get("fieldStatus").and_then(Value::as_map).into_iter().flatten() {
        if name != "AXPID" {
            node.paths.entry(name.clone()).or_insert_with(|| special_path(name));
        }
    }
}

fn special(node: &mut Builder<'_>, name: &str, value: &Value) -> bool {
    match name {
        "AXSubrole" => {
            let Some(value) = value.as_str() else { return false };
            if matches!(value, "AXDialog" | "AXSystemDialog") {
                set(&mut node.data, "role", text("dialog"));
                node.role = Some("dialog".to_owned());
            }
            if let Some(role) = lookup(ROLE_DETAILS, value) {
                node.put(name, &["role_detail"], text(role), any);
                if value == "AXSecureTextField" {
                    node.put(name, &["states", "protected"], Value::Bool(true), any);
                }
            } else if value != "AXDialog" {
                node.supplement(name, &text(value), Some("provider.role_detail"), false);
            }
        }
        "AXValue" => {
            let converted = node.extra(value, &path(&["control_value", "value"]));
            let scalar = converted.as_ref().is_some_and(super::Extra::scalar);
            let typed = converted.as_ref().and_then(super::Extra::typed_diagnostic_value);
            let converted = converted.map(super::Extra::into_value);
            if let Some((content, runs)) = converted.as_ref().and_then(|value|
                value.get("text").and_then(Value::as_str).zip(value.get("runs").and_then(Value::as_seq)))
            {
                node.put(name, &["control_value", "value"], text(content), any);
                set(node.text_info(), "content_field", Value::Seq(path(&["control_value", "value"])));
                let runs = runs.iter().enumerate().map(|(index, run)| super::text::attributed_run(node, run, index)).collect();
                set(node.text_info(), "runs", Value::Seq(runs));
            } else if let Some(converted) = converted.filter(|_| scalar) {
                node.put_with_typed_origin(name, &["control_value", "value"], converted.clone(), any, typed);
                if is_text(&converted) && ["AXSelectedText", "AXSelectedTextRange", "AXSelectedTextRanges",
                    "AXNumberOfCharacters"].iter().any(|name| node.values.get(name).is_some())
                {
                    set(node.text_info(), "content_field", Value::Seq(path(&["control_value", "value"])));
                }
                if matches!(node.role.as_deref(), Some("checkbox" | "radio_button" | "switch" | "toggle_button")) {
                    let toggle = match &converted {
                        Value::Bool(false) | Value::Int(0) => Some("off"),
                        Value::Bool(true) | Value::Int(1) => Some("on"),
                        Value::Int(2) => Some("mixed"),
                        _ => None,
                    };
                    if let Some(toggle) = toggle { node.put(name, &["states", "checked"], text(toggle), any); }
                }
                if number(&converted).is_some() && ranged(node.values) {
                    node.put(name, &["numeric_value", "value"], converted, any);
                }
            } else {
                node.supplement(name, value, None, false);
            }
        }
        "AXExpanded" => {
            let result = match value {
                Value::Null => Value::Null,
                Value::Bool(value) => text(if *value { "expanded" } else { "collapsed" }),
                Value::Text(value) => text(match value.as_str() {
                    "Expanded" => "expanded", "Collapsed" => "collapsed",
                    "PartiallyExpanded" => "partial", "LeafNode" => "leaf",
                    _ => return false,
                }),
                _ => return false,
            };
            node.put(name, &["states", "expanded"], result, any);
        }
        "AXOrientation" => {
            let at = ["structure", "orientation"];
            match value.as_str() {
                Some("AXUnknownOrientation" | "None") => {
                    node.paths.insert(name.to_owned(), path(&at));
                    node.gap(&path(&at), "absent", Some("Provider declares no orientation."));
                }
                Some("AXHorizontalOrientation" | "Horizontal") => node.put(name, &at, text("horizontal"), any),
                Some("AXVerticalOrientation" | "Vertical") => node.put(name, &at, text("vertical"), any),
                _ => return false,
            }
        }
        "AXPosition" | "AXSize" => {
            if value.as_map().is_none() { return false; }
            let mut combined = Value::Map(Vec::new());
            for key in ["AXPosition", "AXSize"] {
                for (name, item) in node.values.get(key).and_then(Value::as_map).into_iter().flatten() {
                    set(&mut combined, name, item.clone());
                }
            }
            let at = path(&["geometry", "bounds"]);
            let bounds = node.bounds(&combined, &at);
            set(&mut node.geometry, "bounds", bounds);
            node.paths.insert(name.to_owned(), at);
        }
        "AXRowIndexRange" | "AXColumnIndexRange" => {
            let endpoints = value.get("location").and_then(integer).zip(value.get("length").and_then(integer));
            let Some((start, length)) = endpoints.filter(|(start, length)| *start >= 0 && *length >= 0
                && start.checked_add(*length).is_some_and(|end| end <= 9_007_199_254_740_991))
                else { return false };
            if value.as_map().is_none_or(|items| items.len() != 2) { return false; }
            let (index, span) = if name == "AXRowIndexRange" { ("row_index", "row_span") } else { ("column_index", "column_span") };
            node.put(name, &["structure", index], Value::Int(start), any);
            node.put(name, &["structure", span], Value::Int(length), any);
        }
        "AXMenuItemCmdChar" | "AXMenuItemCmdModifiers" | "AXMenuItemCmdVirtualKey" | "AXMenuItemCmdGlyph" => (),
        name if name.starts_with("settable.") => (),
        _ => return super::text::field(node, name, value),
    }
    true
}

pub(super) fn status(node: &mut Builder<'_>, provider: &str, status: &Value) {
    let at = node.paths.get(provider).cloned().unwrap_or_else(|| {
        path(&["supplements", &format!("provider.{}.{provider}", node.backend)])
    });
    let mut continuation = None;
    match status.get("textRef") {
        Some(Value::Text(token)) if !token.is_empty() => {
            let delivered = node.values.get(provider).and_then(Value::as_str).map(|value| value.encode_utf16().count() as i64);
            let total = status.get("totalUnits").unwrap_or(&Value::Null);
            let valid = matches!(total, Value::Null) || nonnegative(total)
                && delivered.is_none_or(|delivered| integer(total).is_none_or(|total| delivered <= total));
            if valid {
                let mut reference = map([
                    ("token", text(token)), ("kind", text("text")), ("consistency", text("captured")),
                    ("unit", text("utf16_code_unit")),
                ]);
                if !matches!(total, Value::Null) { set(&mut reference, "total", total.clone()); }
                if let Some(delivered) = delivered {
                    set(&mut reference, "delivered", Value::Seq(vec![Value::Int(0), Value::Int(delivered)]));
                }
                continuation = Some(reference);
            } else {
                node.gap(&at, "error", Some("Text continuation coverage is invalid."));
            }
        }
        Some(value) if !matches!(value, Value::Null) => node.gap(&at, "error", Some("Text continuation token is invalid.")),
        _ => (),
    }
    node.issue(&at, status.get("state").and_then(Value::as_str).unwrap_or("error"),
        status.get("detail").cloned(), continuation, Vec::new());
    for key in ["rawResult", "wrappedNativeValue"] {
        if let Some(value) = status.get(key) {
            let diagnostic = key == "wrappedNativeValue" && node.values.get(provider).is_none()
                && matches!(status.get("state").and_then(Value::as_str), Some("absent" | "unsupported"))
                && status.get("nativeDomain").and_then(Value::as_str).is_some_and(|value| !value.is_empty())
                && matches!(value, Value::Int(_) | Value::BigInt(_)) && status.get("nativeCode") == Some(value);
            node.supplement(&format!("{provider}.failed_payload"), value,
                Some(&format!("provider.{}.{provider}.failed_payload", node.backend)), diagnostic);
        }
    }
}

pub(super) fn declared_actions(node: &mut Builder<'_>, kinds: &[(&str, &str)]) {
    let Some(catalogue) = node.row.get("actions") else { return };
    let items = catalogue.get("items").and_then(Value::as_seq).unwrap_or_default();
    for (index, raw) in items.iter().enumerate() {
        let Some(name) = raw.get("name").and_then(Value::as_str) else { continue };
        let kind = lookup(kinds, name).unwrap_or("custom");
        let mut action = map([("kind", text(kind)), ("key", text(name)), ("label", text(name))]);
        let at = vec![text("actions"), Value::Int(index as i64), text("description")];
        if let Some(description) = raw.get("description").filter(|value| !matches!(value, Value::Null)) {
            if is_text(description) { set(&mut action, "description", description.clone()); }
            else {
                node.gap(&at, "error", Some("Provider action description is not text."));
                node.supplement(&format!("action.{name}.description"), description, None, false);
            }
        }
        set(&mut action, "shortcuts", Value::Seq(Vec::new()));
        node.actions.push(action);
        if let Some(status) = raw.get("descriptionStatus").filter(|value| value.as_map().is_some()) {
            node.issue(&at, status.get("state").and_then(Value::as_str).unwrap_or("error"),
                status.get("detail").cloned(), None, Vec::new());
        }
    }
    if catalogue.get("state").and_then(Value::as_str) != Some("complete") {
        let queried = node.properties.iter().any(|(_, property)|
            property.key_namespace == "toolkit" && property.key.as_str() == Some("actions"));
        let mut detail = catalogue.get("detail").cloned();
        if let Some(total) = catalogue.get("total").and_then(integer) {
            let mut coverage = format!("Observed {} of {total} declared actions.", items.len());
            if let Some(detail) = detail.as_ref().and_then(Value::as_str).filter(|value| !value.is_empty()) {
                coverage.push(' ');
                coverage.push_str(detail);
            }
            detail = Some(text(coverage));
        }
        let state = if queried { catalogue.get("state").and_then(Value::as_str).unwrap_or("not_read") } else { "not_read" };
        node.issue(&path(&["actions"]), state, detail, None, Vec::new());
    }
}

pub(super) fn actions(node: &mut Builder<'_>) {
    declared_actions(node, ACTION_KINDS);
    for (provider, kind, target) in [
        ("settable.AXFocused", "focus", &[][..]),
        ("settable.AXValue", "set_value", &["control_value", "value"][..]),
        ("settable.AXSelectedText", "replace_selection", &["text", "selections"][..]),
    ] {
        if let Some(value) = node.values.get(provider) {
            if let Value::Bool(value) = value {
                let mut action = map([("kind", text(kind))]);
                if !target.is_empty() { set(&mut action, "target", Value::Seq(path(target))); }
                set(&mut action, "available", Value::Bool(*value));
                set(&mut action, "shortcuts", Value::Seq(Vec::new()));
                let at = append(path(&["actions"]), Value::Int(node.actions.len() as i64));
                node.paths.insert(provider.to_owned(), at);
                node.actions.push(action);
            } else {
                node.supplement(provider, value, None, false);
                if let Some(at) = node.paths.get(provider).cloned() {
                    node.gap(&at, "error", Some("Settable capability is not a boolean."));
                }
            }
        }
    }
    shortcuts(node);
}

fn shortcuts(node: &mut Builder<'_>) {
    let names = ["AXMenuItemCmdChar", "AXMenuItemCmdModifiers", "AXMenuItemCmdVirtualKey", "AXMenuItemCmdGlyph"];
    let known = node.role.as_deref() == Some("menu_item") &&
        ["AXRole", names[0], names[1]].iter().all(|name| node.row.get("fieldStatus").and_then(|states| states.get(name)).is_none());
    let character = node.values.get(names[0]).and_then(Value::as_str).and_then(|value| {
        let mut chars = value.chars();
        let first = chars.next()?;
        chars.next().is_none().then_some(first)
    });
    if let Some((character, modifiers)) = character.zip(node.values.get(names[1]).and_then(integer))
        .filter(|(_, modifiers)| known && (0..=15).contains(modifiers))
    {
        let scalar = character as u32;
        let key = match character {
            '\u{f700}' => "↑".to_owned(), '\u{f701}' => "↓".to_owned(), '\u{f702}' => "←".to_owned(),
            '\u{f703}' => "→".to_owned(), ' ' => "Space".to_owned(),
            _ if (0xf704..=0xf70f).contains(&scalar) => format!("F{}", scalar - 0xf704 + 1),
            _ => character.to_string(),
        };
        // Space and the named navigation keys have already been translated.
        // Match the original Unicode category C/Z exclusion for the rest.
        if key.chars().next().is_some_and(crate::diff::python_is_printable) {
            let mut shortcut = String::new();
            for (bit, label) in [(4, '⌃'), (2, '⌥'), (1, '⇧')] {
                if modifiers & bit != 0 { shortcut.push(label); }
            }
            if modifiers & 8 == 0 { shortcut.push('⌘'); }
            shortcut.push_str(&key);
            set(&mut node.data, "shortcuts", Value::Seq(vec![map([("text", text(shortcut)), ("kind", text("accelerator"))])]));
            return;
        }
    }
    for name in names {
        if let Some(value) = node.values.get(name) {
            if !matches!(value, Value::Int(0) | Value::Bool(false))
                && !matches!(value, Value::Float(number) if *number == 0.0)
                && value.as_str() != Some("")
            {
                node.supplement(name, value, Some(&format!("provider.macos.shortcut.{name}")), false);
            }
        }
    }
}

pub(super) fn relation(node: &mut Builder<'_>, edge: &Value) {
    let Some(name) = edge.get("property").and_then(Value::as_str) else { return };
    let kind = if node.backend == "macos" {
        lookup(RELATIONS, name)
    } else {
        super::provider::relation_kind(node.backend, name)
    }.map_or_else(|| format!("provider.{}.{name}", node.backend), str::to_owned);
    let mut key = kind.clone();
    let mut ordinal = 2;
    while node.relations.get(&key).is_some() {
        key = format!("{kind}:{ordinal}");
        ordinal += 1;
    }
    let targets = edge.get("targets").and_then(Value::as_seq).unwrap_or_default();
    let gaps = edge.get("unavailablePositions").and_then(Value::as_seq).unwrap_or_default();
    let sampled = kind == "viewport_candidates" || !gaps.is_empty();
    let mut record = map([("kind", text(&kind)), ("targets", Value::Seq(targets.to_vec()))]);
    if node.backend == "windows" && name.starts_with("RawView") {
        set(&mut record, "view", text("raw"));
    }
    if let Some(offset) = edge.get("offset").and_then(integer) {
        let count = edge.get("inspectedCount").and_then(integer).unwrap_or(targets.len() as i64);
        let end = offset + count;
        let positions = (offset..end).filter(|position| !gaps.contains(&Value::Int(*position))).map(Value::Int).collect();
        set(&mut record, "positions", Value::Seq(positions));
    }
    if let Some(total) = edge.get("total").filter(|value| !matches!(value, Value::Null)) {
        set(&mut record, "total", total.clone());
    }
    set(&mut record, "enumeration", text(if sampled { "sampled" } else { "enumerated" }));
    let certainty = edge.get("certainty").and_then(Value::as_str);
    set(&mut record, "evidence", text(if certainty == Some("confirmed") { "reported" } else { "candidate" }));
    if let Some(offset) = edge.get("offset") { set(&mut record, "offset", offset.clone()); }
    if let Some(count) = edge.get("inspectedCount") { set(&mut record, "inspected_count", count.clone()); }
    set(&mut record, "unavailable_positions", Value::Seq(gaps.to_vec()));
    set(&mut node.relations, &key, record);
    if let Some(membership) = edge.get("membershipObservedAt").and_then(Value::as_map) {
        let mut record = map([("relation", text(&key))]);
        for (name, item) in membership { set(&mut record, name, item.clone()); }
        node.supplement(&format!("{name}.membershipObservedAt"), &record, Some("relation_membership"), false);
    }
    set(&mut node.relation_fields, &key, text(name));
    let at = path(&["relations", &key]);
    let state = edge.get("state").and_then(Value::as_str).unwrap_or("error");
    let detail = edge.get("detail").cloned();
    if state != "complete" {
        let continuation = edge.get("cursor").and_then(Value::as_str).map(|token|
            map([("token", text(token)), ("kind", text("relation")), ("consistency", text("live"))]));
        node.issue(&at, state, detail, continuation, Vec::new());
    } else if sampled {
        node.issue(&at, "partial", detail, None, Vec::new());
    } else if let Some(detail) = detail.filter(truthy) {
        node.supplement(&format!("{name}.detail"), &map([("relation", text(&key)), ("text", detail)]), Some("relation_detail"), false);
    }
    if certainty == Some("conflicting") {
        node.gap(&at, "conflict", Some("Provider relationship declarations conflict."));
    }
}

pub(super) fn cf_date(seconds: f64) -> Option<String> {
    if !seconds.is_finite() || !(-100_000_000_000.0..=300_000_000_000.0).contains(&seconds) { return None; }
    let whole = seconds.trunc() as i64;
    let micros = (seconds.fract() * 1_000_000.0).round_ties_even() as i64;
    let micros = whole.checked_mul(1_000_000)?.checked_add(micros)?;
    let days = micros.div_euclid(86_400_000_000) + 11_323;
    let remainder = micros.rem_euclid(86_400_000_000);
    let seconds = remainder / 1_000_000;
    let fraction = remainder % 1_000_000;
    // Convert days since the Unix epoch into a proleptic Gregorian date.
    let days = days + 719_468;
    let era = days.div_euclid(146_097);
    let doe = days.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = mp + if mp < 10 { 3 } else { -9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    if !(1..=9999).contains(&year) { return None; }
    let mut date = format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}", seconds / 3600, seconds / 60 % 60, seconds % 60);
    if fraction != 0 { date.push_str(&format!(".{fraction:06}")); }
    date.push_str("+00:00");
    Some(date)
}
