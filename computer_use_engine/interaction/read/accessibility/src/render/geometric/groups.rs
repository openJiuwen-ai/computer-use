// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Present selected content groups and their complete capture-local recovery records.

use super::super::super::disclosure::groups::{GroupDisclosurePlan, GroupKind, GroupOverview};
use super::super::super::value::Value;
use super::super::text::{json, JsonFormat};
use super::{
    append_padded, nested, Block, BlockId, LayoutContext, NodeId, Result, TextError, Writer,
};
use super::{reference_set, AtomPlan, HashMap};

fn text(value: &str) -> Value {
    Value::Text(value.to_owned())
}

fn map<const N: usize>(values: [(&str, Value); N]) -> Value {
    Value::Map(
        values
            .into_iter()
            .map(|(key, value)| (key.to_owned(), value))
            .collect(),
    )
}

fn literal(value: &Value) -> Result<String> {
    json(value, JsonFormat::Literal)
}

fn reference(atoms: &AtomPlan, id: NodeId) -> Result<&str> {
    atoms
        .source_references
        .get(&id)
        .map(String::as_str)
        .ok_or(TextError::Invalid("group source reference"))
}

fn refs(atoms: &AtomPlan, ids: &[NodeId]) -> Result<String> {
    let refs = ids
        .iter()
        .map(|id| reference(atoms, *id))
        .collect::<Result<Vec<_>>>()?;
    reference_set(refs.into_iter())
}

fn outline(group: &GroupOverview, atoms: &AtomPlan) -> Result<String> {
    group
        .outline
        .iter()
        .map(|field| {
            Ok(format!(
                "{} {}",
                reference(atoms, field.source)?,
                literal(&field.slot.value)?
            ))
        })
        .collect::<Result<Vec<_>>>()
        .map(|items| items.join("; "))
}

fn overview(group: &GroupOverview, atoms: &AtomPlan) -> Result<Vec<String>> {
    if let Some(background) = &group.background {
        let blockers: Vec<_> = background.blockers.iter().map(u64::to_string).collect();
        let mut lines = vec![format!(
            "Blocked background overview: window {}; blocked by window {}; {} captured elements. Controls remain unavailable in this background scope.",
            background.window_ref, blockers.join(", "), group.projected_refs.len(),
        )];
        if !group.outline.is_empty() {
            lines.push(format!("  Captured regions: {}", outline(group, atoms)?));
        }
        if !background.nested_windows.is_empty() {
            lines.push(format!(
                "  Nested window elements: {}; separate original references, not identified with another window capture.",
                refs(atoms, &background.nested_windows)?,
            ));
        }
        if !background.uncertain_refs.is_empty() {
            lines.push(format!(
                "  Incomplete or unknown captured facts: elements {}.",
                refs(atoms, &background.uncertain_refs)?
            ));
        }
        if !background.directory_complete {
            lines.push(
                "  Window directory incomplete; unknown window relationships remain unresolved."
                    .to_owned(),
            );
        }
        let mut states: Vec<(&str, String, Vec<NodeId>)> = Vec::new();
        for (id, key, value) in &background.state_facts {
            let value = literal(value)?;
            if let Some((_, _, members)) = states
                .iter_mut()
                .find(|(label, rendered, _)| label == key && *rendered == value)
            {
                members.push(*id);
            } else {
                states.push((key, value, vec![*id]));
            }
        }
        for (key, value, members) in states {
            lines.push(format!(
                "  Reported background state [elements {}]: {key} = {value}",
                refs(atoms, &members)?
            ));
        }
        lines.push(format!(
            "  Captured background details: section {}.",
            group.section_key
        ));
        return Ok(lines);
    }
    let count = if group.kind == GroupKind::Records {
        format!(
            "{} captured records",
            group
                .captured_records
                .ok_or(TextError::Invalid("group record count"))?
        )
    } else {
        format!("{} captured objects", group.projected_refs.len())
    };
    let mut lines = vec![format!(
        "Content overview [{}]: {count}; {} characters across captured fields; elements {}.",
        group.section_key,
        group.captured_characters,
        refs(atoms, &group.projected_refs)?,
    )];
    if !group.outline.is_empty() {
        lines.push(format!("  Outline: {}", outline(group, atoms)?));
    }
    for (label, fragments) in [
        ("First excerpt", &group.head),
        ("Last excerpt", &group.tail),
    ] {
        for part in fragments {
            lines.push(format!(
                "  {label} [element {}]: {}",
                reference(atoms, part.source.source)?,
                super::super::quoted(&part.text)
            ));
        }
    }
    lines.push(format!(
        "  Captured content details: section {}.",
        group.section_key
    ));
    Ok(lines)
}

impl<'a> Writer<'a> {
    pub(super) fn write_grouped(
        &mut self,
        block: &'a Block,
        id: BlockId,
        mut depth: usize,
        mut context: LayoutContext,
    ) -> Result<()> {
        let plan = self
            .options
            .groups
            .ok_or(TextError::Invalid("group writer plan"))?;
        let groups = plan
            .groups
            .iter()
            .filter(|group| group.parent == id)
            .filter_map(|group| group.children.first().map(|child| (*child, group)))
            .collect();
        if let Some(primary) = block.primary_ref {
            self.write_atom(primary, depth, &context, false)?;
            depth += 1;
        } else if block.kind == "region" {
            let role = match block.region_role {
                Some("navigation") => "Navigation",
                Some("toolbar") => "Toolbar",
                _ => "Region",
            };
            let position = block
                .position
                .as_ref()
                .map_or_else(String::new, |position| format!(" [{position}]"));
            let heading = format!("{}{role}{position}:", "\t".repeat(depth));
            self.lines.push(heading.clone());
            context = nested(&context, &block.key, &heading).into();
            depth += 1;
        }
        self.write_grouped_children(&block.children, depth, context, groups)
    }

    fn write_grouped_children(
        &mut self,
        mut children: &'a [BlockId],
        depth: usize,
        context: LayoutContext,
        groups: HashMap<BlockId, &'a GroupOverview>,
    ) -> Result<()> {
        while let Some((&child, rest)) = children.split_first() {
            let Some(&group) = groups.get(&child) else {
                let child_context = context.clone();
                self.defer(move |writer| {
                    writer.write_grouped_children(rest, depth, context, groups)
                });
                self.schedule(child, depth, &child_context, false);
                return Ok(());
            };
            if !children.starts_with(&group.children) {
                return Err(TextError::Invalid(
                    "content group no longer matches child range",
                ));
            }
            children = &children[group.children.len()..];
            let lines = overview(group, self.atoms)?;
            let heading = format!("{}{}", "\t".repeat(depth), lines[0]);
            append_padded(&mut self.lines, depth, lines);
            let group_context = nested(&context, &group.section_key, &heading).into();
            if group.background.is_some() {
                let start = self.lines.len();
                self.defer(move |writer| {
                    writer.finish_background(group, depth, start)?;
                    writer.register_group(group)?;
                    writer.write_grouped_children(children, depth, context, groups)
                });
                self.children(&group.children, depth, &group_context, false);
                return Ok(());
            }
            for id in &group.projected_refs {
                let depth = *self
                    .depths
                    .get(id)
                    .ok_or(TextError::Invalid("hidden group member depth"))?;
                self.remember(*id, depth, &group_context, None)?;
            }
            self.register_group(group)?;
        }
        Ok(())
    }

    fn register_group(&mut self, group: &GroupOverview) -> Result<()> {
        if !self.group_sections.insert(group.section_key.clone()) {
            return Err(TextError::Invalid("duplicate group recovery section"));
        }
        Ok(())
    }

    fn finish_background(
        &mut self,
        group: &GroupOverview,
        depth: usize,
        start: usize,
    ) -> Result<()> {
        let background = group
            .background
            .as_ref()
            .ok_or(TextError::Invalid("group writer background"))?;
        self.background
            .insert(group.section_key.clone(), self.lines.split_off(start));
        if !background.important_refs.is_empty() {
            self.lines
                .push(format!("{}Background state:", "\t".repeat(depth)));
        }
        for id in &background.important_refs {
            let index = self
                .order
                .iter()
                .position(|member| member == id)
                .ok_or(TextError::Invalid("background member"))?;
            self.lines.extend(
                super::super::physical_lines(&self.nodes[index].text)
                    .into_iter()
                    .map(str::to_owned),
            );
        }
        for id in &background.uncertain_refs {
            let atom = self.atom(*id)?;
            let indent = "\t".repeat(depth);
            let lines = super::coverage_lines(atom);
            self.lines.extend(
                lines
                    .into_iter()
                    .map(|hint| format!("{indent}Element {}: {hint}", atom.reference)),
            );
        }
        Ok(())
    }
}

pub(crate) fn directory(
    plan: &GroupDisclosurePlan,
    atoms: &AtomPlan,
) -> Result<String> {
    if plan.groups.is_empty() {
        return Ok(String::new());
    }
    let mut lines =
        vec!["Captured content directory (same observation; complete hidden fields):".to_owned()];
    for group in &plan.groups {
        lines.push(format!(
            "  {}: elements {}; {} characters across captured fields",
            group.section_key,
            refs(atoms, &group.projected_refs)?,
            group.captured_characters
        ));
    }
    Ok(lines.join("\n"))
}

pub(crate) fn payload(
    plan: &GroupDisclosurePlan,
    atoms: &AtomPlan,
    capture: [&str; 3],
    tokens: &[String],
    background: &HashMap<String, Vec<String>>,
) -> Result<String> {
    let mut lines = Vec::new();
    for group in &plan.groups {
        if group.capture.each_ref().map(String::as_str) != capture {
            return Err(TextError::Invalid("group catalogue capture identity"));
        }
        lines.push(format!("Captured content section {}:", group.section_key));
        let source = map([
            ("backend", text(capture[0])),
            ("session", text(capture[1])),
            ("observation", text(capture[2])),
        ]);
        lines.push(format!("  Source: {}", literal(&source)?));
        if group.background.is_some() {
            let content = background
                .get(&group.section_key)
                .filter(|lines| !lines.is_empty())
                .ok_or(TextError::Invalid(
                    "background payload missing original presentation",
                ))?;
            lines.push(
                "  Captured presentation (blocked background; original targets and actions):"
                    .to_owned(),
            );
            lines.extend(content.iter().cloned());
        }
        for entry in &group.entries {
            let source = reference(atoms, entry.source)?;
            let parent = entry
                .parent
                .map(|id| reference(atoms, id))
                .transpose()?
                .map_or(Value::Null, text);
            let token = tokens
                .get(entry.source.0)
                .ok_or(TextError::Invalid("group native source token"))?;
            let object = map([
                ("ref", text(source)),
                ("parent", parent),
                ("role", text(&entry.role)),
                ("native", text(token)),
            ]);
            lines.push(format!("  Object: {}", literal(&object)?));
            for field in &entry.fields {
                let mut equal = Vec::new();
                let mut other = Vec::new();
                for fact in &field.slot.facts {
                    let owner = reference(atoms, fact.key.source)?;
                    let key = &fact.key.attribute;
                    if fact.value == field.slot.value {
                        equal.push(map([("ref", text(owner)), ("field", text(key))]));
                    } else {
                        other.push(map([
                            ("ref", text(owner)),
                            ("field", text(key)),
                            ("value", fact.value.clone()),
                        ]));
                    }
                }
                let mut record = vec![
                    ("key".to_owned(), text(&field.slot.key)),
                    ("value".to_owned(), field.slot.value.clone()),
                ];
                if !equal.is_empty()
                    && equal
                        != [map([
                            ("ref", text(source)),
                            ("field", text(&field.slot.key)),
                        ])]
                {
                    record.push(("sameSources".to_owned(), Value::Seq(equal)));
                }
                if !other.is_empty() {
                    record.push(("sourceValues".to_owned(), Value::Seq(other)));
                }
                let differences = field
                    .sources
                    .iter()
                    .filter(|source| source.value != source.normalized)
                    .map(|source| {
                        Ok(map([
                            ("ref", text(reference(atoms, source.source)?)),
                            ("field", text(&source.native_field)),
                            ("value", source.value.clone()),
                        ]))
                    })
                    .collect::<Result<Vec<_>>>()?;
                if !differences.is_empty() {
                    record.push(("nativeValues".to_owned(), Value::Seq(differences)));
                }
                lines.push(format!("    Field: {}", literal(&Value::Map(record))?));
            }
        }
        lines.push(format!(
            "End captured content section {}.",
            group.section_key
        ));
    }
    Ok(lines.join("\n"))
}
