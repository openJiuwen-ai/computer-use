// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Evidence used to advertise UIA and AT-SPI input routes.

use super::{field, Capabilities, Field, Support, Value};
use crate::facts::provider::reference_scope;

fn role(platform: &str, raw: Field<'_>) -> Option<String> {
    let raw = match raw {
        Field::Absent | Field::Value(Value::Null) => return Some("unknown".to_owned()),
        Field::Value(value) => value.as_str()?,
        Field::Unknown => return None,
    };
    if platform == "linux" {
        return Some(match raw {
            "text" | "entry" | "password text" => "textField",
            "document text" | "document spreadsheet" | "document frame" | "document presentation" => "document",
            "document web" => "webArea",
            "spin button" => "incrementor",
            "combo box" => "comboBox",
            "push button" | "button" => "button",
            "push button menu" => "menuButton",
            "check box" => "checkBox",
            "radio button" => "radioButton",
            "menu item" | "check menu item" | "radio menu item" => "menuItem",
            "page tab" => "tab",
            "link" => "link",
            "frame" | "window" => "window",
            "table cell" => "cell",
            "panel" | "section" | "filler" | "viewport" | "embedded" | "grouping" => "group",
            "toggle button" | "scroll bar" | "slider" | "menu" | "menu bar" | "popup menu" | "label" | "static"
            | "heading" | "application" | "dialog" | "alert" | "file chooser" | "font chooser" | "color chooser"
            | "table" | "table row" | "row header" | "column header" | "table row header" | "table column header"
            | "list" | "list box" | "list item" | "tree" | "tree table" | "tree item" | "scroll pane"
            | "tool bar" | "page tab list" | "separator" | "image" | "icon" | "progress bar" => raw,
            _ => "unknown",
        }.to_owned());
    }
    let raw = raw.strip_prefix("ControlType.").unwrap_or(raw);
    Some(match raw {
        "Edit" => "textField".to_owned(),
        "Pane" | "Group" => "group".to_owned(),
        "DataItem" | "ListItem" | "TreeItem" => "row".to_owned(),
        "Tab" => "tabGroup".to_owned(),
        "TabItem" => "tab".to_owned(),
        "SplitButton" => "menuButton".to_owned(),
        "Spinner" => "incrementor".to_owned(),
        _ => super::role_name(raw),
    })
}

pub(super) fn input_container(platform: &str, node: &Value) -> Option<bool> {
    let values = node.get("values")?;
    let states = node.get("fieldStatus")?;
    let name = if platform == "windows" { "ControlType" } else { "role" };
    let raw = field(values, states, name);
    Some(matches!(&raw, Field::Value(Value::Text(_)))
        && matches!(role(platform, raw).as_deref(), Some("window" | "group" | "unknown"))
        && !(platform == "linux" && field(values, states, "editable").flag() == Some(true)))
}

fn click(platform: &str, role: Option<&str>, actions: &Value) -> bool {
    if actions.get("state").and_then(Value::as_str) != Some("complete") {
        return false;
    }
    let rows = actions.get("items").and_then(Value::as_seq).unwrap_or_default();
    let count = |name: &str| rows.iter().filter_map(|row| row.get("name").and_then(Value::as_str))
        .filter(|item| if platform == "linux" { item.eq_ignore_ascii_case(name) } else { *item == name }).count();
    if platform == "windows" {
        let names: &[&str] = match role {
            Some("comboBox" | "menuItem") => &["ExpandCollapse.Expand", "Invoke.Invoke", "Toggle.Toggle", "SelectionItem.Select"],
            Some("checkBox" | "switch") => &["Toggle.Toggle", "Invoke.Invoke", "SelectionItem.Select"],
            Some("row") => &["SelectionItem.Select", "Toggle.Toggle"],
            Some("tab" | "radioButton") => &["SelectionItem.Select", "Toggle.Toggle", "Invoke.Invoke"],
            Some("menuButton") => &["Invoke.Invoke", "Toggle.Toggle", "SelectionItem.Select", "ExpandCollapse.Expand"],
            _ => &["Invoke.Invoke", "Toggle.Toggle", "SelectionItem.Select"],
        };
        return names.iter().map(|name| count(name)).find(|count| *count > 0) == Some(1);
    }
    match role {
        Some("button" | "menuButton" | "menuItem" | "link" | "tab") => {
            ["click", "press", "activate"].into_iter().map(count).find(|count| *count > 0) == Some(1)
        }
        Some("checkBox" | "radioButton") => {
            ["click", "press", "toggle", "activate"].into_iter().map(count).find(|count| *count > 0) == Some(1)
        }
        Some("comboBox") => ["click", "press"].into_iter().map(count).find(|count| *count > 0) == Some(1),
        _ => false,
    }
}

pub(super) fn capabilities(platform: &str, node: &Value, features: &[String], scope: Option<&str>) -> Option<Capabilities> {
    use Support::{Available, Unavailable, Unknown};

    let values = node.get("values")?;
    let states = node.get("fieldStatus")?;
    let get = |name| field(values, states, name);
    let has = |name| features.iter().any(|feature| feature == name);
    let windows = platform == "windows";
    let enabled = if windows {
        get("IsEnabled").flag()
    } else {
        match (get("enabled").flag(), get("sensitive").flag()) {
            (Some(true), _) | (_, Some(true)) => Some(true),
            (Some(false), Some(false)) => Some(false),
            _ => None,
        }
    };
    let outside = windows && states.get("UIA.ReferenceScopeEvidence").is_none()
        && values.get("UIA.ReferenceScopeEvidence").is_some_and(|scope|
            reference_scope(scope) && scope.get("input").and_then(Value::as_str) == Some("observationOnly"));
    if scope == Some("applicationContent") || enabled == Some(false) || outside {
        return Some(Capabilities::all(Unavailable));
    }
    let role_field = if windows { "ControlType" } else { "role" };
    let name = role(platform, get(role_field));
    let role = name.as_deref();
    let editable = if windows { None } else { get("editable").flag() };
    let focusable = get(if windows { "IsKeyboardFocusable" } else { "focusable" }).flag() == Some(true);
    let read_only = get(if windows { "Value.IsReadOnly" } else { "readOnly" }).flag();
    let items = get(if windows { "SupportedPatterns" } else { "interfaces" });
    let interfaces = match items {
        Field::Value(value) => value.as_seq().filter(|items| items.len() <= 64 &&
            items.iter().all(|item| item.as_str().is_some_and(|item| !item.is_empty()))).unwrap_or_default(),
        _ => &[],
    };
    let uses = |item| interfaces.iter().any(|value| value.as_str() == Some(item));
    let editor = matches!(role, Some("textField" | "textArea" | "document" | "webArea" | "incrementor" | "comboBox" | "unknown"));
    let mut result = Capabilities::all(Unknown);
    if has("nativeActionIntents") && click(platform, role, node.get("actions")?) {
        result.click = Available;
    }
    if windows {
        let text_read_only = get("Text.IsReadOnly").flag();
        let can_focus = has("nativeKeyboardIntents") && focusable;
        let editing = editor && read_only != Some(true) && text_read_only != Some(true)
            && (uses("ValuePatternIdentifiers.Pattern") && read_only == Some(false)
                || uses("TextPatternIdentifiers.Pattern") && text_read_only == Some(false));
        if can_focus { result.focus = Available; }
        if has("nativeTextInput") && focusable && editing {
            result.type_text = Available;
            let selection = get("Text.SupportedTextSelection");
            if uses("TextPatternIdentifiers.Pattern") && matches!(selection, Field::Value(value)
                if matches!(value.as_str(), Some("Single" | "Multiple"))) {
                result.replace_text = Available;
            }
        }
        if text_read_only == Some(true) { result.type_text = Unavailable; }
        if read_only == Some(true) {
            result.type_text = Unavailable;
            result.replace_text = Unavailable;
        } else if read_only == Some(false) && uses("ValuePatternIdentifiers.Pattern") && has("nativeTextReplacement") {
            result.replace_text = Available;
        }
    } else {
        let composite = matches!(role, Some("textField" | "textArea" | "incrementor" | "comboBox" | "cell"));
        if editable == Some(false) && !composite {
            result.type_text = Unavailable;
            result.replace_text = Unavailable;
        }
        let component = uses("Component") && focusable;
        let keyboard = has("nativeKeyboardIntents") && component;
        let raw = values.get(role_field).and_then(Value::as_str);
        let editing = editor && editable == Some(true) && read_only != Some(true) && raw != Some("password text")
            && (uses("EditableText") || component && (uses("Text")
                || matches!(role, Some("textField" | "textArea" | "webArea" | "document" | "incrementor"))));
        if editing {
            let opaque_spin = role == Some("incrementor") && !uses("Text");
            if opaque_spin {
                result.type_text = Unavailable;
            } else if has("nativeTextInput") && (keyboard || uses("Text") && uses("EditableText")) {
                result.type_text = Available;
            }
            if has("nativeTextReplacement")
                && (uses("EditableText") || keyboard && opaque_spin && has("nativeElementPointer"))
            {
                result.replace_text = Available;
            }
        }
        if keyboard { result.focus = Available; }
        if read_only == Some(true) {
            result.type_text = Unavailable;
            result.replace_text = Unavailable;
        }
    }
    Some(result)
}
