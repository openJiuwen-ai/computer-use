// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Serialize chosen geometric blocks and preserve independent diff records.

use std::{
    collections::{HashMap, HashSet},
    rc::Rc,
};

use super::super::collections::{CollectionDocument, CollectionMemberPrefix};
use super::super::content::readable::{ReadableHeading, ReadablePlan};
use super::super::content::{FieldContentPlan, FieldPresentation, MemberMode};
use super::super::disclosure::{
    coverage::CoveragePlan, groups::GroupDisclosurePlan, DisclosurePlan,
};
use super::super::geometric::{Block, BlockId, GeometricPlan};
use super::super::numbering::NodeId;
use super::super::projection::Projection;
use super::super::sharing::{SharingPlan, TextSliceDocument};
use super::atoms::{
    atom_lines, atom_notes, coverage_lines, field, remember, Atom, AtomPlan, AtomStyle, Slot,
};
use super::lines::{reference_set, text_items, Fields};
use super::{RenderedNode, RenderedSection, TextError};

mod collections;
pub(crate) mod groups;
mod inline;
mod traversal;
mod width;

use traversal::{LayoutContext, Work};

type Result<T> = std::result::Result<T, TextError>;
type Context = [(String, String)];

#[derive(Default)]
pub(crate) struct WriterOptions<'a> {
    pub(crate) readable: bool,
    pub(crate) geometric_v2: bool,
    pub(crate) disclosure: Option<&'a DisclosurePlan>,
    pub(crate) styles: HashMap<NodeId, AtomStyle<'a>>,
    visual_rows: HashMap<NodeId, Vec<String>>,
    shared_slots: HashMap<NodeId, Vec<String>>,
    shared_actions: HashMap<NodeId, Vec<usize>>,
    slices: Option<&'a TextSliceDocument>,
    pub(crate) groups: Option<&'a GroupDisclosurePlan>,
    pub(crate) collections: Option<&'a CollectionDocument>,
    pub(crate) notes_only: HashSet<NodeId>,
    pub(crate) omit_coverage: HashSet<NodeId>,
    pub(crate) fields: Option<&'a FieldContentPlan>,
    pub(crate) headings: Option<&'a ReadablePlan>,
    pub(crate) declarations: HashMap<BlockId, Vec<String>>,
    pub(crate) grid_columns: HashMap<BlockId, usize>,
    pub(crate) card_rows: HashSet<BlockId>,
}

impl<'a> WriterOptions<'a> {
    pub(crate) fn with_outside_window(mut self, refs: &HashSet<NodeId>) -> Self {
        for id in refs {
            self.styles.entry(*id).or_default().outside_window = true;
        }
        self
    }

    pub(crate) fn from_plans(
        plan: &GeometricPlan,
        atoms: &AtomPlan,
        disclosure: Option<&'a DisclosurePlan>,
        fields: Option<&'a FieldContentPlan>,
        readable: Option<&'a ReadablePlan>,
        coverage: Option<&CoveragePlan<BlockId>>,
    ) -> Result<Self> {
        let mut options = Self {
            geometric_v2: plan.enabled,
            readable: readable.is_some(),
            disclosure,
            fields,
            headings: readable,
            ..Self::default()
        };
        if let Some(readable) = readable {
            for (id, style) in &readable.nodes {
                options.styles.insert(
                    *id,
                    AtomStyle {
                        glyph: style.glyph,
                        note_slots: &style.note_slots,
                        block_slots: &style.block_slots,
                        note_actions: &style.note_actions,
                        toggle_slot: style.toggle_slot,
                        ..AtomStyle::default()
                    },
                );
                if style.notes_only {
                    options.notes_only.insert(*id);
                }
                if !style.visual_row.is_empty() {
                    let row = style
                        .visual_row
                        .iter()
                        .map(|id| {
                            atoms
                                .source_references
                                .get(id)
                                .cloned()
                                .ok_or(TextError::Invalid("readable visual row source"))
                        })
                        .collect::<Result<_>>()?;
                    options.visual_rows.insert(*id, row);
                }
            }
        }
        if let Some(coverage) = coverage {
            options.omit_coverage.clone_from(&coverage.omitted_refs);
            for (block, groups) in &coverage.by_block {
                let statements = options.declarations.entry(*block).or_default();
                for group in groups {
                    let refs = group
                        .refs
                        .iter()
                        .map(|id| {
                            atoms
                                .source_references
                                .get(id)
                                .map(String::as_str)
                                .ok_or(TextError::Invalid("shared coverage source"))
                        })
                        .collect::<Result<Vec<_>>>()?;
                    statements.push(format!(
                        "Children [elements {}]: 0/{} read; partial: {}; inspect each with element(ref).read()",
                        reference_set(refs.into_iter())?,
                        group.total,
                        field(&group.reason)
                    ));
                }
            }
        }
        for pattern in &plan.geometric_patterns {
            if pattern.kind == "cards" {
                options
                    .card_rows
                    .extend(pattern.rows.iter().filter_map(|row| plan.by_key(&row.key)));
            } else if pattern.kind == "grid" {
                let rows: Vec<_> = pattern
                    .rows
                    .iter()
                    .filter_map(|row| plan.by_key(&row.key))
                    .collect();
                for id in plan.active_ids() {
                    if plan.block(id).is_some_and(|block| {
                        block.layout == "visual-grid" && block.children == rows
                    }) {
                        options.grid_columns.insert(id, pattern.column_count);
                    }
                }
            }
        }
        Ok(options)
    }

    pub(crate) fn with_sharing(
        mut self,
        atoms: &AtomPlan,
        sharing: Option<&SharingPlan>,
        slices: Option<&'a TextSliceDocument>,
    ) -> Result<Self> {
        self.slices = slices;
        let Some(sharing) = sharing else {
            return Ok(self);
        };
        self.shared_slots = sharing
            .omitted_slots
            .iter()
            .map(|(id, values)| (*id, values.iter().cloned().collect()))
            .collect();
        self.shared_actions = sharing
            .omitted_actions
            .iter()
            .map(|(id, values)| (*id, values.iter().copied().collect()))
            .collect();
        let refs = |ids: &[NodeId]| -> Result<String> {
            let refs = ids
                .iter()
                .map(|id| {
                    atoms
                        .source_references
                        .get(id)
                        .map(String::as_str)
                        .ok_or(TextError::Invalid("shared declaration source"))
                })
                .collect::<Result<Vec<_>>>()?;
            reference_set(refs.into_iter())
        };
        for (block, groups) in &sharing.by_block {
            let mut statements = Vec::new();
            for group in &groups.actions {
                let labels: Vec<_> = group
                    .actions
                    .iter()
                    .map(|action| {
                        let label = field(&action.label);
                        if action.ambiguous {
                            format!("{label} [name: {}]", super::quoted(&action.name))
                        } else {
                            label
                        }
                    })
                    .collect();
                statements.push(format!(
                    "Secondary Actions [elements {}]: {}",
                    refs(&group.refs)?,
                    labels.join(", ")
                ));
            }
            for (index, group) in groups.fields.iter().enumerate() {
                if self
                    .headings
                    .and_then(|plan| plan.heading(*block))
                    .and_then(|heading| heading.shared_index)
                    == Some(index)
                {
                    continue;
                }
                let label = group.slot.label.unwrap_or(&group.slot.key);
                let value = super::atoms::slot_value(&group.slot)?;
                statements.push(format!(
                    "{label} [elements {}]: {value}",
                    refs(&group.refs)?
                ));
            }
            statements.extend(self.declarations.remove(block).unwrap_or_default());
            self.declarations.insert(*block, statements);
        }
        Ok(self)
    }
}

fn nested(context: &Context, key: &str, heading: &str) -> Vec<(String, String)> {
    let mut context = context.to_vec();
    context.push((key.to_owned(), heading.to_owned()));
    context
}

fn append_padded(lines: &mut Vec<String>, depth: usize, values: impl IntoIterator<Item = String>) {
    let indent = "\t".repeat(depth);
    lines.extend(values.into_iter().map(|line| format!("{indent}{line}")));
}

fn floating_row(cells: &[(Option<usize>, String)], columns: usize) -> Result<String> {
    let mut previous = None;
    let mut line = String::new();
    for (index, (slot, text)) in cells.iter().enumerate() {
        if let Some(slot) = slot {
            if *slot >= columns || previous.is_some_and(|previous| *slot <= previous) {
                return Err(TextError::Invalid("floating column order"));
            }
            if let Some(previous) = previous {
                line.push_str(&" | ".repeat(slot - previous));
            } else {
                if index != 0 {
                    line.push_str(" ; ");
                }
                line.push_str(&" | ".repeat(*slot));
            }
            previous = Some(*slot);
        } else if index != 0 {
            line.push_str(" ; ");
        }
        line.push_str(text);
    }
    if let Some(previous) = previous {
        line.push_str(&" | ".repeat(columns - previous - 1));
    }
    Ok(line)
}

fn compact_grid(rows: &[Vec<String>], alignments: &[&str]) -> Result<Vec<String>> {
    if rows.is_empty() {
        return Ok(Vec::new());
    }
    let count = alignments.len();
    if rows.iter().any(|row| row.len() != count)
        || alignments
            .iter()
            .any(|kind| !matches!(*kind, "left" | "right" | "center"))
    {
        return Err(TextError::Invalid("compact grid shape"));
    }
    let widths: Vec<Vec<_>> = rows
        .iter()
        .map(|row| row.iter().map(|cell| width::width(cell)).collect())
        .collect();
    let padding: Vec<_> = (0..count)
        .map(|column| {
            let widest = widths.iter().map(|row| row[column]).max().unwrap_or(0);
            let narrowest = widths.iter().map(|row| row[column]).min().unwrap_or(0);
            (widest - narrowest <= 4 && rows.iter().all(|row| !row[column].contains('\u{200d}')))
                .then_some(widest)
        })
        .collect();
    Ok(rows
        .iter()
        .zip(widths)
        .map(|(row, widths)| {
            row.iter()
                .zip(widths)
                .zip(alignments)
                .enumerate()
                .map(|(index, ((text, width), alignment))| {
                    let extra = padding[index].map_or(0, |max| max - width);
                    let left = match *alignment {
                        "right" => extra,
                        "center" => extra / 2,
                        _ => 0,
                    };
                    let right = if index + 1 < count { extra - left } else { 0 };
                    format!("{}{text}{}", " ".repeat(left), " ".repeat(right))
                })
                .collect::<Vec<_>>()
                .join(" | ")
        })
        .collect())
}

struct Writer<'a> {
    plan: &'a GeometricPlan,
    projection: &'a Projection,
    atoms: &'a AtomPlan,
    options: &'a WriterOptions<'a>,
    lines: Vec<String>,
    nodes: Vec<RenderedNode>,
    order: Vec<NodeId>,
    seen: HashSet<NodeId>,
    menus: Vec<NodeId>,
    declaration_counts: HashMap<BlockId, usize>,
    group_sections: HashSet<String>,
    background: HashMap<String, Vec<String>>,
    depths: HashMap<NodeId, usize>,
    work: Vec<Work<'a>>,
}

impl<'a> Writer<'a> {
    fn atom(&self, id: NodeId) -> Result<&'a Atom> {
        self.atoms
            .atoms
            .get(&id)
            .ok_or(TextError::Invalid("geometric atom"))
    }

    fn block(&self, id: BlockId) -> Result<&'a Block> {
        self.plan
            .block(id)
            .ok_or(TextError::Invalid("geometric block"))
    }

    fn atom_role(&self, id: NodeId) -> Result<String> {
        let atom = self.atom(id)?;
        let mut label = field(&atom.role);
        if !atom.states.is_empty() {
            label.push_str(&format!(" ({})", atom.states.join(", ")));
        }
        Ok(label)
    }

    fn style(&self, id: NodeId) -> AtomStyle<'a> {
        let mut style = self.options.styles.get(&id).copied().unwrap_or_default();
        style.shared_index_basis = self.atoms.shared_index_basis;
        style.visual_row = self.options.visual_rows.get(&id).map_or(&[], Vec::as_slice);
        style.omit_slots = self
            .options
            .shared_slots
            .get(&id)
            .map_or(style.omit_slots, Vec::as_slice);
        style.omit_actions = self
            .options
            .shared_actions
            .get(&id)
            .map_or(style.omit_actions, Vec::as_slice);
        style.note_omit_actions = style.omit_actions;
        style
    }

    fn values(&self, id: NodeId, slots: &[Slot], shared: bool) -> Result<HashMap<String, String>> {
        let mut values = if shared {
            self.style(id).value_overrides.cloned().unwrap_or_default()
        } else {
            HashMap::new()
        };
        for slot in slots {
            if values.contains_key(&slot.key) {
                continue;
            }
            if let Some(slice) = self
                .options
                .slices
                .filter(|_| shared)
                .and_then(|slices| slices.get(id, &slot.key))
            {
                values.insert(slot.key.clone(), slice.statement());
            } else if let Some(excerpt) = self
                .options
                .disclosure
                .and_then(|disclosure| disclosure.get(id, &slot.key))
            {
                if slot.value.as_str() != Some(&excerpt.source.value) {
                    return Err(TextError::Invalid("prepared excerpt does not match source"));
                }
                let fragments: Vec<_> = excerpt
                    .fragments
                    .iter()
                    .map(|fragment| field(&fragment.text))
                    .collect();
                values.insert(
                    slot.key.clone(),
                    format!(
                        "{} [excerpt: {}/{} chars; captured details: element {}]",
                        fragments.join(" … "),
                        excerpt.shown,
                        excerpt.total,
                        excerpt.source.reference,
                    ),
                );
            }
        }
        Ok(values)
    }

    fn atom_lines(
        &self,
        id: NodeId,
        short: bool,
        shared: bool,
        slots: Option<&[Slot]>,
    ) -> Result<Vec<String>> {
        self.atom_lines_with_prefix(id, short, shared, slots, None)
    }

    fn atom_lines_with_prefix(
        &self,
        id: NodeId,
        short: bool,
        shared: bool,
        slots: Option<&[Slot]>,
        member: Option<&CollectionMemberPrefix>,
    ) -> Result<Vec<String>> {
        let atom = self.atom(id)?;
        let values = self.values(id, slots.unwrap_or(&atom.slots), shared)?;
        let mut style = self.style(id);
        style.slots = slots;
        style.short = short;
        style.value_overrides = Some(&values);
        style.include_native_notes = !shared;
        if !shared {
            style.omit_slots = &[];
            style.omit_actions = &[];
            style.label_overrides = None;
        }
        let mut labels = style.label_overrides.cloned().unwrap_or_default();
        if let Some(slices) = self.options.slices.filter(|_| shared) {
            for slot in slots.unwrap_or(&atom.slots) {
                if slices.anchors.contains(&(id, slot.key.clone())) {
                    labels.insert(slot.key.clone(), super::atoms::capitalize_key(&slot.key));
                } else if slot.label.is_none() && slices.get(id, &slot.key).is_some() {
                    labels.insert(slot.key.clone(), "Text".to_owned());
                }
            }
        }
        if !labels.is_empty() {
            style.label_overrides = Some(&labels);
        }
        let mut omissions = style.omit_slots.to_vec();
        if let Some(member) = member {
            omissions.extend(member.slot_keys.iter().cloned());
            style.omit_slots = &omissions;
            style.states_to_remove = &member.state_indices;
            style.prefix_member = true;
        }
        atom_lines(atom, &style, self.atoms)
    }

    fn remember(
        &mut self,
        id: NodeId,
        depth: usize,
        context: &Context,
        slots: Option<&[Slot]>,
    ) -> Result<()> {
        if !self.seen.insert(id) {
            return Err(TextError::Invalid("geometric duplicate target"));
        }
        let atom = self.atom(id)?;
        if atom.coverage.unexpanded_menu {
            self.menus.push(id);
        }
        let values = self.values(id, slots.unwrap_or(&atom.slots), false)?;
        let mut style = self.style(id);
        style.omit_slots = &[];
        style.omit_actions = &[];
        style.label_overrides = None;
        style.slots = slots;
        style.value_overrides = Some(&values);
        style.include_native_notes = true;
        let related = self
            .projection
            .node(id)
            .ok_or(TextError::Invalid("geometric projected target"))?
            .seq("labelTargets");
        let related = text_items(related)?
            .into_iter()
            .map(str::to_owned)
            .collect();
        let mut recovery = Vec::new();
        if let Some(disclosure) = self.options.disclosure {
            for (_, excerpt) in disclosure.entries().filter(|(owner, _)| *owner == id) {
                recovery.push(format!(
                    "Omitted: {} of {} characters from {}; captured details: element {}",
                    excerpt.omitted(),
                    excerpt.total,
                    excerpt.source.attribute,
                    atom.reference,
                ));
            }
        }
        if let Some(groups) = self.options.groups {
            for group in groups
                .groups
                .iter()
                .filter(|group| group.projected_refs.first() == Some(&id))
            {
                let refs = group
                    .projected_refs
                    .iter()
                    .map(|id| self.atom(*id).map(|atom| atom.reference.as_str()))
                    .collect::<Result<Vec<_>>>()?;
                recovery.push(format!(
                    "Omitted: captured content for elements {}; captured details: section {}",
                    reference_set(refs.into_iter())?,
                    group.section_key
                ));
            }
        }
        self.nodes.push(remember(
            atom, self.atoms, depth, context, &style, related, &recovery,
        )?);
        self.order.push(id);
        Ok(())
    }

    fn coverage(&mut self, id: NodeId, depth: usize) -> Result<()> {
        let atom = self.atom(id)?;
        if !self.options.omit_coverage.contains(&id) {
            append_padded(&mut self.lines, depth + 1, coverage_lines(atom));
        }
        Ok(())
    }

    fn write_atom(
        &mut self,
        id: NodeId,
        depth: usize,
        context: &Context,
        short: bool,
    ) -> Result<()> {
        self.remember(id, depth, context, None)?;
        if !self.options.notes_only.contains(&id) {
            let lines = self.atom_lines(id, short, true, None)?;
            append_padded(&mut self.lines, depth, lines);
            self.coverage(id, depth)?;
        }
        Ok(())
    }

    fn declarations(&mut self, id: BlockId, depth: usize, at: Option<usize>) {
        if let Some(lines) = self
            .options
            .declarations
            .get(&id)
            .filter(|lines| !lines.is_empty())
        {
            *self.declaration_counts.entry(id).or_insert(0) += 1;
            let prefix = "\t".repeat(depth + 1);
            let at = at.unwrap_or(self.lines.len()).min(self.lines.len());
            self.lines
                .splice(at..at, lines.iter().map(|line| format!("{prefix}{line}")));
        }
    }

    fn capture_lines(
        &mut self,
        id: BlockId,
        depth: usize,
        start: usize,
    ) -> Result<(String, Vec<String>)> {
        let mut lines = self.lines.split_off(start).into_iter();
        if let Some(main) = lines.next() {
            let indent = "\t".repeat(depth);
            return Ok((
                main.strip_prefix(&indent).unwrap_or(&main).to_owned(),
                lines.collect(),
            ));
        }
        if self
            .block(id)?
            .primary_ref
            .is_some_and(|id| self.options.notes_only.contains(&id))
        {
            return Ok((String::new(), Vec::new()));
        }
        Err(TextError::Invalid("geometric inline unit has no content"))
    }

    fn packed_atom(&self, id: NodeId, depth: usize, short: bool) -> Result<(String, Vec<String>)> {
        let lines = self.atom_lines(id, short, true, None)?;
        self.pack_lines(id, depth, lines)
    }

    fn pack_lines(
        &self,
        id: NodeId,
        depth: usize,
        lines: Vec<String>,
    ) -> Result<(String, Vec<String>)> {
        if !self.options.readable {
            return Ok((lines.join(" "), Vec::new()));
        }
        let mut lines = lines.into_iter();
        let head = lines
            .next()
            .ok_or(TextError::Invalid("geometric packed atom"))?;
        let lines: Vec<_> = lines.collect();
        if lines.is_empty() {
            return Ok((head, lines));
        }
        let indent = "\t".repeat(depth + 1);
        let reference = &self.atom(id)?.reference;
        let trailing = std::iter::once(format!("{indent}{reference}:"))
            .chain(lines.into_iter().map(|line| format!("{indent}{line}")))
            .collect();
        Ok((head, trailing))
    }

    fn write_field_heading(
        &mut self,
        id: NodeId,
        depth: usize,
        context: &Context,
    ) -> Result<String> {
        self.remember(id, depth, context, None)?;
        let mut lines = self.atom_lines(id, false, true, None)?.into_iter();
        let indent = "\t".repeat(depth);
        let atom = lines
            .next()
            .ok_or(TextError::Invalid("geometric field heading"))?;
        let heading = format!("{indent}Field: {atom}");
        self.lines.push(heading.clone());
        append_padded(&mut self.lines, depth, lines);
        self.coverage(id, depth)?;
        Ok(heading)
    }

    fn field_heading(
        &mut self,
        plan: &ReadableHeading,
        depth: usize,
        context: &Context,
    ) -> Result<String> {
        self.remember(plan.reference, depth, context, None)?;
        if plan.transparent {
            return Ok(String::new());
        }
        let indent = "\t".repeat(depth);
        let line = if let Some(slot) = &plan.caption {
            let source = *plan
                .sources
                .first()
                .ok_or(TextError::Invalid("geometric heading source"))?;
            let mut values = self.values(source, std::slice::from_ref(slot), false)?;
            let text = values
                .remove(&slot.key)
                .map_or_else(|| super::atoms::slot_value(slot), Ok)?;
            let refs = plan
                .sources
                .iter()
                .map(|source| self.atom(*source).map(|atom| atom.reference.as_str()))
                .collect::<Result<Vec<_>>>()?;
            format!(
                "{indent}{text} [elements {}]:",
                reference_set(refs.into_iter())?
            )
        } else {
            format!("{indent}{} Field:", self.atom(plan.reference)?.reference)
        };
        self.lines.push(line.clone());
        Ok(line)
    }

    fn write_field(
        &mut self,
        block: &Block,
        plan: &FieldPresentation,
        mut depth: usize,
        context: &Context,
    ) -> Result<()> {
        let inline = block.layout == "control-unit";
        let mut context = context.to_vec();
        if let Some(heading) = plan.heading.filter(|_| !inline) {
            let heading = self.write_field_heading(heading, depth, &context)?;
            context.push((block.key.clone(), heading));
            depth += 1;
        }
        self.remember(plan.target, depth, &context, Some(&plan.slots))?;
        let target_line = self.lines.len();
        let lines = self.atom_lines(plan.target, false, true, Some(&plan.slots))?;
        append_padded(&mut self.lines, depth, lines);
        self.coverage(plan.target, depth)?;
        let heading = format!(
            "{}{}",
            "\t".repeat(depth),
            self.atom_lines(plan.target, false, false, Some(&plan.slots))?[0]
        );
        let context = nested(&context, &block.key, &heading);
        for member in &plan.members {
            let id = &member.source;
            if Some(*id) == plan.heading && !inline {
                continue;
            }
            self.remember(*id, depth + 1, &context, None)?;
            match member.mode {
                MemberMode::Complete if !self.options.notes_only.contains(id) => {
                    let mut lines = self.atom_lines(*id, false, true, None)?.into_iter();
                    if inline {
                        let head = lines
                            .next()
                            .ok_or(TextError::Invalid("geometric field member"))?;
                        self.lines[target_line]
                            .push_str(&format!(" [{}]", head.trim_start_matches('\t')));
                    }
                    append_padded(&mut self.lines, depth + 1, lines);
                    self.coverage(*id, depth + 1)?;
                }
                MemberMode::CaptionReference => {
                    let atom = self.atom(*id)?;
                    self.lines[target_line].push_str(&format!(
                        " [{}: {}]",
                        field(&atom.role),
                        atom.reference
                    ));
                }
                MemberMode::ActionSource => {
                    let mut actions: Vec<(&str, Vec<String>)> = Vec::new();
                    for action in &self.atom(*id)?.actions {
                        if self
                            .style(*id)
                            .note_actions
                            .contains(&action.catalogue_index)
                        {
                            continue;
                        }
                        let mut label = field(&action.label);
                        if action.ambiguous {
                            label.push_str(&format!(" [name: {}]", super::quoted(&action.name)));
                        }
                        if let Some((_, labels)) = actions
                            .iter_mut()
                            .find(|(owner, _)| *owner == action.reference.as_str())
                        {
                            labels.push(label);
                        } else {
                            actions.push((&action.reference, vec![label]));
                        }
                    }
                    append_padded(
                        &mut self.lines,
                        depth + 1,
                        actions.into_iter().map(|(owner, labels)| {
                            format!("Secondary Actions [element {owner}]: {}", labels.join(", "))
                        }),
                    );
                }
                _ => {}
            }
        }
        Ok(())
    }

    fn write_bound_sources(
        &mut self,
        block: &Block,
        depth: usize,
        context: &Context,
    ) -> Result<()> {
        let mut refs = Vec::new();
        for member in &block.children {
            let child = self.block(*member)?;
            if !child.children.is_empty()
                || child
                    .primary_ref
                    .is_none_or(|reference| refs.contains(&reference))
            {
                return Err(TextError::Invalid("geometric unit source inventory"));
            }
            refs.extend(child.primary_ref);
        }
        let target = block
            .field_target_ref
            .filter(|target| refs.contains(target))
            .ok_or(TextError::Invalid("geometric unit target"))?;
        self.remember(target, depth, context, None)?;
        let mut main = self.atom_lines(target, false, true, None)?.into_iter();
        let mut line = main
            .next()
            .ok_or(TextError::Invalid("geometric unit target content"))?;
        let mut trailing = Vec::new();
        append_padded(&mut trailing, depth, main);
        for id in &refs {
            if *id == target {
                continue;
            }
            self.remember(*id, depth + 1, context, None)?;
            if self.options.notes_only.contains(id) {
                continue;
            }
            let mut atom = self.atom_lines(*id, false, true, None)?.into_iter();
            let head = atom
                .next()
                .ok_or(TextError::Invalid("geometric unit member content"))?;
            line.push_str(&format!(" [{head}]"));
            append_padded(&mut trailing, depth + 1, atom);
        }
        self.lines.push(format!("{}{line}", "\t".repeat(depth)));
        self.lines.extend(trailing);
        for id in refs {
            self.coverage(id, depth)?;
        }
        let mut pending: Vec<_> = block.children.iter().rev().copied().collect();
        while let Some(member) = pending.pop() {
            self.declarations(member, depth, None);
            pending.extend(self.block(member)?.children.iter().rev().copied());
        }
        Ok(())
    }

    fn write_records(&mut self, block: &Block, depth: usize, context: &Context) -> Result<()> {
        if block.columns.len() != block.column_prefix_refs.len() {
            return Err(TextError::Invalid("record column prefixes"));
        }
        let indent = "\t".repeat(depth);
        let headings = block
            .columns
            .iter()
            .zip(&block.column_prefix_refs)
            .map(|(label, owner)| {
                owner.map_or_else(|| Ok(label.clone()), |owner| self.atom_role(owner))
            })
            .collect::<Result<Vec<_>>>()?;
        let label = if block.origin == "visual" {
            "Visual records"
        } else {
            "Records"
        };
        let heading = format!("{indent}{label}: {}", headings.join(" | "));
        self.lines.push(heading.clone());
        let mut matrix = Vec::new();
        let mut irregular = HashMap::new();
        let mut annotations = HashMap::new();
        for row_id in &block.children {
            let row = self.block(*row_id)?;
            let row_context = nested(context, &row.key, &heading);
            let mut cells = vec![String::new(); block.columns.len()];
            let mut ordered = Vec::new();
            let mut trailing = Vec::new();
            let defaults: Vec<_> = (0..row.children.len()).map(Some).collect();
            let slots = if row.column_slots.is_empty() {
                &defaults
            } else {
                &row.column_slots
            };
            if slots.len() != row.children.len() {
                return Err(TextError::Invalid("geometric record slots"));
            }
            for (slot, member) in slots.iter().zip(&row.children) {
                let member = self.block(*member)?;
                let id = member
                    .primary_ref
                    .filter(|_| member.children.is_empty())
                    .ok_or(TextError::Invalid("geometric record cell"))?;
                self.remember(id, depth + 1, &row_context, None)?;
                let prefix = slot
                    .and_then(|slot| block.column_prefix_refs.get(slot))
                    .copied()
                    .flatten()
                    .is_some();
                let (main, rest) = self.packed_atom(id, depth + 1, prefix)?;
                if let Some(slot) = slot {
                    cells
                        .get_mut(*slot)
                        .ok_or(TextError::Invalid("geometric record column"))?
                        .clone_from(&main);
                }
                ordered.push((*slot, main));
                trailing.extend(rest);
            }
            annotations.insert(*row_id, trailing);
            if slots.contains(&None) {
                irregular.insert(*row_id, floating_row(&ordered, cells.len())?);
            } else {
                matrix.push(cells);
            }
        }
        let rows = if block.column_alignments.is_empty() {
            matrix.into_iter().map(|cells| cells.join(" | ")).collect()
        } else {
            compact_grid(&matrix, &block.column_alignments)?
        };
        let mut ordinary = rows.into_iter();
        for row_id in &block.children {
            let line = irregular
                .remove(row_id)
                .or_else(|| ordinary.next())
                .ok_or(TextError::Invalid("geometric record row"))?;
            self.lines.push(format!("{indent}\t{line}"));
            self.lines
                .extend(annotations.remove(row_id).unwrap_or_default());
            self.declarations(*row_id, depth + 1, None);
            for member in &self.block(*row_id)?.children {
                self.declarations(*member, depth + 1, None);
                let id = self
                    .block(*member)?
                    .primary_ref
                    .ok_or(TextError::Invalid("geometric record owner"))?;
                self.coverage(id, depth + 1)?;
            }
        }
        Ok(())
    }

    fn write_chain(&mut self, block: &Block, depth: usize, context: &LayoutContext) -> Result<()> {
        let mut current = block;
        let mut member_depth = depth;
        let mut cells = Vec::new();
        let mut annotations = Vec::new();
        let mut members = Vec::new();
        loop {
            let id = current
                .primary_ref
                .ok_or(TextError::Invalid("geometric chain target"))?;
            self.remember(id, member_depth, context, None)?;
            let mut lines = self.atom_lines(id, false, true, None)?.into_iter();
            cells.push(
                lines
                    .next()
                    .ok_or(TextError::Invalid("geometric chain cell"))?,
            );
            annotations.extend(lines);
            if !matches!(current.layout, "chain" | "record-path") {
                break;
            }
            let Some(&child) = current.children.first() else {
                return Err(TextError::Invalid("geometric chain child"));
            };
            members.push(child);
            current = self.block(child)?;
            member_depth += 1;
        }
        self.lines
            .push(format!("{}{}", "\t".repeat(depth), cells.join(" > ")));
        append_padded(&mut self.lines, depth, annotations);
        for child in members {
            self.declarations(child, depth, None);
        }
        let start = self.lines.len();
        let padding = "\t".repeat(member_depth - depth);
        if !padding.is_empty() {
            self.defer(move |writer| {
                for line in &mut writer.lines[start..] {
                    if line.starts_with(&padding) {
                        line.drain(..padding.len());
                    }
                }
                Ok(())
            });
        }
        self.children(&current.children, member_depth + 1, context, false);
        Ok(())
    }

    fn write_block(
        &mut self,
        id: BlockId,
        depth: usize,
        context: LayoutContext,
        inherited_prefix: bool,
    ) -> Result<()> {
        let block = self.block(id)?;
        if self
            .options
            .groups
            .is_some_and(|groups| groups.groups.iter().any(|group| group.parent == id))
        {
            return self.write_grouped(block, id, depth, context);
        }
        if let Some(field) = self.options.fields.and_then(|fields| fields.get(id)) {
            return self.write_field(block, field, depth, &context);
        }
        if let Some(table) = self
            .options
            .collections
            .and_then(|collections| collections.tables.get(&block.primary_ref?))
        {
            return self.write_table(table, depth, context);
        }
        let indent = "\t".repeat(depth);
        match block.layout {
            "source-only" => {
                let id = block
                    .primary_ref
                    .filter(|_| block.children.is_empty())
                    .ok_or(TextError::Invalid("geometric source-only target"))?;
                return self.write_atom(id, depth, &context, false);
            }
            "control-unit" | "compound-control" => {
                return self.write_bound_sources(block, depth, &context)
            }
            "visual-row" => {
                return self.write_visual_row(
                    &block.children,
                    depth,
                    context,
                    Vec::new(),
                    Vec::new(),
                );
            }
            "visual-grid" => return self.write_grid(id, block, depth, context),
            "compact-list" => {
                let prefix = self.atom_role(
                    block
                        .shared_prefix_ref
                        .ok_or(TextError::Invalid("geometric group prefix"))?,
                )?;
                let mut refs = Vec::new();
                for child in &block.children {
                    let id = self
                        .block(*child)?
                        .primary_ref
                        .ok_or(TextError::Invalid("geometric compact list target"))?;
                    self.remember(id, depth + 1, &context, None)?;
                    refs.push(self.atom(id)?.reference.as_str());
                }
                self.lines.push(format!(
                    "{indent}Elements [{prefix}]: {}",
                    reference_set(refs.into_iter())?
                ));
                return Ok(());
            }
            "record-blocks" => {
                let prefix = block
                    .shared_prefix_ref
                    .map(|id| self.atom_role(id))
                    .transpose()?;
                let label = if !block.children.is_empty()
                    && block
                        .children
                        .iter()
                        .all(|id| self.options.card_rows.contains(id))
                {
                    "Items"
                } else {
                    "Records (visual grouping)"
                };
                let prefix = prefix.map_or_else(String::new, |prefix| format!(" [{prefix}]"));
                let heading = format!("{indent}{label}{prefix}:");
                self.lines.push(heading.clone());
                let context = nested(&context, &block.key, &heading).into();
                self.children(
                    &block.children,
                    depth + 1,
                    &context,
                    block.shared_prefix_ref.is_some(),
                );
                return Ok(());
            }
            "chain" | "record-path" => return self.write_chain(block, depth, &context),
            "rows" => {
                let prefix = self.atom_role(
                    block
                        .shared_prefix_ref
                        .ok_or(TextError::Invalid("geometric row prefix"))?,
                )?;
                let heading = format!("{indent}Elements [{prefix}]:");
                self.lines.push(heading.clone());
                let context = nested(&context, &block.key, &heading).into();
                self.children(&block.children, depth + 1, &context, true);
                return Ok(());
            }
            "records" => return self.write_records(block, depth, &context),
            "inline" => {
                let prefix = block
                    .shared_prefix_ref
                    .map(|id| self.atom_role(id))
                    .transpose()?;
                let heading = prefix.as_ref().map_or_else(
                    || format!("{indent}Visual row:"),
                    |prefix| format!("{indent}Elements [{prefix}]:"),
                );
                let child_context = if inherited_prefix {
                    context.to_vec()
                } else {
                    self.lines.push(heading.clone());
                    nested(&context, &block.key, &heading)
                };
                let child_depth = depth + usize::from(!inherited_prefix);
                let mut cells = Vec::new();
                let mut trailing = Vec::new();
                for child in &block.children {
                    let child = self.block(*child)?;
                    let id = child
                        .primary_ref
                        .filter(|_| child.children.is_empty())
                        .ok_or(TextError::Invalid("geometric inline member"))?;
                    self.remember(id, child_depth, &child_context, None)?;
                    let (main, rest) = self.packed_atom(id, child_depth, prefix.is_some())?;
                    cells.push(main);
                    trailing.extend(rest);
                }
                self.lines
                    .push(format!("{}{}", "\t".repeat(child_depth), cells.join(" | ")));
                self.lines.extend(trailing);
                return Ok(());
            }
            _ => {}
        }
        if block.kind == "field" && block.primary_ref.is_none() {
            return self.write_field_block(id, block, depth, context);
        }
        if block.kind == "region" {
            let role = match block.region_role {
                Some("navigation") => "Navigation",
                Some("toolbar") => "Toolbar",
                _ => "Region",
            };
            let position = block
                .position
                .as_ref()
                .map_or_else(String::new, |position| format!(" [{position}]"));
            let mut heading = format!("{indent}{role}{position}:");
            let label = block.primary_ref.or(block.label_ref);
            let mut trailing = Vec::new();
            if let Some(label) = label {
                self.remember(label, depth, &context, None)?;
                let mut lines = self.atom_lines(label, false, true, None)?.into_iter();
                let main = lines
                    .next()
                    .ok_or(TextError::Invalid("geometric region label"))?;
                heading.push_str(&format!(" {main}"));
                trailing.extend(lines);
            }
            self.lines.push(heading.clone());
            append_padded(&mut self.lines, depth, trailing);
            if let Some(label) = label {
                self.coverage(label, depth)?;
            }
            let context: LayoutContext = nested(&context, &block.key, &heading).into();
            for &child in block.children.iter().rev() {
                let context = Rc::clone(&context);
                self.defer(move |writer| {
                    let member = writer.block(child)?;
                    if member.primary_ref == label {
                        writer.declarations(child, depth, None);
                        writer.children(&member.children, depth + 1, &context, false);
                    } else {
                        writer.schedule(child, depth + 1, &context, false);
                    }
                    Ok(())
                });
            }
            return Ok(());
        }
        let child_depth = if let Some(id) = block.primary_ref {
            self.write_atom(
                id,
                depth,
                &context,
                inherited_prefix && block.kind == "record",
            )?;
            depth + 1
        } else {
            depth
        };
        self.children(&block.children, child_depth, &context, false);
        Ok(())
    }

    fn write_field_block(
        &mut self,
        id: BlockId,
        block: &'a Block,
        depth: usize,
        context: LayoutContext,
    ) -> Result<()> {
        let indent = "\t".repeat(depth);
        let geometric = self.options.geometric_v2
            && matches!(block.layout, "fields" | "field-inline")
            && block.label_ref.is_some();
        let at = self.lines.len();
        let heading = match (geometric, block.label_ref) {
            (true, Some(label)) => {
                if let Some(style) = self.options.headings.and_then(|plan| plan.heading(id)) {
                    self.field_heading(style, depth, &context)?
                } else if block.heading_style == "caption" {
                    self.remember(label, depth, &context, None)?;
                    let atom = self.atom(label)?;
                    let slot = atom
                        .slots
                        .first()
                        .ok_or(TextError::Invalid("geometric caption slot"))?;
                    let mut values = self.values(label, std::slice::from_ref(slot), false)?;
                    let value = values
                        .remove(&slot.key)
                        .map_or_else(|| super::atoms::slot_value(slot), Ok)?;
                    let heading = format!("{indent}{} {value}:", atom.reference);
                    self.lines.push(heading.clone());
                    heading
                } else {
                    let mut heading = self.write_field_heading(label, depth, &context)?;
                    heading.push(':');
                    self.lines[at].push(':');
                    heading
                }
            }
            (_, Some(label)) => self.write_field_heading(label, depth, &context)?,
            (_, None) => {
                let heading = format!("{indent}Field:");
                self.lines.push(heading.clone());
                heading
            }
        };
        let transparent = geometric && heading.is_empty();
        let context = if transparent {
            context
        } else {
            nested(&context, &block.key, &heading).into()
        };
        self.write_field_children(
            block,
            &block.children,
            depth,
            context,
            geometric,
            transparent,
            at,
            true,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn write_field_children(
        &mut self,
        block: &'a Block,
        children: &'a [BlockId],
        depth: usize,
        context: LayoutContext,
        geometric: bool,
        transparent: bool,
        heading: usize,
        first: bool,
    ) -> Result<()> {
        let Some((&child, rest)) = children.split_first() else {
            return Ok(());
        };
        let member = self.block(child)?;
        let label = member.primary_ref == block.label_ref;
        self.defer({
            let context = Rc::clone(&context);
            move |writer| {
                writer.write_field_children(
                    block,
                    rest,
                    depth,
                    context,
                    geometric,
                    transparent,
                    heading,
                    first && label,
                )
            }
        });
        if label {
            self.declarations(child, depth, None);
            if !geometric {
                self.children(&member.children, depth + 1, &context, false);
            }
        } else {
            if first && geometric && !transparent && block.layout == "field-inline" {
                let start = self.lines.len();
                self.defer(move |writer| {
                    if start < writer.lines.len() {
                        let line = writer.lines.remove(start);
                        writer.lines[heading].push(' ');
                        writer.lines[heading].push_str(line.trim_start_matches('\t'));
                    }
                    Ok(())
                });
            }
            self.schedule(child, depth + usize::from(!transparent), &context, false);
        }
        Ok(())
    }
}

pub(crate) struct GeometricRendered {
    pub(crate) section: RenderedSection,
    pub(crate) background: HashMap<String, Vec<String>>,
}

pub(crate) fn render_geometric(
    plan: &GeometricPlan,
    projection: &Projection,
    atoms: &AtomPlan,
    options: &WriterOptions<'_>,
) -> Result<GeometricRendered> {
    let mut depths = HashMap::new();
    if options.groups.is_some() {
        let mut pending: Vec<_> = projection.roots.iter().map(|id| (*id, 0)).collect();
        while let Some((id, depth)) = pending.pop() {
            let node = projection
                .node(id)
                .ok_or(TextError::Invalid("geometric group depth target"))?;
            if depths.insert(id, depth).is_some() {
                return Err(TextError::Invalid("geometric group depth inventory"));
            }
            pending.extend(node.children.iter().map(|id| (*id, depth + 1)));
        }
    }
    let mut writer = Writer {
        plan,
        projection,
        atoms,
        options,
        lines: Vec::new(),
        nodes: Vec::new(),
        order: Vec::new(),
        seen: HashSet::new(),
        menus: Vec::new(),
        declaration_counts: HashMap::new(),
        group_sections: HashSet::new(),
        background: HashMap::new(),
        depths,
        work: Vec::new(),
    };
    writer.run()?;
    let expected: HashSet<_> = plan.target_refs.iter().copied().collect();
    if writer.seen != expected {
        return Err(TextError::Invalid("serialized interface target inventory"));
    }
    if options
        .declarations
        .iter()
        .any(|(block, lines)| !lines.is_empty() && writer.declaration_counts.get(block) != Some(&1))
    {
        return Err(TextError::Invalid(
            "serialized interface declaration inventory",
        ));
    }
    if options.groups.is_some_and(|groups| {
        groups.groups.len() != writer.group_sections.len()
            || groups.groups.iter().any(|group| {
                !writer.group_sections.contains(&group.section_key)
                    || writer.background.contains_key(&group.section_key)
                        != group.background.is_some()
            })
    }) {
        return Err(TextError::Invalid("serialized group recovery inventory"));
    }
    let mut groups: Vec<(Option<i64>, Vec<&str>)> = Vec::new();
    for id in &writer.menus {
        let atom = writer.atom(*id)?;
        if let Some((_, group)) = groups
            .iter_mut()
            .find(|(total, _)| *total == atom.coverage.total)
        {
            group.push(&atom.reference);
        } else {
            groups.push((atom.coverage.total, vec![&atom.reference]));
        }
    }
    let suffix = if groups.is_empty() {
        String::new()
    } else {
        let groups = groups
            .into_iter()
            .map(|(total, refs)| {
                let total = total.map_or_else(
                    || "total unknown".to_owned(),
                    |total| format!("0/{total} children"),
                );
                Ok(format!("{} ({total})", reference_set(refs.into_iter())?))
            })
            .collect::<Result<Vec<_>>>()?;
        format!("Menus unexpanded: {}.", groups.join("; "))
    };
    let mut notes = Vec::new();
    for id in &writer.order {
        let atom = writer.atom(*id)?;
        let values = writer.values(*id, &atom.slots, false)?;
        let mut style = writer.style(*id);
        style.value_overrides = Some(&values);
        if let Some(note) = atom_notes(atom, &style, true)? {
            notes.push(format!("\t{note}"));
        }
    }
    if !notes.is_empty() {
        writer
            .lines
            .push("Native notes (original element references):".to_owned());
        writer.lines.extend(notes);
    }
    let mut prefix = if !options.geometric_v2
        && plan
            .active_ids()
            .filter_map(|id| plan.block(id))
            .any(|block| block.kind == "field" && block.origin == "visual")
    {
        "Fields group visually related controls; each number keeps its original target.".to_owned()
    } else {
        String::new()
    };
    if atoms.shared_index_basis {
        if !prefix.is_empty() {
            prefix.push('\n');
        }
        prefix.push_str("Index is 0-based.");
        if atoms
            .atoms
            .values()
            .flat_map(|atom| &atom.slots)
            .any(|slot| matches!(slot.key.as_str(), "rowRange" | "columnRange"))
        {
            prefix.push_str(" Row/Column are 1-based.");
        }
    }
    let mut lines = writer.lines;
    if !prefix.is_empty() {
        lines.insert(0, prefix.clone());
    }
    if !suffix.is_empty() {
        lines.push(suffix.clone());
    }
    let section = RenderedSection {
        text: lines.join("\n"),
        nodes: writer.nodes,
        prefix,
        suffix,
        refresh: false,
    };
    Ok(GeometricRendered {
        section,
        background: writer.background,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deep_blocks_and_inline_captures_fit_the_read_worker_stack() {
        use crate::{
            geometric::GeometricContext,
            geometry::Geometry,
            projection::ProjectionOptions,
            render::atoms::{plan_atoms, AtomOptions},
            value::Value,
        };

        const COUNT: usize = 2000;
        let aliases = (0..COUNT)
            .map(|index| (index.to_string(), NodeId(index)))
            .collect();
        let mut children = Vec::new();
        for index in (0..COUNT).rev() {
            let role = if index == 0 { "window" } else { "group" };
            children = vec![Value::Map(vec![
                ("ref".into(), Value::Text(index.to_string())),
                ("role".into(), Value::Text(role.to_owned())),
                ("name".into(), Value::Text(format!("Layer {index}"))),
                ("children".into(), Value::Seq(children)),
            ])];
        }
        let projection = Projection::prepare(
            children.pop().unwrap(),
            &aliases,
            ProjectionOptions::default(),
        )
        .unwrap();
        let geometry = Geometry::default();
        let mut plan = GeometricPlan::seed(
            &GeometricContext {
                projection: &projection,
                geometry: &geometry,
                facts: None,
                atoms: None,
            },
            true,
        )
        .unwrap();
        let atoms = plan_atoms(&projection, &AtomOptions::default()).unwrap();
        let options = WriterOptions::default();

        let direct = std::thread::scope(|scope| {
            std::thread::Builder::new()
                .stack_size(512 * 1024)
                .spawn_scoped(scope, || {
                    render_geometric(&plan, &projection, &atoms, &options)
                        .unwrap()
                        .section
                })
                .unwrap()
                .join()
                .unwrap()
        });
        assert_eq!(direct.nodes.len(), COUNT);
        assert_eq!(direct.text.lines().count(), COUNT);
        assert_eq!(direct.nodes[COUNT - 1].reference, (COUNT - 1).to_string());
        assert_eq!(direct.nodes[COUNT - 1].depth, COUNT - 1);
        assert!(direct.text.ends_with("Layer 1999"));

        // Both visual formats capture the child before emitting its cell.
        // Wrapping one child preserves every line and diff record.
        for reference in 0..COUNT - 1 {
            let parent = plan.source_block(NodeId(reference)).unwrap();
            let child = plan.source_block(NodeId(reference + 1)).unwrap();
            let mut row = Block::new(format!("visual:{reference}"), "tree", None, vec![child]);
            row.layout = "visual-row";
            let inline = if reference % 2 == 0 {
                row.column_slots = vec![Some(0)];
                let row = plan.insert(row);
                let mut grid = Block::new(format!("grid:{reference}"), "tree", None, vec![row]);
                grid.layout = "visual-grid";
                grid.columns = vec!["Cell".to_owned()];
                grid.column_alignments = vec!["left"];
                plan.insert(grid)
            } else {
                plan.insert(row)
            };
            plan.block_mut(parent).unwrap().children = vec![inline];
        }
        let inline = std::thread::scope(|scope| {
            std::thread::Builder::new()
                .stack_size(512 * 1024)
                .spawn_scoped(scope, || {
                    render_geometric(&plan, &projection, &atoms, &options)
                        .unwrap()
                        .section
                })
                .unwrap()
                .join()
                .unwrap()
        });
        assert_eq!(inline, direct);
    }

    #[test]
    fn aligns_unicode_cells_and_keeps_floating_items() {
        assert_eq!(
            compact_grid(
                &[
                    vec!["中文".into(), "a".into()],
                    vec!["e\u{301}".into(), "b".into()]
                ],
                &["right", "left"]
            )
            .unwrap(),
            vec!["中文 | a", "   e\u{301} | b"],
        );
        assert_eq!(
            floating_row(
                &[
                    (None, String::new()),
                    (Some(1), "part".into()),
                    (None, "extra".into())
                ],
                3
            )
            .unwrap(),
            " ;  | part ; extra | ",
        );
    }
}
