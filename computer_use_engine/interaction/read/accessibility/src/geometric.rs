// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Source-conserving geometric interface layout. Arena identities are never
//! exposed as executable references.

use std::collections::{HashMap, HashSet};

use super::{
    diff::{python_str_repr, sha256},
    facts::FactSnapshot,
    geometry::{Box, Geometry},
    numbering::NodeId,
    projection::{Projection, ProjectionNode},
    value::Value,
};

pub(super) mod alignment;
pub(super) mod columns;
mod composition;
mod compound;
mod evidence;
mod partition;
mod patterns;
mod regions;
mod rows;
mod scopes;
mod structure;
mod templates;
mod terminal;
#[cfg(test)]
mod tests;
mod units;
mod validation;
pub(crate) use evidence::{ordered_ancestry, reading_scope, source_partition};

pub(crate) type Result<T> = std::result::Result<T, GeometryError>;

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum GeometryError {
    MissingBlock(BlockId),
    Invalid(&'static str),
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub(crate) struct BlockId(pub(crate) usize);

#[derive(Clone, Debug)]
pub(crate) struct Block {
    pub(crate) key: String,
    pub(crate) kind: &'static str,
    pub(crate) primary_ref: Option<NodeId>,
    pub(crate) source_refs: Vec<NodeId>,
    pub(crate) children: Vec<BlockId>,
    pub(crate) layout: &'static str,
    pub(crate) origin: &'static str,
    pub(crate) label_ref: Option<NodeId>,
    pub(crate) position: Option<String>,
    pub(crate) shared_prefix_ref: Option<NodeId>,
    pub(crate) columns: Vec<String>,
    pub(crate) column_prefix_refs: Vec<Option<NodeId>>,
    pub(crate) region_role: Option<&'static str>,
    pub(crate) field_target_ref: Option<NodeId>,
    pub(crate) member_roles: Vec<(NodeId, &'static str)>,
    pub(crate) heading_style: &'static str,
    pub(crate) visual_unit: bool,
    pub(crate) column_slots: Vec<Option<usize>>,
    pub(crate) column_alignments: Vec<&'static str>,
}

impl Block {
    pub(crate) fn new(key: String, kind: &'static str, primary_ref: Option<NodeId>, children: Vec<BlockId>) -> Self {
        Self {
            key,
            kind,
            primary_ref,
            source_refs: primary_ref.into_iter().collect(),
            children,
            layout: "tree",
            origin: "native",
            label_ref: None,
            position: None,
            shared_prefix_ref: None,
            columns: Vec::new(),
            column_prefix_refs: Vec::new(),
            region_role: None,
            field_target_ref: None,
            member_roles: Vec::new(),
            heading_style: "atom",
            visual_unit: false,
            column_slots: Vec::new(),
            column_alignments: Vec::new(),
        }
    }
}

#[derive(Clone, Debug)]
pub(crate) struct Relation {
    pub(crate) source: NodeId,
    pub(crate) property: String,
    pub(crate) kind: String,
    pub(crate) targets: Vec<NodeId>,
    pub(crate) complete: bool,
    pub(crate) confirmed: bool,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct NodeEvidence {
    pub(crate) scope: NodeId,
    pub(crate) independent: bool,
    pub(crate) boundary: bool,
    pub(crate) incomplete: bool,
    pub(crate) hidden: bool,
}

#[derive(Clone, Copy, Debug)]
pub(crate) enum ParentReport {
    Confirmed(NodeId),
    Root,
    Unknown,
    Conflict,
    Semantic(Option<NodeId>),
}

#[derive(Clone, Debug)]
pub(crate) struct AtomSlot {
    pub(crate) key: String,
    pub(crate) format: String,
    pub(crate) value: Value,
    pub(crate) block: bool,
    pub(crate) caption: bool,
    pub(crate) protected: bool,
    pub(crate) preview: bool,
    pub(crate) label: Option<&'static str>,
}

#[derive(Clone, Debug)]
pub(crate) struct AtomLayoutEvidence {
    pub(crate) role_label: String,
    pub(crate) source_description: Option<String>,
    pub(crate) state_labels: Vec<String>,
    pub(crate) has_actions: bool,
    pub(crate) actions_unavailable: bool,
    pub(crate) declarations: bool,
    pub(crate) partial: bool,
    pub(crate) focused: bool,
    pub(crate) unexpanded_menu: bool,
    pub(crate) action_cost: usize,
    pub(crate) slots: Vec<AtomSlot>,
}

pub(crate) struct GeometricContext<'a> {
    pub(crate) projection: &'a Projection,
    pub(crate) geometry: &'a Geometry,
    pub(crate) facts: Option<&'a FactSnapshot>,
    pub(crate) atoms: Option<&'a HashMap<NodeId, AtomLayoutEvidence>>,
}

#[derive(Debug)]
pub(crate) struct GeometricPlan {
    pub(crate) roots: Vec<BlockId>,
    pub(crate) root_refs: Vec<NodeId>,
    pub(crate) target_refs: Vec<NodeId>,
    pub(crate) ref_locations: HashMap<NodeId, BlockId>,
    pub(crate) projected_parents: HashMap<NodeId, NodeId>,
    pub(crate) evidence: HashMap<NodeId, NodeEvidence>,
    pub(crate) relations: Vec<Relation>,
    pub(crate) parent_reports: HashMap<NodeId, ParentReport>,
    pub(crate) geometric_patterns: Vec<patterns::PatternPlan>,
    pub(crate) decisions: Vec<String>,
    pub(crate) enabled: bool,
    blocks: Vec<Option<Block>>,
    keys: HashMap<String, BlockId>,
    source_blocks: HashMap<NodeId, BlockId>,
    pub(crate) regions: Vec<(BlockId, HashMap<NodeId, Vec<usize>>)>,
}

impl GeometricPlan {
    pub(crate) fn build(context: &GeometricContext<'_>, enabled: bool) -> Result<Self> {
        let mut plan = Self::seed(context, enabled)?;
        compound::compose(&mut plan, context)?;
        composition::classify_units(&mut plan, context)?;
        units::compose_control_units(&mut plan, context)?;
        units::expand_transparent_frontier(&mut plan, context)?;
        patterns::apply_patterns(&mut plan, context)?;
        let alignment = regions::alignments(&plan, context);
        let relations = regions::capture_relations(&alignment);
        composition::group_heading_sections(&mut plan, context)?;
        regions::canonicalize(&mut plan, context, &alignment, &relations)?;
        composition::group_form_bands(&mut plan, context, &alignment)?;
        composition::associate_field_labels(&mut plan, context)?;
        regions::arrange(&mut plan, context, &relations)?;
        composition::associate_value_controls(&mut plan, context)?;
        rows::group_rows(&mut plan, context)?;
        composition::build_records(&mut plan, context)?;
        structure::build_record_blocks(&mut plan, context)?;
        structure::organize_subfields(&mut plan, context)?;
        structure::group_radio_options(&mut plan, context)?;
        rows::order_regions(&mut plan, context);
        rows::order_rows(&mut plan, context);
        terminal::group_empty_atoms(&mut plan, context);
        terminal::group_inline_members(&mut plan, context);
        terminal::share_inline_headers(&mut plan, context)?;
        terminal::plan_record_members(&mut plan, context)?;
        terminal::plan_visual_chains(&mut plan, context)?;
        templates::select_templates(&mut plan, context);
        columns::plan_columns(&mut plan, context)?;
        validation::finish(plan, context)
    }

    pub(crate) fn seed(context: &GeometricContext<'_>, enabled: bool) -> Result<Self> {
        let projection = context.projection;
        let target_refs = projection
            .visible_order()
            .map_err(|_| GeometryError::Invalid("invalid interface sources"))?;
        let mut plan = Self {
            roots: Vec::new(),
            root_refs: projection.roots.clone(),
            target_refs,
            ref_locations: HashMap::new(),
            projected_parents: HashMap::new(),
            evidence: HashMap::new(),
            relations: Vec::new(),
            parent_reports: HashMap::new(),
            geometric_patterns: Vec::new(),
            decisions: Vec::new(),
            enabled,
            blocks: Vec::new(),
            keys: HashMap::new(),
            source_blocks: HashMap::new(),
            regions: Vec::new(),
        };
        let (relations, parents) = evidence::capture(context);
        plan.relations = relations;
        plan.parent_reports = parents;
        for (i, &id) in plan.target_refs.iter().enumerate() {
            plan.source_blocks.insert(id, BlockId(i));
        }
        for index in 0..plan.target_refs.len() {
            let id = plan.target_refs[index];
            let node = projection
                .node(id)
                .ok_or(GeometryError::Invalid("missing projected node"))?;
            plan.evidence.insert(id, evidence::node_evidence(node));
            let children = node
                .children
                .iter()
                .map(|child| {
                    plan.projected_parents.insert(*child, id);
                    plan.source_blocks
                        .get(child)
                        .copied()
                        .ok_or(GeometryError::Invalid("foreign projected child"))
                })
                .collect::<Result<Vec<_>>>()?;
            let role = role(node);
            let region_role = if role == "toolbar" {
                Some("toolbar")
            } else if role == "navigation" || field_text(node, "landmarkType") == Some("navigation") {
                Some("navigation")
            } else {
                None
            };
            let kind = match role {
                "window" | "sheet" => "window",
                "table" => "table",
                _ if region_role.is_some() => "region",
                "list" | "outline" => "collection",
                _ if region_role_name(role) || truth(node.field("spatialPosition")) => "region",
                _ => "tree",
            };
            let mut block = Block::new(format!("element:{}", node.reference), kind, Some(id), children);
            block.position = field_text(node, "spatialPosition")
                .filter(|position| matches!(*position, "left" | "middle" | "right" | "top" | "bottom"))
                .map(str::to_owned);
            block.region_role = region_role.or((kind == "region").then_some("region"));
            plan.insert(block);
        }
        plan.roots = plan.root_refs.iter().map(|id| plan.source_blocks[id]).collect();
        Ok(plan)
    }

    pub(crate) fn active_ids(&self) -> impl Iterator<Item = BlockId> + '_ {
        self.blocks
            .iter()
            .enumerate()
            .filter_map(|(index, block)| block.is_some().then_some(BlockId(index)))
    }

    pub(crate) fn block(&self, id: BlockId) -> Option<&Block> {
        self.blocks.get(id.0)?.as_ref()
    }

    pub(crate) fn block_mut(&mut self, id: BlockId) -> Option<&mut Block> {
        self.blocks.get_mut(id.0)?.as_mut()
    }

    pub(crate) fn by_key(&self, key: &str) -> Option<BlockId> {
        self.keys.get(key).copied()
    }

    pub(crate) fn source_block(&self, reference: NodeId) -> Option<BlockId> {
        self.source_blocks.get(&reference).copied()
    }

    pub(crate) fn insert(&mut self, block: Block) -> BlockId {
        if let Some(&id) = self.keys.get(&block.key) {
            self.blocks[id.0] = Some(block);
            return id;
        }
        let id = BlockId(self.blocks.len());
        self.keys.insert(block.key.clone(), id);
        self.blocks.push(Some(block));
        id
    }

    pub(crate) fn remove(&mut self, id: BlockId) -> Option<Block> {
        let block = self.blocks.get_mut(id.0)?.take()?;
        self.keys.remove(&block.key);
        Some(block)
    }

    pub(crate) fn block_sources(&self, id: BlockId) -> Vec<NodeId> {
        let mut output = Vec::new();
        let mut pending = vec![id];
        while let Some(next) = pending.pop() {
            if let Some(block) = self.block(next) {
                output.extend(block.source_refs.iter().copied());
                pending.extend(block.children.iter().rev().copied());
            }
        }
        output
    }

    pub(crate) fn block_bounds(&self, id: BlockId, geometry: &Geometry) -> Option<Box> {
        let block = self.block(id)?;
        if let Some(source) = block.primary_ref {
            return geometry.get(&source).cloned();
        }
        let mut frames = vec![(block.children.iter(), Vec::new())];
        while let Some((children, bounds)) = frames.last_mut() {
            if let Some(&child) = children.next() {
                let child = self.block(child)?;
                if let Some(source) = child.primary_ref {
                    bounds.push(Some(geometry.get(&source)?.clone()));
                } else {
                    frames.push((child.children.iter(), Vec::new()));
                }
            } else {
                let (_, bounds) = frames.pop().expect("active geometry block");
                let combined = union(bounds)?;
                let Some((_, parent)) = frames.last_mut() else { return Some(combined); };
                parent.push(Some(combined));
            }
        }
        unreachable!("root geometry block returns its bounds")
    }

    pub(crate) fn same_region(&self, references: &[NodeId]) -> bool {
        self.regions.iter().all(|(_, paths)| {
            let mut assigned = references.iter().filter_map(|id| paths.get(id));
            assigned.next().is_none_or(|first| assigned.all(|path| path == first))
        })
    }

    pub(crate) fn same_scope(&self, refs: &[NodeId], context: &GeometricContext<'_>) -> bool {
        let key = |id| {
            let node = context.projection.node(id)?;
            Some((self.evidence.get(&id)?.scope, seq(node.field("blockedBy"))))
        };
        refs.first()
            .and_then(|id| key(*id))
            .is_some_and(|first| refs.iter().all(|id| key(*id) == Some(first)))
    }

    fn validate_sources(&self) -> Result<HashMap<NodeId, BlockId>> {
        let all: HashSet<_> = self.target_refs.iter().copied().collect();
        if all.len() != self.target_refs.len() {
            return Err(GeometryError::Invalid("duplicate projected source"));
        }
        let mut pending = self.roots.clone();
        let mut seen_blocks = HashSet::new();
        let mut locations = HashMap::new();
        while let Some(id) = pending.pop() {
            if !seen_blocks.insert(id) {
                return Err(GeometryError::Invalid("interface block appears more than once"));
            }
            let block = self.block(id).ok_or(GeometryError::MissingBlock(id))?;
            if block
                .primary_ref
                .is_some_and(|reference| !block.source_refs.contains(&reference))
            {
                return Err(GeometryError::Invalid("interface primary is outside its sources"));
            }
            if block.label_ref.is_some_and(|label| !all.contains(&label)) {
                return Err(GeometryError::Invalid("interface label is outside this projection"));
            }
            if block.field_target_ref.is_some() || !block.member_roles.is_empty() {
                let mut descendants = vec![id];
                let mut checked = HashSet::new();
                let mut members = HashSet::new();
                while let Some(member) = descendants.pop() {
                    if !checked.insert(member) {
                        return Err(GeometryError::Invalid("composed field contains a repeated block"));
                    }
                    let child = self.block(member).ok_or(GeometryError::MissingBlock(member))?;
                    members.extend(child.source_refs.iter().copied());
                    descendants.extend(child.children.iter().copied());
                }
                let roles: HashMap<_, _> = block.member_roles.iter().copied().collect();
                if block.kind != "field"
                    || roles.len() != block.member_roles.len()
                    || roles.keys().any(|source| !members.contains(source))
                    || block
                        .field_target_ref
                        .is_some_and(|target| !members.contains(&target) || roles.get(&target) != Some(&"control"))
                {
                    return Err(GeometryError::Invalid(
                        "composed field lost its original member or input target",
                    ));
                }
            }
            for &reference in &block.source_refs {
                if !all.contains(&reference) || locations.insert(reference, id).is_some() {
                    return Err(GeometryError::Invalid(
                        "interface source appears more than once or outside its scope",
                    ));
                }
            }
            pending.extend(block.children.iter().copied());
        }
        if locations.len() != all.len() || seen_blocks.len() != self.active_ids().count() {
            return Err(GeometryError::Invalid(
                "interface layout lost an original source or block",
            ));
        }
        Ok(locations)
    }

    pub(crate) fn freeze(mut self) -> Result<Self> {
        self.ref_locations = self.validate_sources()?;
        Ok(self)
    }

    /// Adapt the frozen native block plan at the existing comparison boundary.
    pub(crate) fn to_value(&self, context: &GeometricContext<'_>) -> Result<Value> {
        let text = |value: &str| Value::Text(value.to_owned());
        let map = |items: Vec<(&str, Value)>| {
            Value::Map(items.into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
        };
        let key = |id| {
            self.block(id)
                .map(|block| text(&block.key))
                .ok_or(GeometryError::MissingBlock(id))
        };
        let reference = |id| {
            context
                .projection
                .node(id)
                .map(|node| text(&node.reference))
                .ok_or(GeometryError::Invalid("missing interface reference"))
        };
        let keys = |ids: &[BlockId]| Ok(Value::Seq(ids.iter().copied().map(key).collect::<Result<_>>()?));
        let refs = |ids: &[NodeId]| Ok(Value::Seq(ids.iter().copied().map(reference).collect::<Result<_>>()?));
        let optional_ref = |id: Option<NodeId>| id.map(reference).transpose().map(|value| value.unwrap_or(Value::Null));
        let mut blocks = Vec::new();
        for id in self.active_ids() {
            let block = self.block(id).expect("active block");
            let members = block
                .member_roles
                .iter()
                .map(|&(id, role)| Ok(Value::Seq(vec![reference(id)?, text(role)])))
                .collect::<Result<_>>()?;
            blocks.push((
                block.key.clone(),
                map(vec![
                    ("key", text(&block.key)),
                    ("kind", text(block.kind)),
                    ("primary_ref", optional_ref(block.primary_ref)?),
                    ("source_refs", refs(&block.source_refs)?),
                    ("children", keys(&block.children)?),
                    ("layout", text(block.layout)),
                    ("origin", text(block.origin)),
                    ("label_ref", optional_ref(block.label_ref)?),
                    ("position", block.position.as_deref().map(text).unwrap_or(Value::Null)),
                    ("shared_prefix_ref", optional_ref(block.shared_prefix_ref)?),
                    (
                        "columns",
                        Value::Seq(block.columns.iter().map(|item| text(item)).collect()),
                    ),
                    (
                        "column_prefix_refs",
                        Value::Seq(
                            block
                                .column_prefix_refs
                                .iter()
                                .copied()
                                .map(optional_ref)
                                .collect::<Result<_>>()?,
                        ),
                    ),
                    ("region_role", block.region_role.map(text).unwrap_or(Value::Null)),
                    ("field_target_ref", optional_ref(block.field_target_ref)?),
                    ("member_roles", Value::Seq(members)),
                    ("heading_style", text(block.heading_style)),
                    ("visual_unit", Value::Bool(block.visual_unit)),
                    (
                        "column_slots",
                        Value::Seq(
                            block
                                .column_slots
                                .iter()
                                .map(|slot| slot.map(|slot| Value::Int(slot as i64)).unwrap_or(Value::Null))
                                .collect(),
                        ),
                    ),
                    (
                        "column_alignments",
                        Value::Seq(block.column_alignments.iter().map(|item| text(item)).collect()),
                    ),
                ]),
            ));
        }
        Ok(map(vec![
            ("roots", keys(&self.roots)?),
            ("blocks", Value::Map(blocks)),
            ("target_refs", refs(&self.target_refs)?),
            ("root_refs", refs(&self.root_refs)?),
            (
                "decisions",
                Value::Seq(self.decisions.iter().map(|item| text(item)).collect()),
            ),
            ("format_version", text("geometric-2")),
            (
                "geometric_patterns",
                Value::Seq(
                    self.geometric_patterns
                        .iter()
                        .map(|pattern| pattern.to_value(self, context))
                        .collect::<Result<_>>()?,
                ),
            ),
        ]))
    }
}

pub(crate) fn field_text<'a>(node: &'a ProjectionNode, key: &str) -> Option<&'a str> {
    node.field(key).and_then(Value::as_str)
}

pub(crate) fn role(node: &ProjectionNode) -> &str {
    field_text(node, "role").unwrap_or("unknown")
}

pub(crate) fn same_prefix(context: &GeometricContext<'_>, first: NodeId, second: NodeId) -> bool {
    if let Some(atoms) = context.atoms {
        return matches!((atoms.get(&first), atoms.get(&second)), (Some(a), Some(b))
            if a.role_label == b.role_label && a.source_description == b.source_description && a.state_labels == b.state_labels);
    }
    let (Some(a), Some(b)) = (context.projection.node(first), context.projection.node(second)) else {
        return false;
    };
    [
        "role",
        "subrole",
        "roleDescription",
        "enabled",
        "focused",
        "focusable",
        "selected",
        "selectable",
        "pressable",
        "editable",
        "settable",
        "required",
        "invalid",
        "busy",
        "modal",
        "expandable",
        "expanded",
        "toggleState",
        "valueType",
        "valueKind",
        "capabilities",
        "blockedBy",
    ]
    .iter()
    .all(|name| a.field(name).unwrap_or(&Value::Null) == b.field(name).unwrap_or(&Value::Null))
}

pub(crate) fn seq(value: Option<&Value>) -> &[Value] {
    value.and_then(Value::as_seq).unwrap_or_default()
}

pub(crate) fn truth(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null | Value::Bool(false)) => false,
        Some(Value::Bool(true)) => true,
        Some(Value::Int(value)) => *value != 0,
        Some(Value::BigInt(value)) => value != "0",
        Some(Value::Float(value)) => *value != 0.0,
        Some(Value::Text(value)) => !value.is_empty(),
        Some(Value::Bytes(value)) => !value.is_empty(),
        Some(Value::Seq(value)) => !value.is_empty(),
        Some(Value::Map(value)) => !value.is_empty(),
    }
}

pub(crate) fn region_role_name(role: &str) -> bool {
    matches!(
        role,
        "toolbar" | "tabGroup" | "tabPanel" | "scrollArea" | "splitGroup" | "webArea" | "document"
    )
}

pub(crate) fn synthetic_key(kind: &str, parent: &str, members: &[&str]) -> String {
    use std::fmt::Write;

    let members_repr = members
        .iter()
        .map(|item| python_str_repr(item))
        .collect::<Vec<_>>()
        .join(", ");
    let comma = if members.len() == 1 { "," } else { "" };
    let repr = format!(
        "({}, {}, ({members_repr}{comma}))",
        python_str_repr(kind),
        python_str_repr(parent)
    );
    let mut key = format!("{kind}:");
    for byte in sha256(repr.as_bytes()).iter().take(12) {
        write!(key, "{byte:02x}").expect("format into string");
    }
    key
}

pub(crate) fn block_key(plan: &GeometricPlan, kind: &str, parent: BlockId, members: &[BlockId]) -> Result<String> {
    let parent = plan.block(parent).ok_or(GeometryError::MissingBlock(parent))?;
    let members = members
        .iter()
        .map(|&id| {
            plan.block(id)
                .map(|block| block.key.as_str())
                .ok_or(GeometryError::MissingBlock(id))
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(synthetic_key(kind, &parent.key, &members))
}

fn union(boxes: impl IntoIterator<Item = Option<Box>>) -> Option<Box> {
    let mut iter = boxes.into_iter();
    let first = iter.next()??;
    let (mut left, mut top, mut right, mut bottom) = (first.x, first.y, first.right(), first.bottom());
    for next in iter {
        let next = next?;
        if !first.comparable(&next) {
            return None;
        }
        left = left.min(next.x);
        top = top.min(next.y);
        right = right.max(next.right());
        bottom = bottom.max(next.bottom());
    }
    Some(Box {
        space: first.space,
        x: left,
        y: top,
        width: right - left,
        height: bottom - top,
    })
}
