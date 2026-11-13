// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Atomic source-backed fields selected before geometric layout or text generation.

use std::collections::{HashMap, HashSet};

use super::super::geometric::{AtomLayoutEvidence, AtomSlot as LayoutSlot};
use super::super::numbering::NodeId;
use super::super::projection::{FactKey, Projection, ProjectionNode};
use super::super::value::Value;
use super::lines::{
    capabilities, click_deferred, embedded, reference_set, role, role_selection, secondary_actions, standard_click,
    state_labels, text_items, value_label, Fields,
};
use super::text::{json, JsonFormat};
use super::{field as scalar_field, quoted, RenderedNode, TextError};

mod finalize;
pub(crate) mod policy;
pub(crate) use finalize::{finalize_atoms, plan_urls, AtomFinalizeOptions, UrlCaption};

type Result<T> = std::result::Result<T, TextError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Format {
    Text,
    Literal,
    Number,
    Span,
    Selection,
    References,
    Toggle,
    Unavailable,
    Preview,
}

impl Format {
    pub(crate) fn name(self) -> &'static str {
        match self {
            Self::Text => "text",
            Self::Literal => "literal",
            Self::Number => "number",
            Self::Span => "span",
            Self::Selection => "selection",
            Self::References => "refs",
            Self::Toggle => "toggle",
            Self::Unavailable => "unavailable",
            Self::Preview => "preview",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Placement {
    Caption,
    Inline,
    Block,
}

#[derive(Clone, Debug)]
enum Preview {
    Carrier(String, usize),
    EmbeddedUrl(String),
    Url(String),
}

#[derive(Clone, Debug)]
pub(crate) struct AtomFact {
    pub(crate) key: FactKey,
    pub(crate) value: Value,
}

#[derive(Clone, Debug)]
pub(crate) struct Slot {
    pub(crate) key: String,
    pub(crate) label: Option<&'static str>,
    pub(crate) value: Value,
    pub(crate) format: Format,
    pub(crate) placement: Placement,
    pub(crate) facts: Vec<AtomFact>,
    pub(crate) protection: Option<&'static str>,
    preview: Option<Preview>,
}

impl Slot {
    pub(crate) fn has_preview(&self) -> bool {
        self.preview.is_some()
    }
}

#[derive(Clone, Debug)]
pub(crate) struct Action {
    pub(crate) source: NodeId,
    pub(crate) reference: String,
    pub(crate) name: String,
    pub(crate) label: String,
    pub(crate) ambiguous: bool,
    pub(crate) catalogue_index: usize,
    pub(crate) record: Value,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct Coverage {
    pub(crate) reason: Option<String>,
    pub(crate) read: Option<i64>,
    pub(crate) total: Option<i64>,
    pub(crate) start: Option<i64>,
    pub(crate) end: Option<i64>,
    pub(crate) next: Option<i64>,
    pub(crate) omissions: Vec<String>,
    pub(crate) unexpanded_menu: bool,
    pub(crate) enumeration_unsupported: bool,
}

impl Coverage {
    pub(crate) fn partial(&self) -> bool {
        self.reason.is_some()
            || self.next.is_some()
            || !self.omissions.is_empty()
            || self
                .read
                .zip(self.total)
                .is_some_and(|(read, total)| read < total && self.end != Some(total))
    }
}

#[derive(Clone, Debug)]
pub(crate) struct Declaration {
    pub(crate) attribute: String,
    pub(crate) value: Value,
    pub(crate) facts: Vec<AtomFact>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DispositionKind {
    Inline,
    Equivalent,
    Details,
}

#[derive(Clone, Debug)]
pub(crate) struct Disposition {
    pub(crate) fact: AtomFact,
    pub(crate) target: NodeId,
    pub(crate) slot: String,
    pub(crate) kind: DispositionKind,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ClaimKind {
    Display,
    Equivalent,
    Shared,
    Continuation,
    TextSelectionSummary,
}

#[derive(Clone, Debug)]
pub(crate) struct Claim {
    pub(crate) fact: FactKey,
    pub(crate) value: Value,
    pub(crate) target: NodeId,
    pub(crate) kind: ClaimKind,
}

#[derive(Clone, Debug)]
pub(crate) struct Atom {
    pub(crate) id: NodeId,
    pub(crate) reference: String,
    pub(crate) parent: Option<NodeId>,
    pub(crate) role: String,
    pub(crate) source_description: bool,
    pub(crate) states: Vec<String>,
    pub(crate) slots: Vec<Slot>,
    pub(crate) actions: Vec<Action>,
    pub(crate) actions_unavailable: bool,
    pub(crate) coverage: Coverage,
    pub(crate) declarations: Vec<Declaration>,
    pub(crate) dispositions: Vec<Disposition>,
    pub(crate) claims: Vec<Claim>,
}

#[derive(Default)]
pub(crate) struct AtomPlan {
    pub(crate) atoms: HashMap<NodeId, Atom>,
    pub(crate) roots: Vec<NodeId>,
    pub(crate) order: Vec<NodeId>,
    pub(crate) focused: Option<NodeId>,
    pub(crate) selected: Vec<NodeId>,
    pub(crate) shared_index_basis: bool,
    pub(crate) source_references: HashMap<NodeId, String>,
    pub(crate) deferred_native_urls: Vec<Value>,
    pub(crate) deferred_native_url_peers: Vec<Value>,
}

pub(crate) struct RoleAlias {
    pub(crate) role: String,
    pub(crate) subrole: Option<String>,
    pub(crate) description: String,
    pub(crate) label: String,
}

#[derive(Default)]
pub(crate) struct AtomOptions<'a> {
    pub(crate) compact: bool,
    pub(crate) captured_details: bool,
    pub(crate) focused: Option<NodeId>,
    pub(crate) deferred: Option<&'a HashSet<FactKey>>,
    pub(crate) role_aliases: Option<&'a HashMap<NodeId, RoleAlias>>,
    pub(crate) suppressed_actions: Option<&'a HashSet<(NodeId, usize)>>,
    pub(crate) summarized_selection: Option<&'a HashSet<NodeId>>,
}

fn value_text(text: &str) -> Value {
    Value::Text(text.to_owned())
}

fn one_ref(reference: &str) -> Value {
    Value::Seq(vec![value_text(reference)])
}

fn collection_index(projection: &Projection, node: &ProjectionNode) -> bool {
    let mut seen = HashSet::new();
    let mut next = Some(node.id);
    while let Some(id) = next {
        let Some(node) = projection.node(id).filter(|_| seen.insert(id)) else {
            return true;
        };
        let native = &node.native_fields;
        if matches!(
            role(native),
            "list" | "outline" | "table" | "row" | "cell" | "column" | "menu" | "menuBar" | "menuItem" | "menuBarItem"
        ) || [
            "rowRange",
            "columnRange",
            "rowCount",
            "columnCount",
            "disclosureLevel",
            "disclosedBy",
        ]
        .into_iter()
        .any(|key| native.value(key).is_some())
        {
            return true;
        }
        next = node.parent;
    }
    false
}

struct AtomBuilder<'a> {
    node: &'a ProjectionNode,
    options: &'a AtomOptions<'a>,
    slots: Vec<Slot>,
    dispositions: Vec<Disposition>,
    claims: Vec<Claim>,
}

fn source_facts(node: &ProjectionNode, attribute: &str) -> Vec<AtomFact> {
    if let Some((_, value)) = node.provenance.iter().find(|(key, _)| key == attribute) {
        return value
            .parts
            .iter()
            .flat_map(|part| {
                part.facts.iter().map(|key| AtomFact {
                    key: key.clone(),
                    value: part.value.clone(),
                })
            })
            .collect();
    }
    node.native_fields
        .value(attribute)
        .map(|value| AtomFact {
            key: FactKey {
                source: node.id,
                attribute: attribute.to_owned(),
            },
            value: value.clone(),
        })
        .into_iter()
        .collect()
}

impl AtomBuilder<'_> {
    fn mark(&mut self, facts: impl IntoIterator<Item = AtomFact>, slot: &str, target: NodeId, kind: DispositionKind) {
        self.dispositions.extend(facts.into_iter().map(|fact| Disposition {
            fact,
            target,
            slot: slot.to_owned(),
            kind,
        }));
    }

    fn claim(&mut self, node: &ProjectionNode, attribute: &str, target: NodeId, kind: ClaimKind) {
        let Some(value) = node.field(attribute) else { return };
        let sources: Vec<_> = if matches!(attribute, "name" | "description" | "value") {
            node.text_sources
                .iter()
                .filter(|part| part.field == attribute)
                .collect()
        } else {
            Vec::new()
        };
        if sources.is_empty() {
            self.claims.push(Claim {
                fact: FactKey {
                    source: node.id,
                    attribute: attribute.to_owned(),
                },
                value: value.clone(),
                target,
                kind,
            });
        } else {
            self.claims.extend(sources.into_iter().map(|part| Claim {
                fact: part.fact.clone(),
                value: part.text.clone(),
                target,
                kind: ClaimKind::Equivalent,
            }));
        }
    }

    fn defer(&mut self, attribute: &str) -> bool {
        let key = FactKey {
            source: self.node.id,
            attribute: attribute.to_owned(),
        };
        if self.options.captured_details && self.options.deferred.is_some_and(|fields| fields.contains(&key)) {
            self.defer_known(attribute);
            true
        } else {
            false
        }
    }

    fn defer_known(&mut self, attribute: &str) {
        self.mark(
            source_facts(self.node, attribute),
            attribute,
            self.node.id,
            DispositionKind::Details,
        );
    }

    fn equivalent(&mut self, attribute: &str, slot: &str, target: Option<NodeId>) {
        let details = target.is_none()
            && self
                .slots
                .iter()
                .any(|candidate| candidate.key == slot && candidate.preview.is_some());
        let target = target.unwrap_or(self.node.id);
        self.mark(
            source_facts(self.node, attribute),
            slot,
            target,
            if details {
                DispositionKind::Details
            } else {
                DispositionKind::Equivalent
            },
        );
        if !details {
            self.claim(self.node, attribute, target, ClaimKind::Equivalent);
        }
    }

    fn add(
        &mut self,
        key: &str,
        label: Option<&'static str>,
        value: Value,
        format: Format,
        placement: Placement,
        protection: Option<&'static str>,
        attributes: &[&str],
    ) -> Result<()> {
        let attributes = if attributes.is_empty() { &[key] } else { attributes };
        let facts: Vec<_> = attributes
            .iter()
            .flat_map(|attribute| source_facts(self.node, attribute))
            .collect();
        let mut preview = None;
        if self.options.compact && self.options.captured_details && protection.is_none() {
            if let Some(value) = value.as_str() {
                if let Some((header, count)) = embedded(value) {
                    preview = Some(Preview::Carrier(header.to_owned(), count));
                } else if key == "url" {
                    if let Some((scheme, tail)) = value.split_once(':').filter(|(scheme, _)| {
                        scheme.eq_ignore_ascii_case("data") || scheme.eq_ignore_ascii_case("webdoc")
                    }) {
                        let kind: String = if scheme.eq_ignore_ascii_case("data") {
                            tail.split(',').next().unwrap_or_default().chars().take(60).collect()
                        } else {
                            String::new()
                        };
                        preview = Some(Preview::EmbeddedUrl(format!("{scheme}:{kind}")));
                    } else {
                        let head: String = value.chars().take(160).collect();
                        if head.len() < value.len() {
                            preview = Some(Preview::Url(head));
                        }
                    }
                }
            }
        }
        let format = if preview.is_some() { Format::Preview } else { format };
        let placement = if preview.is_some() && placement == Placement::Block {
            Placement::Inline
        } else {
            placement
        };
        self.mark(
            facts.iter().cloned(),
            key,
            self.node.id,
            if preview.is_some() {
                DispositionKind::Details
            } else {
                DispositionKind::Inline
            },
        );
        if preview.is_none() {
            for attribute in attributes {
                self.claim(self.node, attribute, self.node.id, ClaimKind::Display);
            }
        }
        self.slots.push(Slot {
            key: key.to_owned(),
            label,
            value,
            format,
            placement,
            protection,
            preview,
            facts,
        });
        Ok(())
    }

    fn ordinary(
        &mut self,
        key: &str,
        label: &'static str,
        format: Format,
        protection: Option<&'static str>,
    ) -> Result<()> {
        if let Some(value) = self.node.field(key) {
            self.add(
                key,
                Some(label),
                value.clone(),
                format,
                Placement::Inline,
                protection,
                &[],
            )?;
        }
        Ok(())
    }

    fn nonempty(&mut self, key: &str, label: &'static str) -> Result<()> {
        if self.node.text(key).is_some_and(|value| !value.is_empty()) {
            self.ordinary(key, label, Format::Text, None)?;
        }
        Ok(())
    }
}

fn state_attributes<'a>(node: &'a ProjectionNode, label: &str) -> Vec<&'a str> {
    let attribute = match label {
        "disabled" | "reported disabled" => "enabled",
        "selected" => "selected",
        "on" | "off" | "mixed" => "toggleState",
        "busy" => "busy",
        "unsaved" => "unsaved",
        "invalid" => "invalid",
        "read-only" => {
            return ["readOnly", "textReadOnly", "rangeReadOnly"]
                .into_iter()
                .filter(|key| node.field(key).is_some())
                .collect()
        }
        "value read-only" => "readOnly",
        "text read-only" => "textReadOnly",
        "range read-only" => "rangeReadOnly",
        "clickable" => {
            if capabilities(node).is_some() {
                "capabilities"
            } else {
                "pressable"
            }
        }
        "selectable" => {
            return if capabilities(node).is_some() {
                vec!["selectable", "capabilities"]
            } else {
                vec!["selectable"]
            }
        }
        "text-input" | "replace-only" | "replace unavailable" => "capabilities",
        "focusable" => {
            if capabilities(node).is_some() {
                "capabilities"
            } else {
                "focusable"
            }
        }
        "submenu" => "submenu",
        "expanded" | "collapsed" => {
            if node.field("expanded").is_some() {
                "expanded"
            } else {
                "disclosing"
            }
        }
        "reports focus" => "focused",
        "modal" | "non-modal" => "modal",
        "hidden" => return vec!["visible", "showing"],
        label if label.starts_with("heading level ") => "headingLevel",
        label if label.ends_with(" landmark") => "landmarkType",
        _ => return Vec::new(),
    };
    vec![attribute]
}

fn coverage(node: &ProjectionNode) -> Result<Coverage> {
    let read = node.num("childrenRead").unwrap_or(node.native_children.len() as i64);
    let total = node.num("childCount");
    let start = node.num("childrenStart");
    let end = node.num("childrenEnd");
    let reason = node.text("partialReason").or_else(|| {
        (role(node) != "application" && total.is_some_and(|total| read < total)).then_some("children not fully read")
    });
    Ok(Coverage {
        reason: reason.map(str::to_owned),
        read: Some(read),
        total,
        start,
        end,
        next: node.num("nextChildOffset"),
        omissions: text_items(node.seq("childOmissions"))?
            .into_iter()
            .map(str::to_owned)
            .collect(),
        unexpanded_menu: role(node) == "menuBarItem"
            && node.children.is_empty()
            && start == Some(0)
            && end == Some(0)
            && total.is_some_and(|count| count > 0),
        enumeration_unsupported: node.flag("childEnumerationUnsupported"),
    })
}

pub(crate) fn actions(
    projection: &Projection,
    node: &ProjectionNode,
    options: &AtomOptions<'_>,
    folded: &[NodeId],
) -> Result<Vec<Action>> {
    let mut result = Vec::new();
    for owner in std::iter::once(node.id).chain(folded.iter().copied()) {
        let source = projection.node(owner).ok_or(TextError::Invalid("atom action owner"))?;
        let selected = secondary_actions(source);
        for (index, action) in source.seq("actions").iter().enumerate() {
            if !selected.iter().any(|selected| std::ptr::eq(*selected, action)) {
                continue;
            }
            let name = action.text("name").ok_or(TextError::Invalid("atom action name"))?;
            let kind = action.text("kind").unwrap_or(name);
            if options.compact
                && (matches!(kind, "show_menu" | "scroll_into_view")
                    || options
                        .suppressed_actions
                        .is_some_and(|suppressed| suppressed.contains(&(owner, index))))
            {
                continue;
            }
            let label = action
                .text("description")
                .filter(|label| !label.is_empty())
                .unwrap_or(name);
            let ambiguous = source.seq("actions").iter().any(|other| {
                other.text("name") != Some(name)
                    && (other.text("name") == Some(label) || other.text("description") == Some(label))
            });
            result.push(Action {
                source: owner,
                reference: source.reference.clone(),
                name: name.to_owned(),
                label: label.to_owned(),
                ambiguous,
                catalogue_index: index,
                record: action.clone(),
            });
        }
    }
    Ok(result)
}

fn declarations(projection: &Projection, builder: &mut AtomBuilder<'_>) -> Result<Vec<Declaration>> {
    let mut declarations = Vec::new();
    let node = builder.node;
    for group in node.shared_descriptions.iter().chain(&node.source_roles) {
        let mut facts = Vec::new();
        for key in &group.facts {
            let value = projection
                .node(key.source)
                .and_then(|node| node.native_fields.value(&key.attribute))
                .ok_or(TextError::Invalid("shared atom field source"))?;
            facts.push(AtomFact {
                key: key.clone(),
                value: value.clone(),
            });
        }
        let deferred = builder.options.captured_details
            && facts.iter().all(|fact| {
                builder
                    .options
                    .deferred
                    .is_some_and(|fields| fields.contains(&fact.key))
            });
        builder.mark(
            facts.iter().cloned(),
            &group.attribute,
            node.id,
            if deferred {
                DispositionKind::Details
            } else {
                DispositionKind::Equivalent
            },
        );
        if deferred {
            continue;
        }
        for fact in &facts {
            let source = projection
                .node(fact.key.source)
                .ok_or(TextError::Invalid("shared atom source"))?;
            if let Some(value) = source.native_fields.value(&fact.key.attribute) {
                builder.claims.push(Claim {
                    fact: fact.key.clone(),
                    value: value.clone(),
                    target: node.id,
                    kind: ClaimKind::Shared,
                });
            }
        }
        declarations.push(Declaration {
            attribute: group.attribute.clone(),
            value: value_text(&group.value),
            facts,
        });
    }
    Ok(declarations)
}

fn plan_atom(
    projection: &Projection,
    node: &ProjectionNode,
    parent: Option<&ProjectionNode>,
    options: &AtomOptions<'_>,
    folded: &[NodeId],
) -> Result<Atom> {
    use super::lines::{
        carries_value, has_display_value, has_selection, text_selection_descendants, BINARY_ROLES, CONVENTIONAL,
        TEXT_ROLES,
    };
    use DispositionKind::{Details, Inline};
    use Format::{Literal, Number, References, Selection, Span, Text, Unavailable};
    use Placement::{Block, Caption, Inline as InLine};

    let mut builder = AtomBuilder {
        node,
        options,
        slots: Vec::new(),
        dispositions: Vec::new(),
        claims: Vec::new(),
    };
    let raw_role = role(node);
    let (mut role_label, mut source_description, mut attributes) = role_selection(node);
    if let Some(alias) = options
        .role_aliases
        .and_then(|aliases| aliases.get(&node.id))
        .filter(|alias| {
            raw_role == alias.role
                && node.text("subrole") == alias.subrole.as_deref()
                && node.text("roleDescription") == Some(&alias.description)
        })
    {
        if builder.defer("roleDescription") {
            role_label.clone_from(&alias.label);
            source_description = false;
            attributes = &["role", "subrole"];
        }
    }
    let role_facts: Vec<_> = attributes.iter().flat_map(|key| source_facts(node, key)).collect();
    builder.mark(role_facts, "role", node.id, Inline);
    for attribute in attributes {
        builder.claim(node, attribute, node.id, ClaimKind::Display);
    }

    let focused = options.focused == Some(node.id);
    let native = &node.native_fields;
    let show_value = carries_value(native)
        || focused && node.field("value").is_some()
        || has_display_value(native)
            && node.field("value") != node.field("name")
            && node.field("value") != node.field("description");
    let mut value = node.field("value").cloned().unwrap_or(Value::Null);
    if BINARY_ROLES.contains(&raw_role)
        && (node.flag("valueIsNumber") || matches!(value.as_str(), Some("true" | "false")))
    {
        value = match value.as_str() {
            Some("0" | "false") => value_text("off"),
            Some("1" | "true") => value_text("on"),
            _ => value,
        };
    }
    let selected_descendants = focused
        && !has_selection(node)
        && !matches!(raw_role, "application" | "window")
        && !text_selection_descendants(projection, node)?.is_empty();
    let states = state_labels(
        node,
        options.compact,
        focused,
        selected_descendants,
        value.as_str(),
        show_value,
    )?;
    for label in &states {
        let attributes = state_attributes(node, label);
        let facts: Vec<_> = attributes.iter().flat_map(|key| source_facts(node, key)).collect();
        builder.mark(facts, &format!("state:{label}"), node.id, Inline);
        for attribute in attributes {
            builder.claim(node, attribute, node.id, ClaimKind::Display);
        }
    }
    if options.compact
        && BINARY_ROLES.contains(&raw_role)
        && show_value
        && node.text("toggleState") == value.as_str()
        && matches!(value.as_str(), Some("on" | "off" | "mixed"))
    {
        builder.equivalent("toggleState", "value", None);
    }
    if node.field("expanded").is_some()
        && node.field("expanded") == node.field("disclosing")
        && states
            .iter()
            .any(|label| matches!(label.as_str(), "expanded" | "collapsed"))
    {
        builder.equivalent("disclosing", "state:disclosure", None);
    }

    if let Some(name) = node.text("name").filter(|name| !name.is_empty()) {
        builder.add("name", None, value_text(name), Text, Caption, None, &[])?;
    }
    for (key, label) in [
        ("readOnly", "Value read-only"),
        ("textReadOnly", "Text read-only"),
        ("rangeReadOnly", "Range read-only"),
        ("unsaved", "Unsaved"),
        ("invalid", "Invalid"),
    ] {
        if let Some(value) = node.field(key).filter(|value| **value != Value::Bool(true)) {
            if *value == Value::Bool(false) && options.captured_details {
                builder.defer_known(key);
            } else {
                builder.ordinary(key, label, Literal, Some("state"))?;
            }
        }
    }
    if node.field("errorRelations").is_some() {
        let relations = node.seq("errorRelations");
        let mut targets = Vec::new();
        let mut plain = true;
        for relation in relations {
            let refs = relation.seq("targets");
            let positions = relation.seq("positions");
            plain &= relation.text("state") == Some("complete")
                && relation.text("certainty") == Some("confirmed")
                && relation.text("enumeration").unwrap_or("enumerated") == "enumerated"
                && relation.num("offset").unwrap_or(0) == 0
                && relation.num("total").unwrap_or(refs.len() as i64) == refs.len() as i64
                && (relation.value("positions").is_none()
                    || positions.len() == refs.len()
                        && positions
                            .iter()
                            .enumerate()
                            .all(|(index, value)| value.as_i64() == Some(index as i64)));
            targets.extend_from_slice(refs);
        }
        if !plain {
            builder.ordinary("errorRelations", "Error relation (unresolved)", Literal, Some("state"))?;
        } else if targets.is_empty() && options.captured_details {
            builder.defer_known("errorRelations");
        } else {
            builder.add(
                "errorRelations",
                Some("Error"),
                Value::Seq(targets),
                References,
                InLine,
                Some("state"),
                &[],
            )?;
        }
    }
    builder.nonempty("spatialPosition", "Position")?;
    if let Some(reference) = node.text("visualLabelRef").filter(|value| !value.is_empty()) {
        builder.add(
            "visualLabelRef",
            Some("Visual label"),
            one_ref(reference),
            References,
            InLine,
            None,
            &[],
        )?;
    }
    for (key, label) in [("rowRange", "Row"), ("columnRange", "Column")] {
        builder.ordinary(key, label, Span, None)?;
    }
    for (key, label) in [
        ("rowCount", "Table rows"),
        ("columnCount", "Table columns"),
        ("selectedChildCount", "Selected children"),
        ("index", "Index (0-based)"),
    ] {
        if key == "index"
            && node.field(key).is_some()
            && options.compact
            && options.captured_details
            && !collection_index(projection, node)
        {
            builder.defer_known(key);
        } else {
            builder.ordinary(key, label, Number, None)?;
        }
    }
    if click_deferred(node) {
        builder.add(
            "clickState",
            Some("Click"),
            value_text("deferred"),
            Text,
            InLine,
            None,
            &["enabled", "capabilities"],
        )?;
    }
    if let Some(reference) = node.text("editorOwnerRef").filter(|value| !value.is_empty()) {
        builder.add(
            "editorOwnerRef",
            Some("Editor for"),
            one_ref(reference),
            References,
            InLine,
            None,
            &[],
        )?;
    }
    if node.field("disclosureLevel") != node.field("outlineDepth") {
        builder.ordinary("disclosureLevel", "Outline level", Number, None)?;
    }
    if let Some(reference) = node.text("disclosedBy").filter(|value| !value.is_empty()) {
        builder.add(
            "disclosedBy",
            Some("Parent row"),
            one_ref(reference),
            References,
            InLine,
            None,
            &[],
        )?;
    }
    if node.flag("incompleteDisclosureRelation") {
        builder.add(
            "incompleteDisclosureRelation",
            Some("Parent row"),
            value_text("outside this observation or unavailable"),
            Text,
            InLine,
            None,
            &[],
        )?;
    }
    builder.nonempty("placeholder", "Placeholder")?;
    if let (Some(expanded), Some(disclosing)) = (node.field("expanded"), node.field("disclosing")) {
        if expanded != disclosing {
            let value = if disclosing.as_bool() == Some(true) {
                "expanded"
            } else {
                "collapsed"
            };
            builder.add(
                "disclosing",
                Some("Disclosure state (native reports disagree)"),
                value_text(value),
                Text,
                InLine,
                None,
                &[],
            )?;
        }
    }
    let caps = capabilities(node);
    if let Some(caps) = caps.filter(|_| !node.false_flag("enabled") && node.seq("blockedBy").is_empty()) {
        let standard = if options.compact {
            standard_click(node)
        } else {
            CONVENTIONAL.contains(&raw_role)
        };
        if caps.text("click") == Some("unavailable") && (standard || node.flag("selectable")) {
            builder.add(
                "clickState",
                Some("Click"),
                value_text("unavailable"),
                Text,
                InLine,
                None,
                &["capabilities"],
            )?;
        }
        if TEXT_ROLES.contains(&raw_role)
            && caps.text("typeText") == Some("unavailable")
            && caps.text("replaceText") != Some("available")
        {
            builder.add(
                "inputState",
                Some("Input"),
                value_text("unavailable"),
                Text,
                InLine,
                None,
                &["capabilities"],
            )?;
        }
    }
    if focused && node.false_flag("focused") {
        builder.ordinary("focused", "Focus flag", Literal, Some("focus"))?;
    }
    if let Some(description) = node.text("description").filter(|value| !value.is_empty()) {
        if Some(description) == node.text("name") {
            builder.equivalent("description", "name", None);
        } else if !builder.defer("description") {
            builder.nonempty("description", "Description")?;
        }
    }
    for (attribute, label, format) in [
        ("rangeMinimum", "Minimum", Number),
        ("rangeMaximum", "Maximum", Number),
        ("rangeSmallStep", "Small step", Number),
        ("rangeLargeStep", "Large step", Number),
        ("rangeAllowedValues", "Allowed values", Literal),
        ("hasPopup", "Has popup", Literal),
        ("protected", "Protected", Literal),
        ("offscreen", "Offscreen", Literal),
        ("canSelectMultiple", "Multiple selection", Literal),
        ("selectionRequired", "Selection required", Literal),
        ("itemStatus", "Item status", Text),
    ] {
        builder.ordinary(attribute, label, format, Some("state"))?;
    }
    builder.nonempty("fullDescription", "Full description")?;
    for (attribute, label) in [
        ("valueDescription", "Value description"),
        ("rangeDescription", "Range description"),
    ] {
        if node.field(attribute).is_some()
            && !node
                .shared_description_destinations
                .iter()
                .any(|(name, _)| name == attribute)
            && !builder.defer(attribute)
        {
            builder.ordinary(attribute, label, Text, None)?;
        }
    }
    builder.nonempty("localizedLandmarkType", "Landmark")?;

    let caption = builder.slots.iter().find_map(|slot| {
        (matches!(slot.key.as_str(), "name" | "description") && Some(&slot.value) == node.field("value"))
            .then(|| slot.key.clone())
    });
    let mut value_destination = None;
    if show_value || node.field("value").is_some() && caption.is_none() {
        let natural = options.compact
            && raw_role == "staticText"
            && node.text("name").is_none_or(str::is_empty)
            && node.text("description").is_none_or(str::is_empty)
            && value != Value::Null;
        let input = TEXT_ROLES.contains(&raw_role)
            || caps.is_some_and(|caps| {
                caps.text("typeText") == Some("available") || caps.text("replaceText") == Some("available")
            });
        let label = if natural {
            None
        } else {
            Some(value_label(projection, node)?)
        };
        let format = if value == Value::Null { Unavailable } else { Text };
        let placement = if value.as_str().is_some_and(|value| value.contains(['\n', '\r'])) {
            Block
        } else {
            InLine
        };
        builder.add("value", label, value, format, placement, input.then_some("input"), &[])?;
        value_destination = Some("value".to_owned());
    } else if let Some(caption) = caption {
        builder.equivalent("value", &caption, None);
        value_destination = Some(caption);
    }
    if !node.seq("labelTargets").is_empty() {
        builder.ordinary("labelTargets", "Labels for", References, None)?;
    }
    if node.flag("incompleteLabelRelation") {
        builder.add(
            "incompleteLabelRelation",
            Some("Label relation"),
            value_text("partly outside this observation or unavailable"),
            Text,
            InLine,
            None,
            &[],
        )?;
    }
    if let Some(selection) = node.field("selectionRange").filter(|selection| {
        !options.compact
            || selection.num("location") != Some(0)
            || selection.num("length") != Some(0)
            || TEXT_ROLES.contains(&raw_role)
            || raw_role == "document"
            || caps.is_some_and(|caps| {
                caps.text("typeText") == Some("available") || caps.text("replaceText") == Some("available")
            })
    }) {
        if options.compact && options.summarized_selection.is_some_and(|refs| refs.contains(&node.id)) {
            builder.mark(
                source_facts(node, "selectionRange"),
                "selection-summary",
                node.id,
                DispositionKind::Equivalent,
            );
            builder.claim(node, "selectionRange", node.id, ClaimKind::TextSelectionSummary);
        } else {
            builder.add(
                "selectionRange",
                Some("Selection"),
                selection.clone(),
                Selection,
                InLine,
                Some("selection"),
                &[],
            )?;
        }
    }
    builder.ordinary("shortcut", "Shortcut", Text, None)?;
    if let Some(value) = node.text("menuMark") {
        builder.add(
            "menuMark",
            Some("Mark"),
            value_text(if value.is_empty() { "<none>" } else { value }),
            Text,
            InLine,
            None,
            &[],
        )?;
    }
    if node.text("selectedText").is_some() && !builder.defer("selectedText") {
        builder.ordinary("selectedText", "Selected text", Text, Some("selection"))?;
    }
    for (key, label) in [("help", "Help"), ("url", "URL")] {
        let Some(value) = node.text(key).filter(|value| !value.is_empty()) else {
            continue;
        };
        let same = builder.slots.iter().find_map(|slot| {
            let legal =
                matches!(slot.key.as_str(), "name" | "description" | "value") || key == "url" && slot.key == "help";
            (legal && slot.value.as_str() == Some(value)).then(|| slot.key.clone())
        });
        if let Some(slot) = same {
            builder.equivalent(key, &slot, None);
        } else {
            builder.ordinary(key, label, Text, None)?;
        }
    }
    builder.ordinary("documentUrl", "Document", Text, None)?;
    builder.ordinary("documentReports", "Document reports (unresolved)", Literal, None)?;
    if node.text("identifier").is_some_and(|value| !value.is_empty()) {
        if options.compact && options.captured_details {
            builder.mark(source_facts(node, "identifier"), "identifier", node.id, Details);
        } else {
            builder.ordinary("identifier", "ID", Text, None)?;
        }
    }
    if !node.seq("blockedBy").is_empty() {
        if let Some(parent) = parent.filter(|parent| node.seq("blockedBy") == parent.seq("blockedBy")) {
            builder.equivalent("blockedBy", "blockedBy", Some(parent.id));
        } else {
            builder.ordinary("blockedBy", "Blocked by", References, Some("blocked"))?;
        }
    }
    for (attribute, label) in [
        ("documentText", "Document text"),
        ("controlValue", "Control value"),
        ("rangeValue", "Range value"),
    ] {
        if let Some(value) = node.text(attribute) {
            let numeric = text_items(node.seq("_numericValueAliases"))?.contains(&attribute);
            let complete = text_items(node.seq("_completeTextValues"))?;
            if let Some(destination) = value_destination.as_deref().filter(|_| {
                node.field(attribute) == node.field("value")
                    && (numeric && show_value || complete.contains(&attribute) && complete.contains(&"value"))
            }) {
                builder.equivalent(attribute, destination, None);
            } else {
                let placement = if value.contains(['\r', '\n']) { Block } else { InLine };
                builder.add(attribute, Some(label), value_text(value), Text, placement, None, &[])?;
            }
        }
    }
    let declarations = declarations(projection, &mut builder)?;
    let actions = actions(projection, node, options, folded)?;
    Ok(Atom {
        id: node.id,
        reference: node.reference.clone(),
        parent: parent.map(|parent| parent.id),
        role: role_label,
        source_description,
        states,
        slots: builder.slots,
        actions,
        actions_unavailable: node.flag("actionsUnavailable"),
        coverage: coverage(node)?,
        declarations,
        dispositions: builder.dispositions,
        claims: builder.claims,
    })
}

pub(crate) fn plan_atoms(projection: &Projection, options: &AtomOptions<'_>) -> Result<AtomPlan> {
    projection
        .visible_order()
        .map_err(|_| TextError::Invalid("atom projection order"))?;
    let mut folded = HashMap::<NodeId, Vec<_>>::new();
    for (source, owner) in &projection.folded_into {
        folded.entry(*owner).or_default().push(*source);
    }
    let source_references = projection
        .source_order
        .iter()
        .filter_map(|id| projection.node(*id).map(|node| (*id, node.reference.clone())))
        .collect();
    let mut atoms = AtomPlan {
        roots: projection.roots.clone(),
        focused: options.focused,
        source_references,
        ..AtomPlan::default()
    };
    let mut pending: Vec<_> = projection.roots.iter().rev().map(|root| (*root, None)).collect();
    while let Some((id, parent)) = pending.pop() {
        let node = projection.node(id).ok_or(TextError::Invalid("atom target"))?;
        let atom = plan_atom(
            projection,
            node,
            parent,
            options,
            folded.get(&id).map_or(&[], Vec::as_slice),
        )?;
        if node.flag("selected") {
            atoms.selected.push(id);
        }
        atoms.order.push(id);
        atoms.atoms.insert(id, atom);
        pending.extend(node.children.iter().rev().map(|id| (*id, Some(node))));
    }
    Ok(atoms)
}

impl AtomPlan {
    pub(crate) fn layout_evidence(&self) -> HashMap<NodeId, AtomLayoutEvidence> {
        self.atoms
            .iter()
            .map(|(id, atom)| {
                (
                    *id,
                    AtomLayoutEvidence {
                        role_label: atom.role.clone(),
                        source_description: atom.source_description.then(|| atom.role.clone()),
                        state_labels: atom.states.clone(),
                        has_actions: !atom.actions.is_empty(),
                        actions_unavailable: atom.actions_unavailable,
                        declarations: !atom.declarations.is_empty(),
                        partial: atom.coverage.partial(),
                        focused: self.focused == Some(*id),
                        unexpanded_menu: atom.coverage.unexpanded_menu,
                        action_cost: atom
                            .actions
                            .iter()
                            .map(|action| action.label.chars().count() + action.name.chars().count() + 24)
                            .sum(),
                        slots: atom
                            .slots
                            .iter()
                            .map(|slot| LayoutSlot {
                                key: slot.key.clone(),
                                format: slot.format.name().to_owned(),
                                value: slot.value.clone(),
                                block: slot.placement == Placement::Block,
                                caption: slot.placement == Placement::Caption,
                                protected: slot.protection.is_some(),
                                preview: slot.has_preview(),
                                label: slot.label,
                            })
                            .collect(),
                    },
                )
            })
            .collect()
    }
}

pub(crate) fn field(value: &str) -> String {
    if value.contains(['|', ';', '{', '}']) {
        quoted(value)
    } else {
        scalar_field(value)
    }
}

pub(crate) fn slot_value(slot: &Slot) -> Result<String> {
    if slot.format == Format::Preview {
        let preview = slot
            .preview
            .as_ref()
            .ok_or(TextError::Invalid("prepared preview lacks captured ranges"))?;
        return Ok(match preview {
            Preview::Carrier(head, count) => format!("{} [embedded data, {count} encoded chars]", field(head)),
            Preview::EmbeddedUrl(head) => format!("{} [embedded]", field(head)),
            Preview::Url(head) => format!("{}… [URL shortened]", field(head)),
        });
    }
    if slot.format == Format::Unavailable {
        return Ok("<unavailable>".to_owned());
    }
    if matches!(slot.format, Format::Span | Format::Selection) && slot.value.as_map().is_some() {
        let start = slot
            .value
            .num("location")
            .ok_or(TextError::Invalid("slot span location"))?;
        let count = slot.value.num("length").ok_or(TextError::Invalid("slot span length"))?;
        return Ok(match slot.format {
            Format::Selection => format!("{start}:{count}"),
            _ if count == 0 => format!("{start}:0 (0-based)"),
            _ if count == 1 => (start + 1).to_string(),
            _ => format!("{}–{}", start + 1, start + count),
        });
    }
    if slot.format == Format::References {
        let values = slot.value.as_seq().ok_or(TextError::Invalid("slot references"))?;
        return Ok(text_items(values)?.join(", "));
    }
    if slot.format == Format::Toggle {
        match &slot.value {
            Value::Bool(true) => return Ok("on".to_owned()),
            Value::Bool(false) => return Ok("off".to_owned()),
            Value::Text(value) if matches!(value.as_str(), "true" | "1") => return Ok("on".to_owned()),
            Value::Text(value) if matches!(value.as_str(), "false" | "0") => return Ok("off".to_owned()),
            _ => {}
        }
    }
    if matches!(slot.format, Format::Literal | Format::Number) {
        return json(&slot.value, JsonFormat::Literal);
    }
    slot.value
        .as_str()
        .map_or_else(|| json(&slot.value, JsonFormat::Literal), |value| Ok(field(value)))
}

#[derive(Clone, Copy, Default)]
pub(crate) struct AtomStyle<'a> {
    pub(crate) short: bool,
    pub(crate) prefix_member: bool,
    pub(crate) omit_states: bool,
    pub(crate) states_to_remove: &'a [usize],
    pub(crate) glyph: Option<&'a str>,
    pub(crate) outside_window: bool,
    pub(crate) shared_index_basis: bool,
    pub(crate) slots: Option<&'a [Slot]>,
    pub(crate) omit_slots: &'a [String],
    pub(crate) omit_actions: &'a [usize],
    pub(crate) note_omit_actions: &'a [usize],
    pub(crate) note_slots: &'a [String],
    pub(crate) note_actions: &'a [usize],
    pub(crate) block_slots: &'a [String],
    pub(crate) toggle_slot: Option<&'a str>,
    pub(crate) visual_row: &'a [String],
    pub(crate) value_overrides: Option<&'a HashMap<String, String>>,
    pub(crate) label_overrides: Option<&'a HashMap<String, String>>,
    pub(crate) include_native_notes: bool,
}

fn action_spelling(action: &Action, force_name: bool) -> String {
    let mut text = field(&action.label);
    if action.ambiguous || force_name && action.label != action.name {
        text.push_str(&format!(" [name: {}]", quoted(&action.name)));
    }
    text
}

fn atom_value(slot: &Slot, style: &AtomStyle<'_>) -> Result<(String, bool)> {
    if let Some(value) = style.value_overrides.and_then(|values| values.get(&slot.key)) {
        return Ok((value.clone(), true));
    }
    Ok((slot_value(slot)?, false))
}

pub(crate) fn atom_notes(
    atom: &Atom,
    style: &AtomStyle<'_>,
    include_owner: bool,
) -> Result<Option<String>> {
    let mut fields = Vec::new();
    for slot in atom.slots.iter().filter(|slot| style.note_slots.contains(&slot.key)) {
        let label = slot.label.map_or_else(|| capitalize_key(&slot.key), str::to_owned);
        fields.push(format!("{label}: {}", atom_value(slot, style)?.0));
    }
    let actions: Vec<_> = atom
        .actions
        .iter()
        .filter(|action| {
            style.note_actions.contains(&action.catalogue_index)
                && !style.note_omit_actions.contains(&action.catalogue_index)
        })
        .map(|action| action_spelling(action, true))
        .collect();
    if !actions.is_empty() {
        fields.push(format!("Secondary Actions: {}", actions.join(", ")));
    }
    if fields.is_empty() {
        return Ok(None);
    }
    let prefix = if include_owner {
        format!("{} {}: ", atom.reference, field(&atom.role))
    } else {
        String::new()
    };
    Ok(Some(prefix + &fields.join(", ")))
}

pub(crate) fn capitalize_key(key: &str) -> String {
    let mut chars = key.chars();
    chars
        .next()
        .into_iter()
        .flat_map(char::to_uppercase)
        .chain(chars.flat_map(char::to_lowercase))
        .collect()
}

pub(crate) fn atom_lines(atom: &Atom, style: &AtomStyle<'_>, atoms: &AtomPlan) -> Result<Vec<String>> {
    let mut prefix = atom.reference.clone();
    if let Some(glyph) = style.glyph {
        prefix.push_str(&format!(" {glyph}"));
    }
    if !style.short && !style.prefix_member {
        prefix.push_str(&format!(" {}", field(&atom.role)));
    }
    if !style.omit_states && (!style.short || style.prefix_member) {
        let states: Vec<_> = atom
            .states
            .iter()
            .enumerate()
            .filter(|(index, _)| !style.states_to_remove.contains(index))
            .map(|(_, label)| label.as_str())
            .collect();
        if !states.is_empty() {
            prefix.push_str(&format!(" ({})", states.join(", ")));
        }
    }
    if style.outside_window {
        prefix.push_str(" (bounds outside observed window)");
    }
    let mut captions = Vec::new();
    let mut inline = Vec::new();
    let mut blocks = Vec::new();
    for slot in style.slots.unwrap_or(&atom.slots) {
        if style.note_slots.contains(&slot.key)
            || style.omit_slots.contains(&slot.key)
            || style.toggle_slot == Some(&slot.key)
        {
            continue;
        }
        let (value, overridden) = atom_value(slot, style)?;
        let mut label = if slot.key == "index" && style.shared_index_basis {
            Some("Index")
        } else {
            slot.label
        }
        .map(str::to_owned);
        if slot.key == "description" && !style.visual_row.is_empty() {
            let row = reference_set(style.visual_row.iter().map(String::as_str))?;
            label = Some(format!(
                "{} [element {}; same visual row {row}]",
                label.as_deref().unwrap_or("Description"),
                atom.reference
            ));
        }
        if let Some(replacement) = style.label_overrides.and_then(|labels| labels.get(&slot.key)) {
            label = Some(replacement.clone());
        }
        let text = label
            .as_deref()
            .map_or_else(|| value.clone(), |label| format!("{label}: {value}"));
        if slot.placement != Placement::Block && style.block_slots.contains(&slot.key) {
            blocks.push(text);
        } else {
            match slot.placement {
                Placement::Caption => captions.push(text),
                Placement::Inline => inline.push(text),
                Placement::Block => {
                    if let Some(original) = slot.value.as_str().filter(|_| !overridden && slot.preview.is_none()) {
                        blocks.push(format!("{}:", label.as_deref().unwrap_or("Value")));
                        blocks.extend(
                            original
                                .replace("\r\n", "\n")
                                .replace('\r', "\n")
                                .split('\n')
                                .map(|paragraph| format!("| {}", field(paragraph))),
                        );
                    } else {
                        blocks.push(text);
                    }
                }
            }
        }
    }
    if !captions.is_empty() {
        prefix.push(' ');
        prefix.push_str(&captions.join(" "));
    }
    let mut actions: Vec<(&str, Vec<String>)> = Vec::new();
    for action in &atom.actions {
        if style.note_actions.contains(&action.catalogue_index) || style.omit_actions.contains(&action.catalogue_index)
        {
            continue;
        }
        if let Some((_, group)) = actions
            .iter_mut()
            .find(|(owner, _)| *owner == action.reference.as_str())
        {
            group.push(action_spelling(action, false));
        } else {
            actions.push((&action.reference, vec![action_spelling(action, false)]));
        }
    }
    for (owner, actions) in actions {
        let label = if owner == atom.reference {
            "Secondary Actions".to_owned()
        } else {
            format!("Secondary Actions [element {owner}]")
        };
        inline.push(format!("{label}: {}", actions.join(", ")));
    }
    if atom.actions_unavailable {
        inline.push("Secondary Actions: <unavailable>".to_owned());
    }
    if !inline.is_empty() {
        prefix.push_str(if captions.is_empty() { " " } else { ", " });
        prefix.push_str(&inline.join(", "));
    }
    for declaration in &atom.declarations {
        let owners = declaration
            .facts
            .iter()
            .map(|fact| {
                atoms
                    .atoms
                    .get(&fact.key.source)
                    .map(|source| source.reference.as_str())
                    .or_else(|| atoms.source_references.get(&fact.key.source).map(String::as_str))
                    .ok_or(TextError::Invalid("declaration source reference"))
            })
            .collect::<Result<Vec<_>>>()?;
        let owners = reference_set(owners.into_iter())?;
        let label = match declaration.attribute.as_str() {
            "valueDescription" => "Value description",
            "rangeDescription" => "Range description",
            "roleDescription" => "Native role description",
            "role" => "Native role",
            "description" => "Description",
            label => label,
        };
        blocks.push(format!(
            "{label} [elements {owners}]: {}",
            json(&declaration.value, JsonFormat::Literal)?
        ));
    }
    if style.include_native_notes {
        if let Some(note) = atom_notes(atom, style, false)? {
            blocks.push(format!("Native: {note}"));
        }
    }
    let mut lines = vec![prefix];
    lines.extend(blocks.into_iter().map(|block| format!("\t{block}")));
    Ok(lines)
}

pub(crate) fn coverage_lines(atom: &Atom) -> Vec<String> {
    let coverage = &atom.coverage;
    if coverage.unexpanded_menu {
        return Vec::new();
    }
    let mut lines = Vec::new();
    if let Some((start, total)) = coverage.start.zip(coverage.total) {
        let read = coverage.read.map_or_else(|| "?".to_owned(), |read| read.to_string());
        let mut count = format!("{read}/{total} read");
        if start != 0 {
            count.push_str(&format!(", offset {start}"));
        }
        if coverage.next.is_some() {
            count.push_str("; partial; cursor below");
        } else if coverage.end == Some(total) {
            if coverage.read.is_some_and(|read| read < total - start) {
                count.push_str("; partial; unavailable native slots");
            }
            count.push_str("; end of direct children");
        } else {
            count.push_str("; partial; read()");
        }
        lines.push(format!("Children: {count}"));
    } else if coverage.partial() {
        let mut count = match (coverage.read, coverage.total) {
            (Some(read), Some(total)) => format!("{read}/{total} read"),
            (_, Some(total)) => format!("total {total}"),
            _ => "total unknown".to_owned(),
        };
        if let Some(reason) = &coverage.reason {
            count.push_str(&format!("; partial: {}", field(reason)));
        }
        if coverage.next.is_some() {
            count.push_str("; cursor below");
        }
        lines.push(format!("Children: {count}"));
        if coverage.next.is_none() && !coverage.enumeration_unsupported {
            lines.push(format!("Inspect subtree: app.element({}).read()", atom.reference));
        }
    }
    lines.extend(
        coverage
            .omissions
            .iter()
            .map(|omission| format!("Omitted: {}", field(omission))),
    );
    lines
}

pub(crate) fn remember(
    atom: &Atom,
    atoms: &AtomPlan,
    depth: usize,
    context: &[(String, String)],
    style: &AtomStyle<'_>,
    related: Vec<String>,
    recovery: &[String],
) -> Result<RenderedNode> {
    let indent = "\t".repeat(depth);
    let own = atom_lines(atom, style, atoms)?;
    let mut no_state = AtomStyle {
        omit_states: true,
        ..*style
    };
    no_state.include_native_notes = false;
    let content = atom_lines(atom, &no_state, atoms)?
        .into_iter()
        .next()
        .ok_or(TextError::Invalid("atom content"))?;
    let coverage: Vec<_> = coverage_lines(atom)
        .into_iter()
        .map(|line| format!("{indent}\t{line}"))
        .collect();
    let text = own
        .iter()
        .map(|line| format!("{indent}{line}"))
        .chain(coverage.iter().cloned())
        .collect::<Vec<_>>()
        .join("\n");
    let hints = coverage
        .into_iter()
        .chain(recovery.iter().map(|hint| format!("{indent}\t{hint}")))
        .collect();
    Ok(RenderedNode {
        reference: atom.reference.clone(),
        parent: atom.parent.and_then(|id| atoms.source_references.get(&id).cloned()),
        depth,
        text,
        content: format!("{indent}{content}"),
        hints,
        related,
        partial: atom.coverage.partial() && !atom.coverage.unexpanded_menu,
        layout: context.to_vec(),
    })
}
