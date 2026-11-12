// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Interaction scope and fact-backed control composition before compression.

use super::{field, put, remove, sequence, text, truthy, Fields, ProjectionError, Result, Value};

const LAYOUT_ROLES: [&str; 3] = ["group", "unknown", "cell"];
const LABEL_ROLES: [&str; 2] = ["staticText", "heading"];
const CONTROL_ROLES: [&str; 7] = ["button", "checkBox", "radioButton", "switch", "link", "tab", "group"];
const EDITOR_ROLES: [&str; 3] = ["textField", "textArea", "comboBox"];
const CAPABILITIES: [&str; 4] = ["click", "typeText", "replaceText", "focus"];

#[derive(Clone)]
pub(super) struct SemanticNode {
    pub(super) fields: Fields,
    pub(super) children: Vec<usize>,
    pub(super) children_index: usize,
}

/// The intermediate tree owns child ordinals, so traversal, cloning and
/// cleanup use bounded stack space even for a deep provider hierarchy.
#[derive(Clone)]
pub(super) struct SemanticTree {
    pub(super) nodes: Vec<SemanticNode>,
    pub(super) depth: usize,
}

impl SemanticTree {
    pub(super) fn parse(root: &Value) -> Result<Self> {
        let mut nodes: Vec<SemanticNode> = Vec::new();
        let mut depth = 0;
        let mut pending = vec![(root, None::<usize>, 1)];
        while let Some((value, parent, level)) = pending.pop() {
            depth = depth.max(level);
            let Value::Map(record) = value else {
                return Err(ProjectionError::Invalid("semantic node must be a record"));
            };
            let position = record.iter().position(|(name, _)| name == "children")
                .ok_or(ProjectionError::Invalid("semantic node has no children field"))?;
            let Value::Seq(children) = &record[position].1 else {
                return Err(ProjectionError::Invalid("semantic children must be ordered"));
            };
            for key in ["ref", "role"] {
                let value = field(record, key).ok_or(ProjectionError::MissingField(key))?;
                if text(value).is_none() {
                    return Err(ProjectionError::Invalid("semantic identity and role must be strings"));
                }
            }
            let index = nodes.len();
            let fields = record[..position].iter().chain(&record[position + 1..]).cloned().collect();
            if let Some(parent) = parent {
                nodes[parent].children.push(index);
            }
            nodes.push(SemanticNode { fields, children: Vec::with_capacity(children.len()), children_index: position });
            pending.extend(children.iter().rev().map(|child| (child, Some(index), level + 1)));
        }
        Ok(Self { nodes, depth })
    }
}

impl SemanticNode {
    fn field(&self, name: &str) -> Option<&Value> {
        field(&self.fields, name)
    }

    pub(super) fn reference(&self) -> &str {
        self.field("ref").and_then(text).expect("parsed node reference")
    }

    pub(super) fn role(&self) -> &str {
        self.field("role").and_then(text).expect("parsed node role")
    }

    fn truth(&self, name: &str) -> bool {
        self.field(name).is_some_and(truthy)
    }

    fn explicit_false(&self, name: &str) -> bool {
        matches!(self.field(name), Some(Value::Bool(false)))
    }

    fn blocked(&self) -> Result<Vec<String>> {
        refs(self.field("blockedBy"))
    }
}

fn refs(value: Option<&Value>) -> Result<Vec<String>> {
    let Some(value) = value else { return Ok(Vec::new()) };
    let list = sequence(value).ok_or(ProjectionError::Invalid("semantic reference list must be ordered"))?;
    list.iter()
        .map(|item| text(item).map(str::to_owned).ok_or(ProjectionError::Invalid("expected a semantic reference")))
        .collect()
}

fn strings(values: impl IntoIterator<Item = String>) -> Value {
    Value::Seq(values.into_iter().map(Value::Text).collect())
}

fn is_window(node: &SemanticNode) -> bool {
    matches!(node.role(), "window" | "sheet")
}

fn active_modal(node: &SemanticNode) -> bool {
    node.truth("modal") && !(node.explicit_false("visible") && node.explicit_false("showing"))
}

fn modals(tree: &SemanticTree, children: &[usize], output: &mut Vec<String>) {
    let mut pending: Vec<_> = children.iter().rev().copied().collect();
    while let Some(index) = pending.pop() {
        let node = &tree.nodes[index];
        if active_modal(node) {
            output.push(node.reference().to_owned());
        } else if !is_window(node) {
            pending.extend(node.children.iter().rev().copied());
        }
    }
}

pub(super) fn annotate_interactions(tree: &mut SemanticTree, inherited: &[String]) -> Result<()> {
    let mut pending = vec![(0, inherited.to_vec())];
    while let Some((index, inherited)) = pending.pop() {
        let node = &tree.nodes[index];
        let mut blocked = if is_window(node) || active_modal(node) {
            let mut owned = refs(node.field("ownedModals"))?;
            modals(tree, &node.children, &mut owned);
            owned
        } else {
            Vec::new()
        };
        if node.role() != "window" && !inherited.iter().any(|item| item == node.reference()) {
            blocked.extend(inherited);
        }
        blocked.sort_unstable();
        blocked.dedup();
        let node = &mut tree.nodes[index];
        if !blocked.is_empty() && node.field("capabilities").is_some() {
            let values = CAPABILITIES
                .iter()
                .map(|key| (key.to_string(), Value::Text("unavailable".to_owned())))
                .collect();
            put(&mut node.fields, "capabilities", Value::Map(values));
        }
        put(&mut node.fields, "blockedBy", strings(blocked.iter().cloned()));
        pending.extend(node.children.iter().rev().map(|&child| (child, blocked.clone())));
    }
    Ok(())
}

fn contains_modal(tree: &SemanticTree, index: usize, blocked: &[String]) -> bool {
    let mut pending = vec![index];
    while let Some(index) = pending.pop() {
        let node = &tree.nodes[index];
        if blocked.iter().any(|item| item == node.reference()) {
            return true;
        }
        pending.extend(node.children.iter().rev().copied());
    }
    false
}

pub(super) fn scope_interactions(tree: &mut SemanticTree) -> Result<()> {
    let mut pending = vec![0];
    while let Some(index) = pending.pop() {
        let node = &tree.nodes[index];
        let blocked = node.blocked()?;
        if !blocked.is_empty() {
            let keep: Vec<_> = node.children.iter().map(|&child| contains_modal(tree, child, &blocked)).collect();
            if keep.contains(&true) && keep.contains(&false) {
                let node = &mut tree.nodes[index];
                let mut keep = keep.into_iter();
                node.children.retain(|_| keep.next().expect("flag for each child"));
                put(
                    &mut node.fields,
                    "partialReason",
                    Value::Text(format!("background blocked by {}", blocked.join(", "))),
                );
                remove(&mut node.fields, "childrenRead");
                remove(&mut node.fields, "childCount");
            }
        }
        pending.extend(tree.nodes[index].children.iter().rev().copied());
    }
    Ok(())
}

fn compare_number(left: &Value, right: &Value) -> Result<bool> {
    match (left, right) {
        (Value::Int(left), Value::Int(right)) => Ok(left < right),
        (Value::Bool(left), right) => compare_number(&Value::Int(i64::from(*left)), right),
        (left, Value::Bool(right)) => compare_number(left, &Value::Int(i64::from(*right))),
        _ => Err(ProjectionError::Invalid("semantic child coverage must use integer counts")),
    }
}

fn incomplete(node: &SemanticNode) -> Result<bool> {
    if ["partialReason", "nextChildOffset", "childrenStart", "childrenEnd"]
        .iter()
        .any(|field| node.field(field).is_some())
        || node.truth("childOmissions")
    {
        return Ok(true);
    }
    let Some(total) = node.field("childCount") else { return Ok(false) };
    compare_number(node.field("childrenRead").unwrap_or(&Value::Int(node.children.len() as i64)), total)
}

fn action_kind(value: &Value) -> Result<&str> {
    let Value::Map(action) = value else {
        return Err(ProjectionError::Invalid("semantic action must be a record"));
    };
    let name = field(action, "name").ok_or(ProjectionError::Invalid("semantic action has no name"))?;
    text(field(action, "kind").unwrap_or(name)).ok_or(ProjectionError::Invalid("semantic action kind must be text"))
}

fn has_actions(node: &SemanticNode, test: impl Fn(&str) -> bool) -> Result<bool> {
    let Some(actions) = node.field("actions") else { return Ok(false) };
    for action in sequence(actions).ok_or(ProjectionError::Invalid("semantic actions must be ordered"))? {
        if test(action_kind(action)?) {
            return Ok(true);
        }
    }
    Ok(false)
}

fn capability(node: &SemanticNode, name: &str) -> Result<bool> {
    Ok(match node.field("capabilities") {
        None => false,
        Some(Value::Map(values)) => field(values, name).and_then(text) == Some("available"),
        _ => return Err(ProjectionError::Invalid("semantic capabilities must be a record")),
    })
}

fn control(kind: &str, members: Vec<(String, &'static str)>) -> Value {
    let members = members.into_iter().map(|(reference, part)| {
        Value::Map(vec![("ref".to_owned(), Value::Text(reference)), ("part".to_owned(), Value::Text(part.to_owned()))])
    });
    Value::Map(vec![
        ("kind".to_owned(), Value::Text(kind.to_owned())),
        ("members".to_owned(), Value::Seq(members.collect())),
    ])
}

fn transparent(node: &SemanticNode) -> Result<bool> {
    Ok(LAYOUT_ROLES.contains(&node.role())
        && !node.truth("name")
        && !node.truth("description")
        && (matches!(node.field("value"), None | Some(Value::Null)) || node.field("value").and_then(text) == Some(""))
        && !node.truth("pressable")
        && !node.truth("selectable")
        && !has_actions(node, |kind| !matches!(kind, "show_menu" | "scroll_into_view"))?
        && !node.truth("actionsUnavailable")
        && node.field("control").is_none())
}

fn editor_path(
    tree: &SemanticTree,
    index: usize,
    editor: &str,
    owner_blocked: &[String],
    depth: usize,
    visited: &mut usize,
    path: &mut Vec<usize>,
) -> Result<bool> {
    *visited += 1;
    let node = &tree.nodes[index];
    if *visited > 64 || depth > 8 || incomplete(node)? {
        return Ok(false);
    }
    if node.truth("modal") || node.blocked()? != owner_blocked {
        return Ok(false);
    }
    if node.reference() == editor {
        return Ok(EDITOR_ROLES.contains(&node.role()));
    }
    if !LAYOUT_ROLES.contains(&node.role()) || node.truth("name") || node.truth("description") || node.field("control").is_some() {
        return Ok(false);
    }
    for &child in &node.children {
        path.push(child);
        if editor_path(tree, child, editor, owner_blocked, depth + 1, visited, path)? {
            return Ok(true);
        }
        path.pop();
    }
    Ok(false)
}

fn compose_editor(tree: &mut SemanticTree, index: usize, editor: &str) -> Result<bool> {
    let node = &tree.nodes[index];
    let blocked = node.blocked()?;
    let mut visited = 0;
    let mut path = Vec::new();
    for &child in &node.children {
        path.push(child);
        if editor_path(tree, child, editor, &blocked, 1, &mut visited, &mut path)? {
            break;
        }
        path.pop();
    }
    let Some(&last) = path.last() else {
        return Ok(false);
    };
    let caps = node.field("capabilities").cloned();
    let mut members = Vec::with_capacity(path.len());
    for child in path {
        let target = &tree.nodes[child];
        let kind = if target.reference() == editor { "editor" } else { "layout" };
        members.push((target.reference().to_owned(), kind));
    }
    let target = &mut tree.nodes[last];
    if !target.explicit_false("enabled") && !target.truth("blockedBy") {
        if let (Some(Value::Map(owner_caps)), Some(Value::Map(child_caps))) = (caps, target.field("capabilities")) {
            let mut derived = child_caps.clone();
            for key in ["typeText", "replaceText", "focus"] {
                let value = field(&owner_caps, key).ok_or(ProjectionError::Invalid("incomplete owner capabilities"))?;
                put(&mut derived, key, value.clone());
            }
            if !has_actions(target, |kind| matches!(kind, "invoke" | "select" | "toggle"))? {
                let focus = field(&owner_caps, "focus").expect("checked focus capability");
                put(&mut derived, "click", focus.clone());
            }
            put(&mut target.fields, "capabilities", Value::Map(derived));
        }
    }
    put(&mut tree.nodes[index].fields, "control", control("textInput", members));
    Ok(true)
}

struct Members<'a> {
    owner: &'a str,
    blocked: Vec<String>,
    items: Vec<(String, &'static str)>,
    visited: usize,
    cells: usize,
    complete: bool,
}

impl Members<'_> {
    fn collect(&mut self, tree: &SemanticTree, index: usize, depth: usize) -> Result<()> {
        self.visited += 1;
        if self.visited > 64 || depth > 8 {
            self.complete = false;
            return Ok(());
        }
        let child = &tree.nodes[index];
        self.cells += usize::from(child.role() == "cell");
        if incomplete(child)? || child.truth("incompleteLabelRelation") || child.truth("modal") {
            return Ok(());
        }
        if child.blocked()? != self.blocked || child.field("toggleState").is_some() {
            return Ok(());
        }
        if LABEL_ROLES.contains(&child.role()) && child.children.is_empty() {
            let targets = refs(child.field("labelTargets"))?;
            if targets.iter().all(|target| target == self.owner || self.items.iter().any(|(ref_, _)| ref_ == target)) {
                self.items.push((child.reference().to_owned(), "label"));
            }
        } else if transparent(child)? {
            self.items.push((child.reference().to_owned(), "layout"));
            for &nested in &child.children {
                self.collect(tree, nested, depth + 1)?;
            }
        }
        Ok(())
    }
}

pub(super) fn compose_controls(tree: &mut SemanticTree) -> Result<()> {
    enum Visit {
        Enter(usize, bool),
        Leave(usize, bool),
    }
    let mut pending = vec![Visit::Enter(0, false)];
    while let Some(visit) = pending.pop() {
        match visit {
            Visit::Enter(index, parent_table) => {
                let node = &tree.nodes[index];
                let in_table = node.role() == "table" || (parent_table && node.role() != "outline");
                pending.push(Visit::Leave(index, in_table));
                pending.extend(node.children.iter().rev().map(|&child| Visit::Enter(child, in_table)));
            }
            Visit::Leave(index, in_table) => compose_control(tree, index, in_table)?,
        }
    }
    Ok(())
}

fn compose_control(tree: &mut SemanticTree, index: usize, in_table: bool) -> Result<()> {
    let node = &tree.nodes[index];
    if node.field("control").is_some() || incomplete(node)? {
        return Ok(());
    }
    if let Some(editor) = node.field("editorRef").and_then(text).filter(|editor| *editor != node.reference()).map(str::to_owned) {
        if compose_editor(tree, index, &editor)? {
            return Ok(());
        }
    }
    let node = &tree.nodes[index];
    let clickable = capability(node, "click")?;
    let item = node.role() == "row" && !in_table;
    if (!clickable && !item) || node.explicit_false("enabled") || node.truth("blockedBy") {
        return Ok(());
    }
    if !CONTROL_ROLES.contains(&node.role()) && !item {
        return Ok(());
    }
    let mut members = Members {
        owner: node.reference(),
        blocked: node.blocked()?,
        items: Vec::new(),
        visited: 0,
        cells: 0,
        complete: true,
    };
    for &child in &node.children {
        members.collect(tree, child, 1)?;
    }
    if members.complete && !members.items.is_empty() && (node.role() != "row" || members.cells <= 1) {
        let kind = if node.role() == "row" { "item" } else { "button" };
        let value = control(kind, members.items);
        put(&mut tree.nodes[index].fields, "control", value);
    }
    Ok(())
}

fn zero(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(value)) => !value,
        Some(Value::Int(value)) => *value == 0,
        Some(Value::Float(value)) => *value == 0.0,
        Some(Value::BigInt(value)) => value == "0",
        _ => false,
    }
}

pub(super) fn display_copy(node: &mut SemanticNode) -> Result<()> {
    put(&mut node.fields, "_factBacked", Value::Bool(true));
    let editing = matches!(node.role(), "textField" | "textArea" | "comboBox" | "document")
        || capability(node, "typeText")?
        || capability(node, "replaceText")?;
    if !editing && !node.truth("focused") && !node.truth("selectedText") {
        if let Some(Value::Map(range)) = node.field("selectionRange") {
            if range.len() == 2 && zero(field(range, "location")) && zero(field(range, "length")) {
                remove(&mut node.fields, "selectionRange");
            }
        }
    }
    if zero(node.field("disclosureLevel"))
        && !matches!(node.role(), "outline" | "row" | "disclosureTriangle")
        && !node.truth("disclosedBy")
        && !node.truth("expanded")
        && !node.truth("disclosing")
    {
        remove(&mut node.fields, "disclosureLevel");
    }
    Ok(())
}
