// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Operation capability evidence retained alongside the observed facts.

mod portable;

use super::value::Value;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Support {
    Unknown,
    Unavailable,
    Available,
}

impl Support {
    fn name(self) -> &'static str {
        match self {
            Self::Unknown => "unknown",
            Self::Unavailable => "unavailable",
            Self::Available => "available",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Capabilities {
    click: Support,
    type_text: Support,
    replace_text: Support,
    focus: Support,
}

impl Capabilities {
    fn all(state: Support) -> Self {
        Self { click: state, type_text: state, replace_text: state, focus: state }
    }

    fn record(self) -> Value {
        Value::Map([
            ("click", self.click), ("typeText", self.type_text),
            ("replaceText", self.replace_text), ("focus", self.focus),
        ].into_iter().map(|(name, state)| (name.to_owned(), Value::Text(state.name().into()))).collect())
    }
}

enum Field<'a> {
    Unknown,
    Absent,
    Value(&'a Value),
}

impl Field<'_> {
    fn flag(&self) -> Option<bool> {
        if let Self::Value(value) = self { value.as_bool() } else { None }
    }
}

fn field<'a>(values: &'a Value, statuses: &Value, key: &str) -> Field<'a> {
    match statuses.get(key).and_then(|status| status.get("state")).and_then(Value::as_str) {
        Some("absent" | "unsupported") => Field::Absent,
        Some(_) => Field::Unknown,
        None => values.get(key).map_or(Field::Unknown, Field::Value),
    }
}

fn role_name(raw: &str) -> String {
    let mut source = raw.strip_prefix("AX").unwrap_or(raw).chars();
    let Some(first) = source.next() else { return "unknown".into() };
    let name: String = first.to_lowercase().chain(source).collect();
    if name.as_bytes()[0].is_ascii_lowercase() && name.bytes().all(|c| c.is_ascii_alphanumeric()) {
        name
    } else {
        "unknown".into()
    }
}

fn observed_capabilities(node: &Value, features: &[String], scope_kind: Option<&str>) -> Option<Capabilities> {
    use Support::{Available, Unavailable, Unknown};

    if scope_kind == Some("applicationContent") {
        return Some(Capabilities::all(Unavailable));
    }
    let values = node.get("values")?;
    let statuses = node.get("fieldStatus")?;
    let enabled = field(values, statuses, "AXEnabled").flag();
    if enabled == Some(false) {
        return Some(Capabilities::all(Unavailable));
    }

    let editable = field(values, statuses, "AXEditable").flag();
    let focusable = field(values, statuses, "settable.AXFocused").flag() == Some(true);
    let role = field(values, statuses, "AXRole");
    let raw_role = match role {
        Field::Value(value) => value.as_str(),
        _ => None,
    };
    let role = match role {
        Field::Absent | Field::Value(Value::Null) => Some("unknown".to_owned()),
        Field::Value(value) => value.as_str().map(role_name),
        Field::Unknown => None,
    };
    let role = role.as_deref();
    let actions = node.get("actions")?;
    let names = actions.get("items")?.as_seq()?;
    let action_count = |name: &str| names.iter().filter(|action| {
        action.get("name").and_then(Value::as_str) == Some(name)
    }).count();
    let complete = actions.get("state").and_then(Value::as_str) == Some("complete");
    let has = |feature: &str| features.iter().any(|known| known == feature);
    let menu = matches!(raw_role, Some("AXMenuBar" | "AXMenuBarItem" | "AXMenuItem" | "AXMenu"));
    let press = action_count("AXPress") != 0;
    let can_click = has("nativeActionIntents")
        && (!menu || scope_kind == Some("applicationMenu") && has("nativeMenuActions") && press)
        && !matches!(raw_role, Some("AXTextField" | "AXCell"))
        && (press
            || complete && raw_role == Some("AXMenuItem") && action_count("AXPick") == 1
            || complete && matches!(raw_role, Some("AXPopUpButton" | "AXMenuButton" | "AXComboBox"))
                && action_count("AXShowMenu") != 0);

    let text_field = matches!(role, Some("textField" | "textArea"));
    let document = matches!(role, Some("textArea" | "webArea" | "document"));
    let editor = document || text_field || matches!(role, Some("comboBox" | "incrementor" | "unknown"));
    let writable = editable == Some(true)
        || field(values, statuses, "settable.AXValue").flag() == Some(true)
        || field(values, statuses, "settable.AXSelectedText").flag() == Some(true);
    let type_text = if editable == Some(false) {
        Unavailable
    } else if has("nativeTextInput") && has("nativeActuators")
        && (editor && writable && (document || focusable) || text_field && focusable)
    {
        Available
    } else {
        Unknown
    };
    Some(Capabilities {
        click: if can_click { Available } else { Unknown },
        type_text,
        replace_text: Unknown,
        focus: if has("nativeKeyboardIntents") && focusable { Available } else { Unknown },
    })
}

pub(crate) fn operation_capabilities(
    platform: &str, rows: &[Value], features: &[String], scope_kind: Option<&str>,
) -> Option<Value> {
    if platform == "macos" {
        return macos_capabilities(rows, features, scope_kind);
    }
    let mut output = Vec::with_capacity(rows.len());
    for row in rows {
        let node = row.get("node")?.as_str()?;
        let capabilities = portable::capabilities(platform, row, features, scope_kind)?;
        output.push((node.to_owned(), capabilities.record()));
    }
    Some(Value::Map(output))
}

pub(crate) fn input_containers(platform: &str, rows: &[Value]) -> Option<Value> {
    if platform == "macos" {
        return macos_input_containers(rows);
    }
    let mut output = Vec::with_capacity(rows.len());
    for row in rows {
        let node = row.get("node")?.as_str()?;
        output.push((node.to_owned(), Value::Bool(portable::input_container(platform, row)?)));
    }
    Some(Value::Map(output))
}

pub(crate) fn macos_capabilities(query_rows: &[Value], features: &[String], scope_kind: Option<&str>) -> Option<Value> {
    let mut rows = Vec::with_capacity(query_rows.len());
    for node in query_rows {
        let token = node.get("node")?.as_str()?;
        rows.push((token.to_owned(), observed_capabilities(node, features, scope_kind)?.record()));
    }
    Some(Value::Map(rows))
}

pub(crate) fn macos_input_containers(query_rows: &[Value]) -> Option<Value> {
    let mut rows = Vec::with_capacity(query_rows.len());
    for node in query_rows {
        let token = node.get("node")?.as_str()?;
        let values = node.get("values")?;
        let statuses = node.get("fieldStatus")?;
        let raw_role = field(values, statuses, "AXRole");
        let input_container = if let Field::Value(Value::Text(role)) = raw_role {
            matches!(role_name(role).as_str(), "group" | "unknown" | "window")
                && !["AXEditable", "settable.AXValue", "settable.AXSelectedText"]
                    .into_iter().any(|name| field(values, statuses, name).flag() == Some(true))
        } else {
            false
        };
        rows.push((token.to_owned(), Value::Bool(input_container)));
    }
    Some(Value::Map(rows))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::decode_json;

    fn row(raw: &str) -> Value {
        decode_json(raw.as_bytes()).unwrap()
    }

    #[test]
    fn capability_status_restricts_display_without_guessing_an_input_route() {
        let features = ["nativeActionIntents", "nativeActuators", "nativeTextInput", "nativeKeyboardIntents"];
        let features = features.map(str::to_owned);
        let field = row(r#"{
            "node":"f", "values":{"AXRole":"AXTextField","settable.AXFocused":true},
            "fieldStatus":{}, "actions":{"state":"complete","items":[{"name":"AXPress"}]}
        }"#);
        let caps = macos_capabilities(&[field.clone()], &features, Some("window")).unwrap();
        assert_eq!(caps.get("f"), Some(&row(r#"{"click":"unknown","typeText":"available","replaceText":"unknown","focus":"available"}"#)));
        let caps = macos_capabilities(&[field], &features, Some("applicationContent")).unwrap();
        assert_eq!(caps.get("f"), Some(&Capabilities::all(Support::Unavailable).record()));
    }

    #[test]
    fn a_partial_menu_catalogue_and_a_known_menu_action_remain_distinct() {
        let features = ["nativeActionIntents", "nativeMenuActions"].map(str::to_owned);
        let item = row(r#"{
            "node":"item", "values":{"AXRole":"AXMenuItem"}, "fieldStatus":{},
            "actions":{"state":"partial","items":[{"name":"AXPress"}]}
        }"#);
        assert_eq!(observed_capabilities(&item, &features, Some("window")).unwrap().click, Support::Unknown);
        assert_eq!(observed_capabilities(&item, &features, Some("applicationMenu")).unwrap().click, Support::Available);
    }
}
