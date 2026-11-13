// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Terminal text packing. The unpacked nodes and their native bindings remain
//! available to the caller for incremental delivery.

use std::collections::{HashMap, HashSet};
use std::ops::Range;

use super::render::RenderedNode;
use crate::unicode_tables::{CATEGORY_C, DECIMAL_DIGIT, LETTER_OR_NUMBER};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum LayoutMode {
    Unpacked,
    Compact,
    Full,
}

#[derive(Debug, Default)]
pub(super) struct LayoutNode {
    pub(super) reference: String,
    pub(super) role: String,
    pub(super) subrole: Option<String>,
    pub(super) identifier: Option<String>,
    pub(super) name: Option<String>,
    pub(super) value: Option<String>,
    pub(super) description: Option<String>,
    pub(super) url: Option<String>,
    pub(super) role_description: Option<String>,
    pub(super) children: Vec<LayoutNode>,
    pub(super) visual_chain_child: Option<String>,
    pub(super) incomplete_children: bool,
    pub(super) menu_children: Option<(u64, u64, u64)>,
    pub(super) anonymous_group: bool,
    pub(super) fact_backed_group_wrapper: bool,
    pub(super) anonymous_control_wrapper: bool,
    pub(super) ordinary_control: bool,
    pub(super) enabled: Option<bool>,
    pub(super) blocked: bool,
    pub(super) unknown_input_pair: bool,
}

impl LayoutNode {
    pub(super) fn new(reference: String, role: String, children: Vec<Self>) -> Self {
        let mut node = Self::default();
        node.reference = reference;
        node.role = role;
        node.children = children;
        node
    }
}

impl Drop for LayoutNode {
    fn drop(&mut self) {
        // Drain descendants before dropping each node so a deep branch uses a fixed call stack.
        let mut pending = std::mem::take(&mut self.children);
        while let Some(mut child) = pending.pop() {
            pending.append(&mut child.children);
        }
    }
}

#[derive(Debug)]
pub(super) struct ChildCoverageHint {
    pub(super) reference: String,
    pub(super) total: u64,
    pub(super) partial_reason: String,
    pub(super) scope: String,
    pub(super) line_index: usize,
    pub(super) text: String,
}

#[derive(Debug)]
pub(super) struct UnexpandedMenuSummary {
    pub(super) entries: Vec<(String, u64)>,
    pub(super) text: String,
}

#[derive(Debug)]
pub(super) struct EmptyValueLine {
    pub(super) reference: String,
    pub(super) parent: String,
    pub(super) address: String,
    pub(super) column: u64,
    pub(super) row: u64,
    pub(super) indent: String,
    pub(super) properties: String,
    pub(super) fields: Vec<String>,
    pub(super) text: String,
}

#[derive(Debug)]
pub(super) struct IdentifierSource {
    pub(super) value: String,
    pub(super) scope: [String; 3],
}

#[derive(Debug)]
pub(super) struct TextPartLine {
    pub(super) parent_ordinal: usize,
    pub(super) sibling_position: usize,
    pub(super) role_label: String,
    pub(super) formatted_value: String,
    pub(super) quoted_value: String,
    pub(super) action_and_capability_key: String,
}

#[derive(Debug)]
pub(super) struct ActionCatalogue {
    pub(super) key: String,
    pub(super) labels: Vec<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct LayoutRecord {
    pub(super) name: &'static str,
    pub(super) before_lines: usize,
    pub(super) after_lines: usize,
    pub(super) shared_fields: Option<usize>,
}

pub(super) struct LayoutFrame<'a> {
    pub(super) lines: Vec<String>,
    pub(super) mode: LayoutMode,
    pub(super) public_api: bool,
    pub(super) record_stages: bool,
    pub(super) roots: &'a [LayoutNode],
    pub(super) rendered_nodes: &'a [RenderedNode],
    pub(super) node_prefixes: &'a HashMap<String, String>,
    pub(super) unexpanded_menus: &'a [UnexpandedMenuSummary],
    pub(super) node_line_ranges: &'a HashMap<String, Range<usize>>,
    pub(super) child_hints: &'a [ChildCoverageHint],
    pub(super) empty_value_lines: &'a [EmptyValueLine],
    pub(super) action_sources: &'a HashMap<String, ActionCatalogue>,
    pub(super) text_parts: &'a HashMap<String, TextPartLine>,
    pub(super) identifier_sources: &'a HashMap<String, IdentifierSource>,
    pub(super) text_slice_sources: &'a HashMap<String, TextSliceSource>,
    pub(super) table_evidence: &'a TableEvidence,
    pub(super) native_tables: &'a [NativeTableGrid],
    pub(super) field_prefix_sources: &'a [(String, NativeFieldSource)],
    pub(super) role_sources: &'a [(String, NativeRoleSource)],
}

#[derive(Debug, PartialEq)]
pub(super) struct LayoutOutput {
    pub(super) lines: Vec<String>,
    pub(super) records: Vec<LayoutRecord>,
    pub(super) text_slices: Vec<TextSliceRecord>,
    pub(super) table_layouts: Vec<TableLayoutEvidence>,
    pub(super) field_prefix_replacements: Vec<FieldPrefixReplacement>,
    pub(super) role_label_replacements: Vec<RoleLabelReplacement>,
    pub(super) input_fields_replacements: Vec<InputFieldsReplacement>,
    pub(super) table_entries_replacements: Vec<TableEntriesReplacement>,
    pub(super) published_table_columns: Vec<super::value::Value>,
    pub(super) local_field_suffix_replacements: Vec<LocalFieldSuffixReplacement>,
    pub(super) shared_description_replacements: Vec<SharedDescriptionReplacement>,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct InvalidReference(pub(super) String);

fn reference_set<'a>(
    references: impl Iterator<Item = &'a str>,
) -> Result<String, InvalidReference> {
    let mut values = references
        .map(|reference| {
            reference
                .parse::<u64>()
                .map_err(|_| InvalidReference(reference.to_owned()))
        })
        .collect::<Result<Vec<_>, _>>()?;
    values.sort_unstable();
    values.dedup();
    let mut output = Vec::new();
    let mut start = 0;
    while start < values.len() {
        let mut end = start + 1;
        while end < values.len() && values[end] - values[end - 1] == 1 {
            end += 1;
        }
        if end - start >= 3 {
            output.push(format!("{}–{}", values[start], values[end - 1]));
        } else {
            output.extend(values[start..end].iter().map(u64::to_string));
        }
        start = end;
    }
    Ok(output.join(", "))
}

fn shared_path<'a>(paths: impl Iterator<Item = &'a [&'a str]>) -> &'a [&'a str] {
    let mut paths = paths;
    let Some(mut shared) = paths.next() else {
        return &[];
    };
    for path in paths {
        let common = shared
            .iter()
            .zip(path)
            .take_while(|(left, right)| left == right)
            .count();
        shared = &shared[..common];
    }
    shared
}

fn is_scope(role: &str) -> bool {
    matches!(role, "window" | "sheet" | "menuBar" | "menu")
}

fn group_child_coverage(frame: &LayoutFrame<'_>) -> Result<Option<Vec<String>>, InvalidReference> {
    if !frame.public_api {
        return Ok(None);
    }
    let nodes: HashMap<_, _> = frame
        .rendered_nodes
        .iter()
        .map(|node| (node.reference.as_str(), node))
        .collect();
    if nodes.len() != frame.rendered_nodes.len() {
        return Ok(None);
    }
    let mut pending: Vec<_> = frame.roots.iter().collect();
    let mut roles = HashMap::new();
    while let Some(node) = pending.pop() {
        roles.insert(node.reference.as_str(), node.role.as_str());
        pending.extend(&node.children);
    }
    let mut paths: HashMap<&str, Vec<&str>> = HashMap::new();
    let mut scopes = HashMap::new();
    for node in frame.rendered_nodes {
        let reference = node.reference.as_str();
        let Some(span) = frame.node_line_ranges.get(reference) else {
            continue;
        };
        let Some(lines) = frame.lines.get(span.clone()) else {
            continue;
        };
        if lines.join("\n") != node.text {
            continue;
        }
        let (mut path, scope) = if let Some(parent) = node.parent.as_deref() {
            let Some(path) = paths.get(parent) else {
                continue;
            };
            (path.clone(), scopes[parent])
        } else {
            (Vec::new(), reference)
        };
        path.push(reference);
        paths.insert(reference, path);
        scopes.insert(
            reference,
            if roles.get(reference).is_some_and(|role| is_scope(role)) {
                reference
            } else {
                scope
            },
        );
    }
    let mut used = HashSet::new();
    let mut group_indices = HashMap::new();
    let mut groups: Vec<Vec<&ChildCoverageHint>> = Vec::new();
    for hint in frame.child_hints {
        let reference = hint.reference.as_str();
        let Some(node) = nodes.get(reference) else {
            continue;
        };
        let Some(span) = frame.node_line_ranges.get(reference) else {
            continue;
        };
        if used.contains(reference)
            || !paths.contains_key(reference)
            || hint.line_index <= span.start
            || hint.line_index >= span.end
            || frame.lines[hint.line_index] != hint.text
            || !node.hints.contains(&hint.text)
            || hint.total == 0
        {
            continue;
        }
        let indent = "\t".repeat(node.depth + node.layout.len() + 1);
        if hint.text != format!("{indent}Children: 0/{} read; partial; read()", hint.total) {
            continue;
        }
        used.insert(reference);
        let key = (
            hint.scope.as_str(),
            scopes[reference],
            hint.total,
            hint.partial_reason.as_str(),
        );
        let index = *group_indices.entry(key).or_insert_with(|| {
            groups.push(Vec::new());
            groups.len() - 1
        });
        groups[index].push(hint);
    }
    let mut removed = HashSet::new();
    let mut inserted: HashMap<usize, Vec<String>> = HashMap::new();
    for members in groups.iter().filter(|members| members.len() >= 2) {
        let shared = shared_path(
            members
                .iter()
                .map(|hint| paths[hint.reference.as_str()].as_slice()),
        );
        let Some(parent_ref) = shared.last().copied() else {
            continue;
        };
        let parent = nodes[parent_ref];
        let indent = "\t".repeat(parent.depth + parent.layout.len() + 1);
        let references = reference_set(members.iter().map(|hint| hint.reference.as_str()))?;
        let declaration = format!(
            "{indent}Children [elements {references}]: 0/{} read; partial; each read()",
            members[0].total,
        );
        if members
            .iter()
            .map(|hint| hint.text.chars().count() + 1)
            .sum::<usize>()
            <= declaration.chars().count() + 1
        {
            continue;
        }
        removed.extend(members.iter().map(|hint| hint.line_index));
        inserted
            .entry(frame.node_line_ranges[parent_ref].end)
            .or_default()
            .push(declaration);
    }
    if removed.is_empty() {
        return Ok(None);
    }
    let mut result = Vec::new();
    for (index, line) in frame.lines.iter().enumerate() {
        result.extend(inserted.remove(&index).unwrap_or_default());
        if !removed.contains(&index) {
            result.push(line.clone());
        }
    }
    Ok(Some(result))
}

fn group_empty_values(lines: &[String], generated: &[EmptyValueLine]) -> Option<Vec<String>> {
    if generated.is_empty() {
        return None;
    }
    let mut counts = HashMap::new();
    for line in lines {
        *counts.entry(line.as_str()).or_insert(0) += 1;
    }
    let cells: HashMap<_, _> = generated
        .iter()
        .filter(|cell| counts.get(cell.text.as_str()) == Some(&1))
        .map(|cell| (cell.text.as_str(), cell))
        .collect();
    let mut output = Vec::new();
    let mut index = 0;
    while index < lines.len() {
        let Some(&first) = cells.get(lines[index].as_str()) else {
            output.push(lines[index].clone());
            index += 1;
            continue;
        };
        let mut run = vec![first];
        let mut direction = None;
        for line in &lines[index + 1..] {
            let Some(&following) = cells.get(line.as_str()) else {
                break;
            };
            if following.parent != first.parent
                || following.indent != first.indent
                || following.properties != first.properties
                || following.fields != first.fields
            {
                break;
            }
            let previous = run[run.len() - 1];
            let horizontal = following.row == previous.row
                && following.column.checked_sub(previous.column) == Some(1);
            let vertical = following.column == previous.column
                && following.row.checked_sub(previous.row) == Some(1);
            if !direction.map_or(horizontal || vertical, |horizontal_run| {
                if horizontal_run {
                    horizontal
                } else {
                    vertical
                }
            }) {
                break;
            }
            direction.get_or_insert(horizontal);
            run.push(following);
        }
        if run.len() >= 3 {
            let addresses = run
                .iter()
                .map(|cell| format!("{} {}", cell.reference, cell.address))
                .collect::<Vec<_>>();
            output.push(format!(
                "{}Empty values [{}; {}]: {}",
                first.indent,
                first.properties,
                first.fields.join(", "),
                addresses.join(", "),
            ));
            index += run.len();
        } else {
            output.push(lines[index].clone());
            index += 1;
        }
    }
    (output.len() != lines.len()).then_some(output)
}

#[derive(Clone, Copy)]
struct NodeLine<'a> {
    depth: usize,
    reference: &'a str,
    body: &'a str,
}

fn depth(line: &str) -> usize {
    line.bytes().take_while(|&byte| byte == b'\t').count()
}

fn reference_prefix(line: &str) -> Option<NodeLine<'_>> {
    let depth = depth(line);
    let (reference, body) = line[depth..].split_once(' ')?;
    (!reference.is_empty() && reference.bytes().all(|byte| byte.is_ascii_digit())).then_some(
        NodeLine {
            depth,
            reference,
            body,
        },
    )
}

fn node_line(line: &str) -> Option<NodeLine<'_>> {
    reference_prefix(line).filter(|entry| !entry.body.is_empty() && !entry.body.contains('\n'))
}

pub(super) fn python_space(value: char) -> bool {
    value.is_whitespace() || matches!(value, '\u{1c}'..='\u{1f}')
}

fn without_actions(line: &str, labels: &[String]) -> Option<String> {
    let field = format!("Secondary Actions: {}", labels.join("; "));
    let mut occurrences = line.match_indices(&field);
    let (start, _) = occurrences.next()?;
    if occurrences.next().is_some() {
        return None;
    }
    let (before, after) = (&line[..start], &line[start + field.len()..]);
    let (mut quoted, mut escaped) = (false, false);
    for value in before.chars() {
        if escaped {
            escaped = false;
        } else if quoted && value == '\\' {
            escaped = true;
        } else if value == '"' {
            quoted = !quoted;
        }
    }
    if quoted || !after.is_empty() && !after.starts_with(", ") {
        return None;
    }
    let before = if let Some(prefix) = before.strip_suffix(", ") {
        prefix
    } else if before.ends_with(' ') {
        before.trim_end_matches(python_space)
    } else {
        return None;
    };
    Some([before, after].concat())
}

fn group_sibling_actions(frame: &LayoutFrame<'_>) -> Result<Option<Vec<String>>, InvalidReference> {
    if frame.action_sources.is_empty() {
        return Ok(None);
    }
    let mut positions = HashMap::new();
    let mut duplicates = HashSet::new();
    for (index, line) in frame.lines.iter().enumerate() {
        if let Some(entry) = reference_prefix(line) {
            if positions
                .insert(entry.reference, (index, entry.depth))
                .is_some()
            {
                duplicates.insert(entry.reference);
            }
        }
    }
    for reference in duplicates {
        positions.remove(reference);
    }
    let mut pending: Vec<_> = frame
        .roots
        .iter()
        .rev()
        .map(|node| (node, None, node.reference.as_str()))
        .collect();
    let mut ancestors: HashMap<&str, Vec<&str>> = HashMap::new();
    let mut nodes = HashMap::new();
    let mut group_indices = HashMap::new();
    let mut groups: Vec<Vec<(&str, usize, String, &ActionCatalogue)>> = Vec::new();
    while let Some((node, parent, mut scope)) = pending.pop() {
        let reference = node.reference.as_str();
        let Some(&(index, depth)) = positions.get(reference) else {
            continue;
        };
        if parent.is_some_and(|parent| {
            positions
                .get(parent)
                .is_none_or(|&(_, previous)| depth != previous + 1)
        }) {
            continue;
        }
        if is_scope(&node.role) {
            scope = reference;
        }
        let mut path = parent.map_or_else(Vec::new, |parent| ancestors[parent].clone());
        path.push(reference);
        ancestors.insert(reference, path);
        nodes.insert(reference, node);
        pending.extend(
            node.children
                .iter()
                .rev()
                .map(|child| (child, Some(reference), scope)),
        );
        if let Some(source) = frame.action_sources.get(reference) {
            if let Some(stripped) = without_actions(&frame.lines[index], &source.labels) {
                let group = *group_indices
                    .entry((scope, source.key.as_str()))
                    .or_insert_with(|| {
                        groups.push(Vec::new());
                        groups.len() - 1
                    });
                groups[group].push((reference, index, stripped, source));
            }
        }
    }
    let mut declarations: HashMap<usize, Vec<String>> = HashMap::new();
    let mut replacements = HashMap::new();
    for members in groups.into_iter().filter(|members| members.len() >= 2) {
        let shared = shared_path(members.iter().map(|member| ancestors[member.0].as_slice()));
        let Some(parent) = shared.last().copied() else {
            continue;
        };
        let (_, parent_depth) = positions[parent];
        let children = nodes[parent]
            .children
            .iter()
            .map(|child| positions.get(child.reference.as_str()).copied())
            .collect::<Option<Vec<_>>>();
        let Some(children) = children else {
            continue;
        };
        if children.iter().any(|&(_, depth)| depth != parent_depth + 1) {
            continue;
        }
        let Some(insertion) = children.iter().map(|&(index, _)| index).min() else {
            continue;
        };
        let indent = "\t".repeat(parent_depth + 1);
        let references = reference_set(members.iter().map(|member| member.0))?;
        let actions = members[0].3.labels.join("; ");
        let declaration = format!("{indent}Secondary Actions [elements {references}]: {actions}");
        let removed: usize = members
            .iter()
            .map(|(_, index, stripped, _)| {
                frame.lines[*index].chars().count() - stripped.chars().count()
            })
            .sum();
        if removed <= declaration.chars().count() + 1 {
            continue;
        }
        declarations.entry(insertion).or_default().push(declaration);
        replacements.extend(
            members
                .into_iter()
                .map(|(_, index, stripped, _)| (index, stripped)),
        );
    }
    if replacements.is_empty() {
        return Ok(None);
    }
    let mut output = Vec::new();
    for (index, line) in frame.lines.iter().enumerate() {
        output.extend(declarations.remove(&index).unwrap_or_default());
        output.push(replacements.remove(&index).unwrap_or_else(|| line.clone()));
    }
    Ok(Some(output))
}

fn apply_stage(
    frame: &mut LayoutFrame<'_>,
    records: &mut Vec<LayoutRecord>,
    name: &'static str,
    lines: Option<Vec<String>>,
) {
    let before_lines = frame.lines.len();
    if let Some(lines) = lines {
        frame.lines = lines;
    }
    if frame.record_stages {
        records.push(LayoutRecord {
            name,
            before_lines,
            after_lines: frame.lines.len(),
            shared_fields: None,
        });
    }
}

/// Apply the selected terminal layout sequence without altering the source
/// nodes. Keep the evidence needed by final publication beside the text.
pub(super) fn pack(mut frame: LayoutFrame<'_>) -> Result<LayoutOutput, InvalidReference> {
    let mut records = Vec::new();
    let mut text_slices = Vec::new();
    let mut table_layouts = Vec::new();
    let mut field_prefix_replacements = Vec::new();
    let mut role_label_replacements = Vec::new();
    let mut input_fields = None;
    let mut entries = None;
    let mut local_field_suffix_replacements = Vec::new();
    let mut shared_description_replacements = Vec::new();
    if frame.mode == LayoutMode::Compact {
        let lines = group_child_coverage(&frame)?;
        apply_stage(&mut frame, &mut records, "group_child_coverage", lines);
        let lines = group_sibling_actions(&frame)?;
        apply_stage(&mut frame, &mut records, "group_sibling_actions", lines);
    }
    let lines = group_empty_values(&frame.lines, frame.empty_value_lines);
    apply_stage(&mut frame, &mut records, "group_empty_values", lines);
    match frame.mode {
        LayoutMode::Unpacked => {}
        LayoutMode::Full => {
            let lines = compact_tree_chains(&frame.lines, &layout_nodes(frame.roots), false);
            apply_stage(&mut frame, &mut records, "join_container_chains", lines);
            let lines = join_text_parts(&frame.lines, frame.text_parts);
            apply_stage(&mut frame, &mut records, "join_text_parts", lines);
        }
        LayoutMode::Compact => {
            let lines = join_visual_chains(&frame.lines, frame.roots);
            apply_stage(&mut frame, &mut records, "join_visual_chains", lines);
            let lines = group_identical_leaves(&frame.lines, frame.roots);
            apply_stage(&mut frame, &mut records, "group_identical_leaves", lines);
            let lines = group_leaf_headers(&frame);
            apply_stage(&mut frame, &mut records, "group_leaf_headers", lines);
            let lines = pack_row_records(&frame, frame.table_evidence);
            apply_stage(&mut frame, &mut records, "group_row_records", lines);
            let lines = pack_multi_column_layout(&frame, frame.table_evidence).map(|packed| {
                table_layouts = packed.tables;
                packed.lines
            });
            apply_stage(
                &mut frame,
                &mut records,
                "group_multi_column_records",
                lines,
            );
            let lines = share_exact_text_slices(&frame).map(|(lines, slices)| {
                text_slices = slices;
                lines
            });
            apply_stage(&mut frame, &mut records, "share_exact_text_slices", lines);
            if frame.record_stages {
                records.last_mut().unwrap().shared_fields = Some(text_slices.len());
            }
            let lines = group_identifier_prefix(&frame);
            apply_stage(&mut frame, &mut records, "group_identifier_prefix", lines);
            let lines = group_field_prefixes(&frame).map(|(lines, replacements)| {
                field_prefix_replacements = replacements;
                lines
            });
            apply_stage(&mut frame, &mut records, "group_field_prefixes", lines);
            apply_stage(&mut frame, &mut records, "publish_table_containment", None);
            let lines = group_role_labels(&frame).map(|(lines, replacements)| {
                role_label_replacements = replacements;
                lines
            });
            apply_stage(&mut frame, &mut records, "group_role_labels", lines);
            let lines = group_input_fields(&frame).map(|(lines, replacements)| {
                input_fields = Some((lines.clone(), replacements));
                lines
            });
            apply_stage(&mut frame, &mut records, "group_input_fields", lines);
            entries = pack_table_entries(&frame, &table_layouts);
            let lines = entries.as_ref().map(|layout| layout.lines.clone());
            apply_stage(&mut frame, &mut records, "share_table_entries", lines);
            let lines = share_local_field_suffixes(&frame, &role_label_replacements).map(
                |(lines, replacements)| {
                    local_field_suffix_replacements = replacements;
                    lines
                },
            );
            apply_stage(
                &mut frame,
                &mut records,
                "share_local_field_suffixes",
                lines,
            );
            let lines = group_field_descriptions(
                &frame,
                &role_label_replacements,
                &field_prefix_replacements,
                &local_field_suffix_replacements,
            )
            .map(|(lines, replacements)| {
                shared_description_replacements = replacements;
                lines
            });
            apply_stage(&mut frame, &mut records, "group_field_descriptions", lines);
        }
    }
    let entries_expanded = if input_fields.is_some() || !frame.native_tables.is_empty() {
        Some(expand_table_entries(&frame.lines))
    } else {
        None
    };
    let published_table_columns = if frame.native_tables.is_empty() {
        Vec::new()
    } else {
        entries_expanded
            .as_deref()
            .and_then(expand_input_fields)
            .and_then(|lines| expand_role_labels(&lines))
            .and_then(|lines| expand_field_prefixes(&lines))
            .map_or_else(Vec::new, |restored| {
                published_table_columns(
                    &frame,
                    frame.table_evidence,
                    frame.native_tables,
                    &restored,
                )
                .into_iter()
                .map(PublishedTableColumn::to_record)
                .collect()
            })
    };
    let table_entries_replacements = entries
        .filter(|layout| layout.lines == frame.lines)
        .map_or_else(Vec::new, |layout| layout.replacements);
    let input_fields_replacements = input_fields
        .filter(|(lines, _)| Some(lines.as_slice()) == entries_expanded.as_deref())
        .map_or_else(Vec::new, |(_, replacements)| replacements);
    Ok(LayoutOutput {
        lines: frame.lines,
        records,
        text_slices,
        table_layouts,
        field_prefix_replacements,
        role_label_replacements,
        input_fields_replacements,
        table_entries_replacements,
        published_table_columns,
        local_field_suffix_replacements,
        shared_description_replacements,
    })
}

struct GroupBody<'a> {
    states: &'a str,
    tail: &'a str,
}

fn group_body(body: &str) -> Option<GroupBody<'_>> {
    let tail = body.strip_prefix("container")?;
    if let Some((states, remainder)) = tail
        .strip_prefix(" (")
        .and_then(|value| value.split_once(')'))
    {
        if !states.is_empty() {
            return Some(GroupBody {
                states,
                tail: remainder,
            });
        }
    }
    Some(GroupBody { states: "", tail })
}

fn layout_nodes(roots: &[LayoutNode]) -> HashMap<&str, &LayoutNode> {
    let mut pending: Vec<_> = roots.iter().rev().collect();
    let mut nodes = HashMap::new();
    while let Some(node) = pending.pop() {
        nodes.insert(node.reference.as_str(), node);
        pending.extend(node.children.iter().rev());
    }
    nodes
}

fn joinable(
    parent: &LayoutNode,
    child: &LayoutNode,
    left: NodeLine<'_>,
    right: NodeLine<'_>,
    selected: bool,
) -> bool {
    let [only_child] = parent.children.as_slice() else {
        return false;
    };
    if !std::ptr::eq(only_child, child) || parent.incomplete_children || child.incomplete_children {
        return false;
    }
    if selected {
        return parent.visual_chain_child.as_deref() == Some(child.reference.as_str());
    }
    if parent.anonymous_group && child.anonymous_group {
        if let (Some(left), Some(right)) = (group_body(left.body), group_body(right.body)) {
            if left.tail == right.tail {
                return true;
            }
        }
    }
    parent.fact_backed_group_wrapper && child.fact_backed_group_wrapper
        || parent.anonymous_control_wrapper && child.ordinary_control
        || matches!(
            (parent.role.as_str(), child.role.as_str()),
            ("row", "cell" | "staticText" | "textField") | ("cell", "staticText" | "textField")
        )
}

fn common_group_chain(entries: &[NodeLine<'_>], nodes: &HashMap<&str, &LayoutNode>) -> bool {
    let Some(first) = group_body(entries[0].body) else {
        return false;
    };
    entries.iter().all(|entry| {
        nodes[entry.reference].anonymous_group
            && group_body(entry.body).is_some_and(|body| body.tail == first.tail)
    })
}

fn chain_text(entries: &[NodeLine<'_>], common_group: bool) -> String {
    let mut members = Vec::with_capacity(entries.len());
    for (index, entry) in entries.iter().enumerate() {
        if common_group && index + 1 != entries.len() {
            let group = group_body(entry.body).expect("a common group contains a container body");
            members.push(if group.states.is_empty() {
                entry.reference.to_owned()
            } else {
                format!("{} ({})", entry.reference, group.states)
            });
        } else {
            members.push(format!("{} {}", entry.reference, entry.body));
        }
    }
    members.join(" > ")
}

fn boundary_depth(line: &str) -> Option<usize> {
    node_line(line).map(|entry| entry.depth).or_else(|| {
        let indent = depth(line);
        line[indent..]
            .starts_with("Empty values [")
            .then_some(indent)
    })
}

fn compact_tree_chains(
    lines: &[String],
    nodes: &HashMap<&str, &LayoutNode>,
    selected: bool,
) -> Option<Vec<String>> {
    let mut output = Vec::new();
    let mut shifts: Vec<(usize, usize)> = Vec::new();
    let mut index = 0;
    let mut changed = false;
    while index < lines.len() {
        if let Some(boundary) = boundary_depth(&lines[index]) {
            while shifts
                .last()
                .is_some_and(|&(end_depth, _)| boundary <= end_depth)
            {
                shifts.pop();
            }
        }
        let shift: usize = shifts.iter().map(|&(_, count)| count).sum();
        let first = node_line(&lines[index]);
        let mut entries: Vec<_> = first.into_iter().collect();
        let mut text = String::new();
        while let Some(previous) = entries.last().copied() {
            let Some(next) = lines
                .get(index + entries.len())
                .and_then(|line| node_line(line))
            else {
                break;
            };
            let (Some(&parent), Some(&child)) =
                (nodes.get(previous.reference), nodes.get(next.reference))
            else {
                break;
            };
            if next.depth != previous.depth + 1
                || !joinable(parent, child, previous, next, selected)
            {
                break;
            }
            entries.push(next);
            let common = !selected && common_group_chain(&entries, nodes);
            let old = &entries[..entries.len() - 1];
            if !selected && !common && old.len() > 1 && common_group_chain(old, nodes) {
                entries.pop();
                break;
            }
            let candidate = chain_text(&entries, common);
            if candidate.encode_utf16().count() > 320 {
                entries.pop();
                break;
            }
            text = candidate;
        }
        if entries.len() > 1 {
            let first = entries[0];
            let last = entries[entries.len() - 1];
            let indent = "\t".repeat(first.depth.saturating_sub(shift));
            output.push(format!("{indent}{text}"));
            shifts.push((last.depth, entries.len() - 1));
            index += entries.len();
            changed = true;
        } else {
            let line = &lines[index];
            output.push(line[depth(line).min(shift)..].to_owned());
            index += 1;
        }
    }
    changed.then_some(output)
}

fn chain_members(line: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    let (mut quoted, mut escaped, mut start) = (false, false, 0);
    let mut characters = line.char_indices();
    while let Some((index, value)) = characters.next() {
        if escaped {
            escaped = false;
        } else if quoted && value == '\\' {
            escaped = true;
        } else if value == '"' {
            quoted = !quoted;
        } else if !quoted && line[index..].starts_with(" > ") {
            parts.push(&line[start..index]);
            start = index + 3;
            characters.next();
            characters.next();
        }
    }
    parts.push(&line[start..]);
    parts
}

fn canonical_reference(value: &str) -> bool {
    value == "0"
        || !value.starts_with('0')
            && !value.is_empty()
            && value.bytes().all(|byte| byte.is_ascii_digit())
}

fn shared_member(part: &str) -> Option<(&str, &str)> {
    let Some((reference, states)) = part.split_once(' ') else {
        return canonical_reference(part).then_some((part, ""));
    };
    if !canonical_reference(reference) {
        return None;
    }
    let body = states.strip_prefix('(')?.strip_suffix(')')?;
    body.split(", ")
        .all(|state| {
            matches!(
                state,
                "disabled"
                    | "reported disabled"
                    | "selected"
                    | "selectable"
                    | "clickable"
                    | "text-input"
                    | "replace-only"
                    | "replace unavailable"
                    | "replace unknown"
                    | "submenu"
                    | "focusable"
                    | "expanded"
                    | "collapsed"
                    | "focused"
                    | "reports focus"
                    | "modal"
                    | "non-modal"
                    | "contains selected text"
            )
        })
        .then_some((reference, states))
}

fn expand_tree_chains(lines: &[String]) -> Vec<String> {
    let mut output = Vec::new();
    let mut shifts: Vec<(usize, usize)> = Vec::new();
    for line in lines {
        let boundary = boundary_depth(line);
        if let Some(indent) = boundary {
            while shifts
                .last()
                .is_some_and(|&(old_depth, _)| indent <= old_depth)
            {
                shifts.pop();
            }
        }
        let shift: usize = shifts.iter().map(|&(_, count)| count).sum();
        if let Some(indent) = boundary {
            let members = chain_members(&line[indent..]);
            let last = members.last().and_then(|part| node_line(part));
            if members.len() > 1
                && last.is_some_and(|part| part.depth == 0)
                && members.iter().all(|member| {
                    if let Some((reference, body)) = member.split_once(' ') {
                        canonical_reference(reference) && !body.is_empty() && !body.contains('\n')
                    } else {
                        canonical_reference(member)
                    }
                })
            {
                let group = last.and_then(|part| group_body(part.body));
                for (index, member) in members.iter().enumerate() {
                    let padding = "\t".repeat(indent + shift + index);
                    if let (Some((reference, states)), Some(group)) =
                        (shared_member(member), group.as_ref())
                    {
                        let separator = if states.is_empty() { "" } else { " " };
                        output.push(format!(
                            "{padding}{reference} container{separator}{states}{}",
                            group.tail
                        ));
                    } else {
                        output.push(format!("{padding}{member}"));
                    }
                }
                shifts.push((indent, members.len() - 1));
                continue;
            }
        }
        let padding = if boundary.is_some() || line.starts_with('\t') {
            "\t".repeat(shift)
        } else {
            String::new()
        };
        output.push(format!("{padding}{line}"));
    }
    output
}

fn join_visual_chains(lines: &[String], roots: &[LayoutNode]) -> Option<Vec<String>> {
    let nodes = layout_nodes(roots);
    let packed = compact_tree_chains(lines, &nodes, true)?;
    (expand_tree_chains(&packed) == lines).then_some(packed)
}

struct TextPart<'a> {
    line: NodeLine<'a>,
    source: &'a TextPartLine,
    secondary_actions: &'a str,
}

fn text_part<'a>(
    line: &'a str,
    evidence: &'a HashMap<String, TextPartLine>,
) -> Option<TextPart<'a>> {
    let line = node_line(line)?;
    let source = evidence.get(line.reference)?;
    if source.role_label.is_empty()
        || !source.role_label.chars().all(|character| {
            matches!(character, ' ' | '_' | '-') || in_unicode_ranges(character, LETTER_OR_NUMBER)
        })
    {
        return None;
    }
    let body = line
        .body
        .strip_prefix(&source.role_label)?
        .strip_prefix(" Value: ")?;
    if body.is_empty() {
        return None;
    }
    let suffix = body.strip_prefix(&source.formatted_value)?;
    let secondary_actions = if suffix.is_empty() {
        ""
    } else {
        let actions = suffix.strip_prefix(", ")?;
        if !actions.starts_with("Secondary Actions: ") || actions.contains(['[', ']', '\r', '\n']) {
            return None;
        }
        actions
    };
    Some(TextPart {
        line,
        source,
        secondary_actions,
    })
}

fn text_part_line(parts: &[TextPart<'_>]) -> String {
    let first = &parts[0];
    let indent = "\t".repeat(first.line.depth);
    let mut properties = first.source.role_label.clone();
    if !first.secondary_actions.is_empty() {
        properties.push_str("; ");
        properties.push_str(first.secondary_actions);
    }
    let items = parts
        .iter()
        .map(|part| format!("{} {}", part.line.reference, part.source.quoted_value))
        .collect::<Vec<_>>();
    format!("{indent}Text parts [{properties}]: {}", items.join(", "))
}

fn join_text_parts(
    lines: &[String],
    evidence: &HashMap<String, TextPartLine>,
) -> Option<Vec<String>> {
    if evidence.is_empty() {
        return None;
    }
    let mut output = Vec::new();
    let mut index = 0;
    while index < lines.len() {
        let Some(first) = text_part(&lines[index], evidence) else {
            output.push(lines[index].clone());
            index += 1;
            continue;
        };
        let mut members = vec![first];
        for line in &lines[index + 1..] {
            let Some(item) = text_part(line, evidence) else {
                break;
            };
            let first = &members[0];
            let previous = &members[members.len() - 1];
            if members.len() >= 8
                || item.source.parent_ordinal != first.source.parent_ordinal
                || item
                    .source
                    .sibling_position
                    .checked_sub(previous.source.sibling_position)
                    != Some(1)
                || item.line.depth != first.line.depth
                || item.source.role_label != first.source.role_label
                || item.secondary_actions != first.secondary_actions
                || item.source.action_and_capability_key != first.source.action_and_capability_key
            {
                break;
            }
            members.push(item);
            if text_part_line(&members).encode_utf16().count() > 480 {
                members.pop();
                break;
            }
        }
        let packed = text_part_line(&members);
        let original_size = lines[index..index + members.len()]
            .iter()
            .map(|line| line.encode_utf16().count() + 1)
            .sum::<usize>()
            - 1;
        if members.len() >= 2 && packed.encode_utf16().count() < original_size {
            output.push(packed);
            index += members.len();
        } else {
            output.push(lines[index].clone());
            index += 1;
        }
    }
    (output.len() != lines.len()).then_some(output)
}

fn ordered_reference_set(references: &[&str]) -> String {
    let mut output = Vec::new();
    let mut start = 0;
    while start < references.len() {
        let mut end = start + 1;
        while end < references.len()
            && references[end - 1]
                .parse::<u64>()
                .ok()
                .and_then(|value| value.checked_add(1))
                .is_some_and(|expected| references[end].parse::<u64>() == Ok(expected))
        {
            end += 1;
        }
        if end - start >= 3 {
            output.push(format!("{}–{}", references[start], references[end - 1]));
        } else {
            output.extend(
                references[start..end]
                    .iter()
                    .map(|value| (*value).to_owned()),
            );
        }
        start = end;
    }
    output.join(", ")
}

fn canonical_node_line(line: &str) -> Option<NodeLine<'_>> {
    node_line(line).filter(|entry| canonical_reference(entry.reference))
}

fn identical_leaf_parts(line: &str, limit: usize) -> Option<Vec<String>> {
    let indent = depth(line);
    let text = line[indent..].strip_prefix("Elements [")?;
    let (body, entries) = text.rsplit_once("]: ")?;
    if body.is_empty() || body.contains('\n') {
        return None;
    }
    let mut refs = Vec::new();
    for entry in entries.split(", ") {
        if canonical_reference(entry) {
            refs.push(entry.to_owned());
        } else if let Some((start, end)) = entry.split_once('–') {
            if !canonical_reference(start) || !canonical_reference(end) {
                return None;
            }
            let (start, end) = (start.parse::<u64>().ok()?, end.parse::<u64>().ok()?);
            if end <= start || end - start >= limit.saturating_sub(refs.len()) as u64 {
                return None;
            }
            refs.extend((start..=end).map(|value| value.to_string()));
        } else {
            return None;
        }
    }
    let unique = refs.iter().collect::<HashSet<_>>().len();
    if unique < 3 || unique != refs.len() {
        return None;
    }
    Some(
        refs.into_iter()
            .map(|reference| format!("{}{reference} {body}", &line[..indent]))
            .collect(),
    )
}

fn group_identical_leaves(lines: &[String], roots: &[LayoutNode]) -> Option<Vec<String>> {
    let mut siblings = HashMap::new();
    let mut pending: Vec<_> = roots.iter().collect();
    while let Some(parent) = pending.pop() {
        pending.extend(&parent.children);
        for (position, child) in parent.children.iter().enumerate() {
            if child.children.is_empty() && !child.incomplete_children {
                siblings.insert(
                    child.reference.as_str(),
                    (parent.reference.as_str(), position),
                );
            }
        }
    }
    let candidate = |index: usize| -> Option<NodeLine<'_>> {
        let item = canonical_node_line(&lines[index])?;
        if !siblings.contains_key(item.reference) {
            return None;
        }
        if let Some(following) = lines.get(index + 1) {
            if canonical_node_line(following)?.depth > item.depth {
                return None;
            }
        }
        Some(item)
    };
    let mut output = Vec::new();
    let mut index = 0;
    while index < lines.len() {
        let Some(first) = candidate(index) else {
            output.push(lines[index].clone());
            index += 1;
            continue;
        };
        let mut refs = vec![first.reference];
        let (parent, position) = siblings[first.reference];
        while index + refs.len() < lines.len() {
            let Some(item) = candidate(index + refs.len()) else {
                break;
            };
            if item.depth != first.depth
                || item.body != first.body
                || siblings[item.reference] != (parent, position + refs.len())
            {
                break;
            }
            refs.push(item.reference);
        }
        let indent = "\t".repeat(first.depth);
        let packed = format!(
            "{indent}Elements [{}]: {}",
            first.body,
            ordered_reference_set(&refs)
        );
        let original_size = lines[index..index + refs.len()]
            .iter()
            .map(|line| line.chars().count() + 1)
            .sum::<usize>()
            - 1;
        if refs.len() >= 3 && packed.chars().count() < original_size {
            output.push(packed);
            index += refs.len();
        } else {
            output.push(lines[index].clone());
            index += 1;
        }
    }
    if output.len() == lines.len() {
        return None;
    }
    let mut expanded = Vec::new();
    for line in &output {
        if let Some(parts) = identical_leaf_parts(line, lines.len()) {
            expanded.extend(parts);
        } else {
            expanded.push(line.clone());
        }
    }
    (expanded == lines).then_some(output)
}

fn leaf_header(line: &str) -> Option<(usize, &str)> {
    let indent = depth(line);
    let label = line[indent..]
        .strip_prefix("Elements [")?
        .strip_suffix("]:")?;
    (!label.is_empty() && !label.contains(['[', ']', '\r', '\n'])).then_some((indent, label))
}

fn expand_leaf_headers(lines: &[String]) -> Vec<String> {
    let mut output = Vec::new();
    let mut index = 0;
    while index < lines.len() {
        let Some((indent, label)) = leaf_header(&lines[index]) else {
            output.push(lines[index].clone());
            index += 1;
            continue;
        };
        let start = index;
        index += 1;
        let mut members = Vec::new();
        while let Some(member) = lines.get(index).and_then(|line| canonical_node_line(line)) {
            if member.depth != indent + 1 {
                break;
            }
            members.push(member);
            index += 1;
        }
        let unique = members
            .iter()
            .map(|member| member.reference)
            .collect::<HashSet<_>>();
        if members.len() >= 3 && unique.len() == members.len() {
            let indent = &lines[start][..indent];
            output.extend(
                members
                    .into_iter()
                    .map(|member| format!("{indent}{} {label} {}", member.reference, member.body)),
            );
        } else {
            output.extend_from_slice(&lines[start..index]);
        }
    }
    output
}

fn visible_menu_summaries<'a>(frame: &'a LayoutFrame<'_>) -> HashMap<&'a str, u64> {
    let mut counts = HashMap::new();
    for summary in frame.unexpanded_menus {
        for (reference, _) in &summary.entries {
            *counts.entry(reference.as_str()).or_insert(0) += 1;
        }
    }
    let mut menus = HashMap::new();
    for summary in frame.unexpanded_menus {
        if frame
            .lines
            .iter()
            .filter(|line| **line == summary.text)
            .count()
            != 1
        {
            continue;
        }
        for (reference, total) in &summary.entries {
            if *total > 0 && counts[reference.as_str()] == 1 {
                menus.insert(reference.as_str(), *total);
            }
        }
    }
    menus
}

struct Leaf<'a> {
    reference: &'a str,
    parent: &'a str,
    position: usize,
    depth: usize,
    prefix: &'a str,
    tail: &'a str,
    layout: &'a [(String, String)],
}

fn leaf_candidates<'a>(frame: &'a LayoutFrame<'_>) -> HashMap<usize, Leaf<'a>> {
    let mut records = HashMap::new();
    let mut record_counts = HashMap::new();
    for record in frame.rendered_nodes {
        records.insert(record.reference.as_str(), record);
        *record_counts.entry(record.reference.as_str()).or_insert(0) += 1;
    }
    let mut line_counts = HashMap::new();
    for line in &frame.lines {
        if let Some(item) = canonical_node_line(line) {
            *line_counts.entry(item.reference).or_insert(0) += 1;
        }
    }
    let menus = visible_menu_summaries(frame);
    let mut positions = HashMap::new();
    let mut occurrences = HashMap::new();
    let mut pending: Vec<_> = frame.roots.iter().collect();
    while let Some(parent) = pending.pop() {
        pending.extend(&parent.children);
        for (position, child) in parent.children.iter().enumerate() {
            let reference = child.reference.as_str();
            *occurrences.entry(reference).or_insert(0) += 1;
            let covered_menu = child.role == "menuBarItem"
                && child.menu_children.is_some_and(|(start, end, total)| {
                    start == 0 && end == 0 && menus.get(reference) == Some(&total)
                });
            if child.children.is_empty()
                && (!child.incomplete_children || covered_menu)
                && !is_scope(&child.role)
                && child.role != "application"
                && !matches!(
                    child.subrole.as_deref(),
                    Some("dialog" | "systemDialog" | "popover")
                )
            {
                positions.insert(reference, (parent.reference.as_str(), position));
            }
        }
    }
    let mut candidates = HashMap::new();
    for (index, line) in frame.lines.iter().enumerate() {
        let Some(item) = canonical_node_line(line) else {
            continue;
        };
        let (Some(&record), Some(&(parent, position)), Some(prefix)) = (
            records.get(item.reference),
            positions.get(item.reference),
            frame.node_prefixes.get(item.reference),
        ) else {
            continue;
        };
        if record_counts[item.reference] != 1
            || line_counts[item.reference] != 1
            || occurrences[item.reference] != 1
            || record.parent.as_deref() != Some(parent)
            || record.partial
            || !record.hints.is_empty()
            || record.text.contains('\n')
            || frame
                .lines
                .get(index + 1)
                .is_some_and(|next| depth(next) > item.depth)
            || prefix.is_empty()
            || prefix.contains(['[', ']', '\r', '\n', '\t'])
        {
            continue;
        }
        let generated = format!("{}{} {prefix}", &line[..item.depth], item.reference);
        if !record.text.starts_with(&generated) {
            continue;
        }
        let Some(tail) = item
            .body
            .strip_prefix(prefix)
            .and_then(|body| body.strip_prefix(' '))
        else {
            continue;
        };
        if !tail.is_empty() {
            candidates.insert(
                index,
                Leaf {
                    reference: item.reference,
                    parent,
                    position,
                    depth: item.depth,
                    prefix,
                    tail,
                    layout: &record.layout,
                },
            );
        }
    }
    candidates
}

fn group_leaf_headers(frame: &LayoutFrame<'_>) -> Option<Vec<String>> {
    if frame.rendered_nodes.is_empty() {
        return None;
    }
    let candidates = leaf_candidates(frame);
    let mut output = Vec::new();
    let mut index = 0;
    let mut changed = false;
    while index < frame.lines.len() {
        let Some(first) = candidates.get(&index) else {
            output.push(frame.lines[index].clone());
            index += 1;
            continue;
        };
        let mut members = vec![first];
        while let Some(member) = candidates.get(&(index + members.len())) {
            if member.parent != first.parent
                || member.position != first.position + members.len()
                || member.depth != first.depth
                || member.prefix != first.prefix
                || member.layout != first.layout
            {
                break;
            }
            members.push(member);
        }
        let indent = "\t".repeat(first.depth);
        let mut packed = vec![format!("{indent}Elements [{}]:", first.prefix)];
        packed.extend(
            members
                .iter()
                .map(|member| format!("{indent}\t{} {}", member.reference, member.tail)),
        );
        let original_size = frame.lines[index..index + members.len()]
            .iter()
            .map(|line| line.len() + 1)
            .sum::<usize>();
        let packed_size = packed.iter().map(|line| line.len() + 1).sum::<usize>();
        if members.len() >= 3 && original_size.saturating_sub(packed_size) >= 8 {
            output.extend(packed);
            index += members.len();
            changed = true;
        } else {
            output.push(frame.lines[index].clone());
            index += 1;
        }
    }
    (changed && expand_leaf_headers(&output) == frame.lines).then_some(output)
}

const IDENTIFIER_DECLARATION: &str = "ID prefix (prepend to ID suffix): ";

struct IdentifierLine<'a> {
    index: usize,
    value: &'a str,
    start: usize,
    scope: &'a [String; 3],
}

fn plain_text(value: &str) -> bool {
    !value.is_empty()
        && !value.chars().any(|character| {
            matches!(character, '\u{2028}' | '\u{2029}') || in_unicode_ranges(character, CATEGORY_C)
        })
}

fn identifier_lines<'a>(frame: &'a LayoutFrame<'_>) -> Option<(usize, Vec<IdentifierLine<'a>>)> {
    let [root] = frame.roots else {
        return None;
    };
    if is_menu(&root.role) {
        return None;
    }
    let mut nodes = HashMap::new();
    let mut parents = HashMap::new();
    let mut occurrences = HashMap::new();
    let mut pending = vec![(root, None)];
    while let Some((node, parent)) = pending.pop() {
        let reference = node.reference.as_str();
        nodes.insert(reference, node);
        parents.insert(reference, parent);
        *occurrences.entry(reference).or_insert(0) += 1;
        pending.extend(node.children.iter().map(|child| (child, Some(reference))));
    }
    let mut positions = HashMap::<&str, Vec<usize>>::new();
    for (index, line) in frame.lines.iter().enumerate() {
        positions.entry(line).or_default().push(index);
    }
    let mut counts = HashMap::new();
    for record in frame.rendered_nodes {
        *counts.entry(record.reference.as_str()).or_insert(0) += 1;
    }
    let root_record = frame
        .rendered_nodes
        .iter()
        .find(|record| record.reference == root.reference)?;
    if counts[root.reference.as_str()] != 1
        || root_record.text.contains('\n')
        || !root_record.text.starts_with(&format!(
            "{}{} ",
            "\t".repeat(root_record.depth),
            root.reference
        ))
    {
        return None;
    }
    let [root_index] = positions.get(root_record.text.as_str())?.as_slice() else {
        return None;
    };
    let mut candidates = Vec::new();
    for record in frame.rendered_nodes {
        let reference = record.reference.as_str();
        let (Some(source), Some(&node), Some(span)) = (
            frame.identifier_sources.get(reference),
            nodes.get(reference),
            frame.node_line_ranges.get(reference),
        ) else {
            continue;
        };
        if source.scope[2] != root.reference
            || counts[reference] != 1
            || occurrences[reference] != 1
            || record.parent.as_deref() != parents[reference]
            || is_menu(&node.role)
            || record.partial
            || !record.hints.is_empty()
            || !record.related.is_empty()
            || !record.layout.is_empty()
            || span.end.checked_sub(span.start) != Some(1)
            || record.text.contains('\n')
        {
            continue;
        }
        let Some([index]) = positions.get(record.text.as_str()).map(Vec::as_slice) else {
            continue;
        };
        let value = source.value.as_str();
        if !plain_text(value)
            || node.identifier.as_deref() != Some(value)
            || super::render::field(value) != value
        {
            continue;
        }
        let prefix = format!("{}{reference} ", "\t".repeat(record.depth));
        let suffix = format!("ID: {value}");
        if index < root_index
            || !record.text.starts_with(&prefix)
            || !record.text.ends_with(&suffix)
            || record.text.contains(" | ")
            || record.text.contains(" > ")
        {
            continue;
        }
        let start = record.text.len() - suffix.len();
        if start <= prefix.len() || record.text.as_bytes()[start - 1] != b' ' {
            continue;
        }
        candidates.push(IdentifierLine {
            index: *index,
            value,
            start,
            scope: &source.scope,
        });
    }
    Some((*root_index, candidates))
}

fn is_menu(role: &str) -> bool {
    matches!(role, "menu" | "menuBar" | "menuBarItem")
}

fn read_layout_json(text: &str) -> Option<String> {
    match super::wire::decode_json(text.as_bytes()).ok()? {
        super::value::Value::Text(value) => Some(value),
        _ => None,
    }
}

fn is_identifier_heading(line: &str) -> bool {
    let trimmed = line.trim_start_matches(python_space);
    if let Some(ordinal) = trimmed
        .strip_prefix("Window ")
        .and_then(|value| value.strip_suffix(':'))
    {
        return !ordinal.is_empty()
            && ordinal
                .chars()
                .all(|character| in_unicode_ranges(character, DECIMAL_DIGIT));
    }
    trimmed
        .strip_prefix("Application menu:")
        .is_some_and(|body| !body.contains('\n'))
}

fn valid_field_preamble(line: &str) -> bool {
    let Some((name, rest)) = line.split_once(" prefix (prepend to ") else {
        return false;
    };
    if name.is_empty()
        || !name
            .bytes()
            .all(|character| character.is_ascii_alphabetic())
    {
        return false;
    }
    let rest = rest.strip_prefix("quoted ").unwrap_or(rest);
    let Some(value) = rest
        .strip_prefix(name)
        .and_then(|text| text.strip_prefix(" suffix): "))
    else {
        return false;
    };
    !value.contains('\n') && read_layout_json(value).as_deref().is_some_and(plain_text)
}

fn expand_identifier_prefixes(lines: &[String]) -> Option<Vec<String>> {
    let mut output = Vec::new();
    let mut prefix: Option<String> = None;
    let mut indent = 0;
    let mut root_started = false;
    for line in lines {
        if is_identifier_heading(line) {
            prefix = None;
        }
        let line_depth = depth(line);
        let stripped = &line[line_depth..];
        if let Some(value) = stripped.strip_prefix(IDENTIFIER_DECLARATION) {
            if prefix.is_some() && !root_started {
                return None;
            }
            prefix = read_layout_json(value).filter(|text| plain_text(text));
            prefix.as_ref()?;
            indent = line_depth;
            root_started = false;
            continue;
        }
        let numbered =
            reference_prefix(line).is_some_and(|line| canonical_reference(line.reference));
        if prefix.is_some() && !line.is_empty() {
            if root_started && line_depth <= indent {
                prefix = None;
            } else if !root_started && line_depth == indent {
                if valid_field_preamble(stripped) {
                    output.push(line.clone());
                    continue;
                }
                if numbered {
                    root_started = true;
                } else {
                    prefix = None;
                }
            }
        }
        if let Some(prefix) = prefix.as_deref().filter(|_| numbered) {
            let marker = [(", ID suffix: ", ", ID: "), (" ID suffix: ", " ID: ")]
                .into_iter()
                .find_map(|(marker, full)| line.rfind(marker).map(|index| (index, marker, full)));
            if let Some((index, marker, full)) = marker {
                let suffix = read_layout_json(&line[index + marker.len()..])?;
                output.push(format!("{}{full}{prefix}{suffix}", &line[..index]));
                continue;
            }
        }
        output.push(line.clone());
    }
    Some(output)
}

fn shared_identifier_prefix<'a>(left: &'a str, right: &str) -> Option<&'a str> {
    left.char_indices()
        .zip(right.chars())
        .take_while(|((_, left), right)| left == right)
        .filter_map(|((index, character), _)| {
            matches!(character, '.' | '/' | ':' | '_').then_some(index + 1)
        })
        .last()
        .map(|end| &left[..end])
}

fn group_identifier_prefix(frame: &LayoutFrame<'_>) -> Option<Vec<String>> {
    let (root_index, mut members) = identifier_lines(frame)?;
    if members.len() < 2
        || members
            .iter()
            .any(|member| member.scope != members[0].scope)
    {
        return None;
    }
    let indent = &frame.lines[root_index][..depth(&frame.lines[root_index])];
    members.sort_by_key(|member| member.value);
    let prefixes = members
        .windows(2)
        .filter_map(|pair| shared_identifier_prefix(pair[0].value, pair[1].value))
        .collect::<std::collections::BTreeSet<_>>();
    let mut best: Option<(i64, &str, Range<usize>)> = None;
    for prefix in prefixes {
        let start = members.partition_point(|member| member.value < prefix);
        let end = start
            + members[start..]
                .iter()
                .take_while(|member| member.value.starts_with(prefix))
                .count();
        if end - start < 2 {
            continue;
        }
        let declaration = IDENTIFIER_DECLARATION.len() + super::render::quoted(prefix).len();
        let saving = members[start..end]
            .iter()
            .map(|member| {
                ("ID: ".len() + member.value.len()) as i64
                    - ("ID suffix: ".len()
                        + super::render::quoted(&member.value[prefix.len()..]).len())
                        as i64
            })
            .sum::<i64>()
            - (indent.len() + declaration + 1) as i64;
        if saving >= 32
            && best
                .as_ref()
                .is_none_or(|(previous, _, _)| saving > *previous)
        {
            best = Some((saving, prefix, start..end));
        }
    }
    let (_, prefix, matching) = best?;
    let mut output = frame.lines.clone();
    for member in &members[matching] {
        let suffix = super::render::quoted(&member.value[prefix.len()..]);
        output[member.index] = format!(
            "{}ID suffix: {suffix}",
            &output[member.index][..member.start]
        );
    }
    let encoded = super::render::quoted(prefix);
    output.insert(
        root_index,
        format!("{indent}{IDENTIFIER_DECLARATION}{encoded}"),
    );
    (expand_identifier_prefixes(&output).as_deref() == Some(frame.lines.as_slice()))
        .then_some(output)
}

// Sorted inclusive ranges from CPython 3.11 unicodedata 14.0.0 and
// the locked regex 2026.11.9. The property tests use code points rather than
// Rust's broader Alphabetic and Numeric properties.
fn in_unicode_ranges(character: char, ranges: &[(u32, u32)]) -> bool {
    let value = character as u32;
    let index = ranges.partition_point(|&(_, end)| end < value);
    ranges.get(index).is_some_and(|&(start, _)| start <= value)
}

pub(super) fn python_alphanumeric(character: char) -> bool {
    // The pinned Python Unicode version excludes letters and numbers added
    // after its printable inventory was generated.
    character.is_ascii_alphanumeric()
        || (!character.is_ascii()
            && super::diff::python_is_printable(character)
            && in_unicode_ranges(character, LETTER_OR_NUMBER))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(super) enum TextAttribute {
    Name,
    Value,
}

impl TextAttribute {
    fn label(self) -> &'static str {
        match self {
            Self::Name => "Name",
            Self::Value => "Value",
        }
    }

    fn path(self) -> &'static [&'static str] {
        match self {
            Self::Name => &["name"],
            Self::Value => &["control_value", "value"],
        }
    }
}

#[derive(Debug)]
pub(super) struct TextSliceSource {
    pub(super) native: String,
    pub(super) scope: [String; 3],
    pub(super) parent: Option<String>,
    pub(super) name: Option<String>,
    pub(super) value: Option<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct TextSliceRecord {
    pub(super) reference: String,
    pub(super) native: String,
    pub(super) field: TextAttribute,
    pub(super) path: &'static [&'static str],
    pub(super) value: String,
    pub(super) anchor: String,
    pub(super) anchor_native: String,
    pub(super) anchor_field: TextAttribute,
    pub(super) anchor_path: &'static [&'static str],
    pub(super) start: usize,
    pub(super) end: usize,
    pub(super) scope: [String; 3],
    pub(super) bare: bool,
    pub(super) statement: String,
}

fn decimal_position(value: char) -> Option<usize> {
    let value = value as u32;
    let index = DECIMAL_DIGIT.partition_point(|&(_, end)| end < value);
    let &(start, _) = DECIMAL_DIGIT.get(index)?;
    (start <= value).then(|| ((value - start) % 10) as usize)
}

fn decimal_prefix(value: &str) -> Option<(&str, &str)> {
    let end = value
        .char_indices()
        .find_map(|(index, character)| decimal_position(character).is_none().then_some(index))
        .unwrap_or(value.len());
    (end != 0).then(|| value.split_at(end))
}

fn decimal_number(value: &str) -> Option<usize> {
    value.chars().try_fold(0usize, |value, character| {
        value
            .checked_mul(10)?
            .checked_add(decimal_position(character)?)
    })
}

struct SliceStatement<'a> {
    bare: bool,
    start: &'a str,
    end: &'a str,
    reference: &'a str,
    field: TextAttribute,
    size: usize,
}

fn slice_statement(value: &str) -> Option<SliceStatement<'_>> {
    let (bare, tail) = if let Some(tail) = value.strip_prefix("Text: text [") {
        (true, tail)
    } else {
        (false, value.strip_prefix("Value: text [")?)
    };
    let (start, tail) = decimal_prefix(tail)?;
    let (end, tail) = decimal_prefix(tail.strip_prefix(", ")?)?;
    let (reference, tail) = decimal_prefix(tail.strip_prefix(") of element ")?)?;
    let (field, tail) = if let Some(tail) = tail.strip_prefix(".Name (UTF-16)") {
        (TextAttribute::Name, tail)
    } else {
        (TextAttribute::Value, tail.strip_prefix(".Value (UTF-16)")?)
    };
    Some(SliceStatement {
        bare,
        start,
        end,
        reference,
        field,
        size: value.len() - tail.len(),
    })
}

fn contains_slice_statement(value: &str) -> bool {
    value
        .match_indices("Text: text [")
        .chain(value.match_indices("Value: text ["))
        .any(|(index, _)| slice_statement(&value[index..]).is_some())
}

fn unquoted_positions(text: &str) -> impl Iterator<Item = usize> + '_ {
    let mut quoted = false;
    let mut escaped = false;
    text.char_indices().filter_map(move |(index, character)| {
        let outside = !quoted;
        if escaped {
            escaped = false;
        } else if quoted && character == '\\' {
            escaped = true;
        } else if character == '"' {
            quoted = !quoted;
        }
        outside.then_some(index)
    })
}

fn space_label_positions(body: &str, label: &str) -> Vec<usize> {
    unquoted_positions(body)
        .filter(|&position| {
            position != 0
                && body.as_bytes()[position - 1] == b' '
                && body[position..].starts_with(label)
        })
        .collect()
}

fn scalar(body: &str, start: usize) -> Option<(String, usize)> {
    let text = body.get(start..)?;
    if let Some(quoted) = text.strip_prefix('"') {
        let mut escaped = false;
        for (offset, character) in quoted.char_indices() {
            if escaped {
                escaped = false;
            } else if character == '\\' {
                escaped = true;
            } else if character == '"' {
                let end = offset + 2;
                return Some((read_layout_json(&text[..end])?, start + end));
            }
        }
        None
    } else {
        let end = text.find(", ").unwrap_or(text.len());
        Some((text[..end].to_owned(), start + end))
    }
}

#[derive(Clone, Copy)]
struct TextSegment<'a> {
    reference: &'a str,
    line: usize,
    offset: usize,
    body: &'a str,
}

fn text_segments(lines: &[String]) -> Vec<TextSegment<'_>> {
    let mut segments = Vec::new();
    let mut seen = HashSet::new();
    let mut duplicates = HashSet::new();
    for (index, line) in lines.iter().enumerate() {
        let indent = depth(line);
        let Some((_, suffix)) = decimal_prefix(&line[indent..]) else {
            continue;
        };
        let Some(content) = suffix.strip_prefix(' ') else {
            continue;
        };
        if content.is_empty() || content.contains('\n') {
            continue;
        }
        let mut offset = indent;
        for body in chain_members(&line[indent..]) {
            let Some((reference, rest)) = decimal_prefix(body) else {
                break;
            };
            if !rest.starts_with(' ') {
                break;
            }
            if !seen.insert(reference) {
                duplicates.insert(reference);
            }
            segments.push(TextSegment {
                reference,
                line: index,
                offset,
                body,
            });
            offset += body.len() + 3;
        }
    }
    segments.retain(|segment| !duplicates.contains(segment.reference));
    segments
}

type LineEdits = HashMap<usize, Vec<(usize, usize, String)>>;

fn replace_line_spans(lines: &[String], mut edits: LineEdits) -> Option<Vec<String>> {
    let mut output = lines.to_vec();
    for (line_index, replacements) in &mut edits {
        replacements.sort_unstable_by(|left, right| right.cmp(left));
        let line = output.get_mut(*line_index)?;
        for (start, end, replacement) in replacements {
            line.get(*start..*end)?;
            line.replace_range(*start..*end, replacement);
        }
    }
    Some(output)
}

fn expand_exact_text_slices(lines: &[String]) -> Option<Vec<String>> {
    let segments = text_segments(lines);
    let by_ref: HashMap<_, _> = segments
        .iter()
        .map(|segment| (segment.reference, segment))
        .collect();
    let mut edits: LineEdits = HashMap::new();
    let mut marked_names = HashSet::new();
    for segment in &segments {
        for position in unquoted_positions(segment.body) {
            if position != 0 && segment.body.as_bytes()[position - 1] != b' ' {
                continue;
            }
            let Some(statement) = slice_statement(&segment.body[position..]) else {
                continue;
            };
            let end = position + statement.size;
            if end != segment.body.len() && !segment.body[end..].starts_with(", ") {
                return None;
            }
            let anchor = by_ref.get(statement.reference)?;
            let label = match statement.field {
                TextAttribute::Name => "Name: ",
                TextAttribute::Value => "Value: ",
            };
            let positions = space_label_positions(anchor.body, label);
            let [source] = positions.as_slice() else {
                return None;
            };
            let (value, _) = scalar(anchor.body, source + label.len())?;
            if contains_slice_statement(&value) {
                return None;
            }
            let units: Vec<_> = value.encode_utf16().collect();
            let (slice_start, slice_end) = (
                decimal_number(statement.start)?,
                decimal_number(statement.end)?,
            );
            if slice_start >= slice_end {
                return None;
            }
            let text = String::from_utf16(units.get(slice_start..slice_end)?).ok()?;
            let restored = super::render::field(&text);
            let replacement = if statement.bare {
                restored
            } else {
                format!("Value: {restored}")
            };
            edits.entry(segment.line).or_default().push((
                segment.offset + position,
                segment.offset + end,
                replacement,
            ));
            if statement.field == TextAttribute::Name {
                marked_names.insert(statement.reference);
            }
        }
    }
    for reference in marked_names {
        let anchor = by_ref[reference];
        let locations = space_label_positions(anchor.body, "Name: ");
        let [location] = locations.as_slice() else {
            return None;
        };
        let start = anchor.offset + location;
        edits
            .entry(anchor.line)
            .or_default()
            .push((start, start + 6, String::new()));
    }
    replace_line_spans(lines, edits)
}

struct TextField<'a> {
    reference: &'a str,
    attribute: TextAttribute,
    value: &'a str,
    line: usize,
    start: usize,
    end: usize,
    bare: bool,
}

fn public_text_fields<'a>(frame: &'a LayoutFrame<'_>) -> Vec<TextField<'a>> {
    let mut node_counts = HashMap::new();
    let mut nodes = HashMap::new();
    let mut pending: Vec<_> = frame.roots.iter().collect();
    while let Some(node) = pending.pop() {
        *node_counts.entry(node.reference.as_str()).or_insert(0) += 1;
        nodes.insert(node.reference.as_str(), node);
        pending.extend(&node.children);
    }
    let mut record_counts = HashMap::new();
    let mut records = HashMap::new();
    for record in frame.rendered_nodes {
        *record_counts.entry(record.reference.as_str()).or_insert(0) += 1;
        records.insert(record.reference.as_str(), record);
    }
    let mut fields = Vec::new();
    for segment in text_segments(&frame.lines) {
        let reference = segment.reference;
        let (Some(&node), Some(&record), Some(source), Some(label)) = (
            nodes.get(reference),
            records.get(reference),
            frame.text_slice_sources.get(reference),
            frame.node_prefixes.get(reference),
        ) else {
            continue;
        };
        let prefix = format!("{reference} {label}");
        if node_counts[reference] != 1
            || record_counts[reference] != 1
            || record.partial
            || label.is_empty()
            || prefix.contains(": ")
            || segment.body
                != record
                    .text
                    .split('\n')
                    .next()
                    .unwrap_or("")
                    .trim_start_matches('\t')
            || !segment
                .body
                .strip_prefix(&prefix)
                .is_some_and(|rest| rest.starts_with(' '))
        {
            continue;
        }
        for (attribute, source_value, node_value) in [
            (
                TextAttribute::Name,
                source.name.as_deref(),
                node.name.as_deref(),
            ),
            (
                TextAttribute::Value,
                source.value.as_deref(),
                node.value.as_deref(),
            ),
        ] {
            let Some(value) = source_value.filter(|value| Some(*value) == node_value) else {
                continue;
            };
            if value.is_empty()
                || value.contains(['\r', '\n', '\u{2028}', '\u{2029}'])
                || contains_slice_statement(value)
            {
                continue;
            }
            let bare = attribute == TextAttribute::Value
                && node.role == "staticText"
                && node.name.as_deref().is_none_or(str::is_empty)
                && node.description.as_deref().is_none_or(str::is_empty);
            let start = if attribute == TextAttribute::Name || bare {
                prefix.len() + 1
            } else {
                let positions = space_label_positions(segment.body, "Value: ");
                let [start] = positions.as_slice() else {
                    continue;
                };
                start + 7
            };
            let encoded = super::render::field(value);
            let Some(suffix) = segment.body[start..].strip_prefix(&encoded) else {
                continue;
            };
            if !suffix.is_empty() && !suffix.starts_with(", ") {
                continue;
            }
            let end = start + encoded.len();
            if scalar(segment.body, start)
                .is_none_or(|(decoded, decoded_end)| decoded != value || decoded_end != end)
            {
                continue;
            }
            fields.push(TextField {
                reference,
                attribute,
                value,
                line: segment.line,
                start: segment.offset + start,
                end: segment.offset + end,
                bare,
            });
        }
    }
    fields
}

fn share_exact_text_slices(frame: &LayoutFrame<'_>) -> Option<(Vec<String>, Vec<TextSliceRecord>)> {
    if frame.text_slice_sources.is_empty()
        || frame
            .lines
            .iter()
            .any(|line| contains_slice_statement(line))
    {
        return None;
    }
    let fields = public_text_fields(frame);
    let index: HashMap<_, _> = fields
        .iter()
        .map(|field| ((field.reference, field.attribute), field))
        .collect();
    let mut records = Vec::new();
    let mut edits: LineEdits = HashMap::new();
    let mut anchors = HashSet::new();
    let mut replaced = HashSet::new();
    for field in &fields {
        if field.attribute != TextAttribute::Value
            || anchors.contains(&(field.reference, field.attribute))
        {
            continue;
        }
        let source = &frame.text_slice_sources[field.reference];
        let mut parent_ref = source.parent.as_deref();
        let mut seen = HashSet::from([field.reference]);
        'ancestors: while let Some(reference) =
            parent_ref.filter(|reference| seen.insert(reference))
        {
            let Some(parent) = frame
                .text_slice_sources
                .get(reference)
                .filter(|parent| parent.scope == source.scope)
            else {
                break;
            };
            for attribute in [TextAttribute::Value, TextAttribute::Name] {
                let key = (reference, attribute);
                let Some(&anchor) = index
                    .get(&key)
                    .filter(|owner| !owner.bare && !replaced.contains(&key))
                else {
                    continue;
                };
                let Some(position) = anchor.value.find(field.value) else {
                    continue;
                };
                let step = anchor.value[position..]
                    .chars()
                    .next()
                    .map_or(0, char::len_utf8);
                if anchor.value[position + step..].contains(field.value) {
                    continue;
                }
                let start = anchor.value[..position].encode_utf16().count();
                let end = start + field.value.encode_utf16().count();
                let marker = if field.bare { "Text: " } else { "" };
                let label = attribute.label();
                let statement = format!(
                    "{marker}text [{start}, {end}) of element {reference}.{label} (UTF-16)"
                );
                let annotation =
                    usize::from(attribute == TextAttribute::Name && !anchors.contains(&key)) * 6;
                if field.end - field.start < statement.len() + annotation + 64 {
                    continue;
                }
                edits.entry(field.line).or_default().push((
                    field.start,
                    field.end,
                    statement.clone(),
                ));
                if anchors.insert(key) && attribute == TextAttribute::Name {
                    edits.entry(anchor.line).or_default().push((
                        anchor.start,
                        anchor.start,
                        "Name: ".into(),
                    ));
                }
                replaced.insert((field.reference, field.attribute));
                records.push(TextSliceRecord {
                    reference: field.reference.into(),
                    native: source.native.clone(),
                    field: field.attribute,
                    path: field.attribute.path(),
                    value: field.value.into(),
                    anchor: reference.into(),
                    anchor_native: parent.native.clone(),
                    anchor_field: attribute,
                    anchor_path: attribute.path(),
                    start,
                    end,
                    scope: source.scope.clone(),
                    bare: field.bare,
                    statement,
                });
                break 'ancestors;
            }
            parent_ref = parent.parent.as_deref();
        }
    }
    if records.is_empty() {
        return None;
    }
    let result = replace_line_spans(&frame.lines, edits)?;
    (expand_exact_text_slices(&result).as_deref() == Some(frame.lines.as_slice()))
        .then_some((result, records))
}

/// Facts already admitted by the typed projection, independent of displayed text.
#[derive(Debug, Default)]
pub(super) struct TableNodeEvidence {
    pub(super) index: Option<u64>,
    pub(super) outline: Option<u64>,
    pub(super) row: Option<u64>,
    pub(super) column: Option<u64>,
    pub(super) has_name: bool,
    pub(super) raw_name: Option<String>,
    pub(super) formatted_name: Option<String>,
    pub(super) boundary_subrole: bool,
    pub(super) native_children: Option<Vec<String>>,
}

#[derive(Debug, Default)]
pub(super) struct TableRowCollection {
    pub(super) rows: Vec<String>,
    pub(super) children: Vec<String>,
    pub(super) cells: HashMap<String, Vec<String>>,
}

#[derive(Debug, Default)]
pub(super) struct TableEvidence {
    pub(super) facts: HashMap<String, TableNodeEvidence>,
    pub(super) row_collections: HashMap<String, TableRowCollection>,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct TableLayoutEvidence {
    pub(super) table: String,
    pub(super) layout: Vec<(String, String)>,
    pub(super) introduction: String,
    pub(super) heading: String,
    pub(super) rows: Vec<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct MultiColumnLayout {
    pub(super) lines: Vec<String>,
    pub(super) tables: Vec<TableLayoutEvidence>,
}

struct TableLine<'a> {
    node: &'a LayoutNode,
    record: &'a RenderedNode,
    index: usize,
    indent: &'a str,
    prefix: &'a str,
    body: &'a str,
}

type TablePositions<'a> = HashMap<&'a str, (&'a str, usize)>;
type TableLines<'a> = HashMap<&'a str, TableLine<'a>>;

fn table_number(value: &str) -> bool {
    value == "0"
        || !value.is_empty()
            && !value.starts_with('0')
            && value.bytes().all(|byte| byte.is_ascii_digit())
}

fn table_node(line: &str) -> Option<(&str, &str, &str)> {
    let body = line.trim_start_matches('\t');
    let indent = &line[..line.len() - body.len()];
    let (reference, body) = body.split_once(' ')?;
    (table_number(reference) && !body.is_empty() && !body.contains('\n'))
        .then_some((indent, reference, body))
}

fn safe_table_record(text: &str) -> bool {
    !text.contains(['|', ';', '\r', '\n', '\t'])
        && !["\\r", "\\n", "\\t"]
            .iter()
            .any(|value| text.contains(value))
}

fn generated_table_lines<'a>(
    frame: &'a LayoutFrame<'_>,
    evidence: &'a TableEvidence,
    lines: &'a [String],
    allow_reindent: bool,
) -> (TableLines<'a>, TablePositions<'a>) {
    let mut nodes = HashMap::new();
    let mut occurrences = HashMap::new();
    let mut positions = HashMap::new();
    let mut pending: Vec<_> = frame.roots.iter().collect();
    while let Some(parent) = pending.pop() {
        let reference = parent.reference.as_str();
        nodes.insert(reference, parent);
        *occurrences.entry(reference).or_insert(0usize) += 1;
        pending.extend(&parent.children);
        if !parent.incomplete_children {
            for (position, child) in parent.children.iter().enumerate() {
                positions.insert(child.reference.as_str(), (reference, position));
            }
        }
    }
    let mut records = HashMap::new();
    let mut counts = HashMap::new();
    for record in frame.rendered_nodes {
        records.insert(record.reference.as_str(), record);
        *counts.entry(record.reference.as_str()).or_insert(0usize) += 1;
    }
    let mut line_counts = HashMap::new();
    for (_, reference, _) in lines.iter().filter_map(|line| table_node(line)) {
        *line_counts.entry(reference).or_insert(0usize) += 1;
    }
    let mut result = HashMap::new();
    for (index, line) in lines.iter().enumerate() {
        let Some((indent, reference, body)) = table_node(line) else {
            continue;
        };
        let (Some(&node), Some(&record), Some(prefix), Some(span)) = (
            nodes.get(reference),
            records.get(reference),
            frame.node_prefixes.get(reference),
            frame.node_line_ranges.get(reference),
        ) else {
            continue;
        };
        if occurrences.get(reference) != Some(&1)
            || counts.get(reference) != Some(&1)
            || line_counts.get(reference) != Some(&1)
            || span.end.checked_sub(span.start) != Some(1)
            || record.text.contains('\n')
            || record.partial
            || !record.hints.is_empty()
            || !record.related.is_empty()
            || node.incomplete_children
            || matches!(
                node.role.as_str(),
                "application" | "window" | "sheet" | "menuBar" | "menu"
            )
            || evidence
                .facts
                .get(reference)
                .is_some_and(|facts| facts.boundary_subrole)
            || !body.strip_prefix(prefix).is_some_and(|rest| {
                rest.is_empty() || rest.starts_with(' ') || rest.starts_with(", ")
            })
            || !safe_table_record(body)
            || body.encode_utf16().count() > 320
        {
            continue;
        }
        let expected = if allow_reindent {
            "\t".repeat(record.depth)
        } else {
            indent.to_owned()
        };
        if !record
            .text
            .starts_with(&format!("{expected}{reference} {prefix}"))
        {
            continue;
        }
        result.insert(
            reference,
            TableLine {
                node,
                record,
                index,
                indent,
                prefix,
                body,
            },
        );
    }
    (result, positions)
}

fn table_indentation(line: &str, parent: &str, delta: usize) -> bool {
    line.len().checked_sub(parent.len()) == Some(delta)
        && line
            .strip_prefix(parent)
            .is_some_and(|tail| tail.bytes().all(|value| value == b'\t'))
}

fn table_lines_size(lines: &[String]) -> usize {
    lines.iter().map(String::len).sum::<usize>() + lines.len().saturating_sub(1)
}

fn enough_table_saving(original: &[String], packed: &[String]) -> bool {
    table_lines_size(original)
        .checked_sub(table_lines_size(packed))
        .is_some_and(|saving| saving >= 8)
}

fn no_duplicate_table_refs(lines: &[String]) -> bool {
    let mut refs = HashSet::new();
    lines
        .iter()
        .filter_map(|line| table_node(line))
        .all(|(_, reference, _)| refs.insert(reference))
}

const ROW_RECORD_TITLE: &str = "Records: each row contains its cell; the last column lists displayed cell children in order. Object entries begin with refs.";
const ROW_RECORD_FOOTER: &str = " | cell children (—: none)";

struct SingleTableRow<'a> {
    start: usize,
    end: usize,
    parent: &'a str,
    position: usize,
    indent: &'a str,
    layout: &'a [(String, String)],
    index_label: &'a str,
    cell_prefix: &'a str,
    column: u64,
    text: String,
}

fn row_index_label<'a>(body: &'a str, index: u64) -> Option<(&'a str, &'a str)> {
    for label in ["Index", "Index (0-based)"] {
        let initial = format!("{label}: {index}");
        if let Some(tail) = body.strip_prefix(&initial) {
            return Some((label, tail));
        }
    }
    None
}

fn formatted_table_name(facts: Option<&TableNodeEvidence>) -> Option<&str> {
    match facts {
        Some(facts) if facts.has_name => facts.formatted_name.as_deref(),
        _ => Some(""),
    }
}

fn single_table_row<'a>(
    row: &TableLine<'a>,
    parent: &'a str,
    position: usize,
    lines: &TableLines<'a>,
    frame: &LayoutFrame<'_>,
    evidence: &TableEvidence,
) -> Option<SingleTableRow<'a>> {
    let node = row.node;
    let [child] = node.children.as_slice() else {
        return None;
    };
    if node.role != "row" || !(row.prefix == "row" || row.prefix.starts_with("row (")) {
        return None;
    }
    let cell = lines.get(child.reference.as_str())?;
    if child.role != "cell"
        || cell.record.parent.as_deref() != Some(node.reference.as_str())
        || !table_indentation(cell.indent, row.indent, 1)
        || cell.record.layout != row.record.layout
        || cell.index != row.index + 1
    {
        return None;
    }
    let row_facts = evidence.facts.get(node.reference.as_str())?;
    let cell_facts = evidence.facts.get(child.reference.as_str())?;
    let index = row_facts.index?;
    let (cell_row, column) = (cell_facts.row?, cell_facts.column?);
    if row_facts.has_name
        && row_facts
            .raw_name
            .as_deref()
            .is_none_or(|name| name.starts_with('('))
    {
        return None;
    }
    let row_name = formatted_table_name(Some(row_facts))?;
    let stem = if row_name.is_empty() {
        row.prefix.to_owned()
    } else {
        format!("{} {row_name}", row.prefix)
    };
    let separator = if row_name.is_empty() { " " } else { ", " };
    let (index_label, outline_tail) =
        row_index_label(row.body.strip_prefix(&format!("{stem}{separator}"))?, index)?;
    if !outline_tail.is_empty() {
        let outline = outline_tail.strip_prefix(", Outline level: ")?;
        if !table_number(outline)
            || outline.parse::<u64>().ok() != row_facts.outline
            || row_facts.outline.is_none()
        {
            return None;
        }
    }
    let cell_name = formatted_table_name(Some(cell_facts))?;
    let named = if cell_name.is_empty() {
        String::new()
    } else {
        format!(" {cell_name},")
    };
    if cell.body != format!("{}{named} Row: {cell_row}, Column: {column}", cell.prefix) {
        return None;
    }
    let mut children = Vec::new();
    for (offset, grandchild) in child.children.iter().enumerate() {
        let item = lines.get(grandchild.reference.as_str())?;
        if !grandchild.children.is_empty()
            || item.record.parent.as_deref() != Some(child.reference.as_str())
            || !table_indentation(item.indent, row.indent, 2)
            || item.index != row.index + offset + 2
            || item.record.layout != row.record.layout
        {
            return None;
        }
        children.push(format!("{} {}", grandchild.reference, item.body));
    }
    let end = row.index + 2 + children.len();
    if frame
        .lines
        .get(end)
        .is_some_and(|line| line.starts_with(&format!("{}\t", row.indent)))
    {
        return None;
    }
    let row_body = format!("{}{}{outline_tail}", node.reference, &stem["row".len()..]);
    let cell_body = if cell_name.is_empty() {
        child.reference.clone()
    } else {
        format!("{} {cell_name}", child.reference)
    };
    let child_body = if children.is_empty() {
        "—".to_owned()
    } else {
        children.join("; ")
    };
    let text = format!("{index} / {cell_row} | {row_body} | {cell_body} | {child_body}");
    (text.encode_utf16().count() <= 320).then_some(SingleTableRow {
        start: row.index,
        end,
        parent,
        position,
        indent: row.indent,
        layout: &row.record.layout,
        index_label,
        cell_prefix: cell.prefix,
        column,
        text,
    })
}

fn row_caption_has_name(caption: &str) -> bool {
    if !caption.starts_with('(') {
        return !caption.is_empty();
    }
    caption[1..]
        .split_once(')')
        .is_some_and(|(states, remaining)| {
            !states.contains('(')
                && remaining
                    .strip_prefix(' ')
                    .is_some_and(|name| !name.is_empty() && !name.contains('\n'))
        })
}

fn parse_table_alias(line: &str) -> Option<(&str, &str)> {
    let (reference, body) = line.split_once(' ').unwrap_or((line, ""));
    (table_number(reference) && !body.contains('\n')).then_some((reference, body))
}

fn expand_single_table_member(
    text: &str,
    indent: &str,
    index_label: &str,
    cell_prefix: &str,
    column: &str,
) -> Option<Vec<String>> {
    let fields: Vec<_> = text.split(" | ").collect();
    let [position, mut row, cell, children] = fields.as_slice() else {
        return None;
    };
    let (index, cell_row) = position.split_once(" / ")?;
    if !table_number(index) || !table_number(cell_row) {
        return None;
    }
    let mut outline = "";
    if let Some((before, value)) = row.rsplit_once(", Outline level: ") {
        if table_number(value) {
            row = before;
            outline = &fields[1][before.len()..];
        }
    }
    let (row_ref, caption) = parse_table_alias(row)?;
    let (cell_ref, cell_name) = parse_table_alias(cell)?;
    let caption_prefix = if caption.is_empty() { "" } else { " " };
    let separator = if row_caption_has_name(caption) {
        ", "
    } else {
        " "
    };
    let mut lines = vec![format!(
        "{indent}{row_ref} row{caption_prefix}{caption}{separator}{index_label}: {index}{outline}"
    )];
    let named = if cell_name.is_empty() {
        String::new()
    } else {
        format!(" {cell_name},")
    };
    lines.push(format!(
        "{indent}\t{cell_ref} {cell_prefix}{named} Row: {cell_row}, Column: {column}"
    ));
    if *children != "—" {
        for child in children.split("; ") {
            let (child_ref, body) = parse_table_alias(child)?;
            if body.is_empty() || child_ref.is_empty() {
                return None;
            }
            lines.push(format!("{indent}\t\t{child}"));
        }
    }
    Some(lines)
}

fn single_table_header(line: &str) -> Option<(&str, &str, &str)> {
    let line = line.strip_prefix("row ")?.strip_suffix(ROW_RECORD_FOOTER)?;
    let (label, cell) = line.split_once(" / cell Row | row | ")?;
    let (prefix, column) = cell.rsplit_once(", Column: ")?;
    (matches!(label, "Index" | "Index (0-based)")
        && !prefix.is_empty()
        && !prefix.contains(['|', ';', '\r', '\n'])
        && table_number(column))
    .then_some((label, prefix, column))
}

fn expand_row_records(lines: &[String]) -> Vec<String> {
    let mut output = Vec::new();
    let mut index = 0;
    while index < lines.len() {
        let line = &lines[index];
        let title = line.trim_start_matches('\t');
        let indent = &line[..line.len() - title.len()];
        let header = lines
            .get(index + 1)
            .and_then(|line| line.strip_prefix(indent))
            .and_then(single_table_header);
        if title == ROW_RECORD_TITLE {
            if let Some((index_label, cell_prefix, column)) = header {
                let mut end = index + 2;
                let mut expanded = Vec::new();
                while let Some(line) = lines.get(end).and_then(|line| line.strip_prefix(indent)) {
                    let Some(restored) =
                        expand_single_table_member(line, indent, index_label, cell_prefix, column)
                    else {
                        break;
                    };
                    expanded.extend(restored);
                    end += 1;
                }
                if end - (index + 2) >= 3 && no_duplicate_table_refs(&expanded) {
                    output.extend(expanded);
                    index = end;
                    continue;
                }
            }
        }
        output.push(line.clone());
        index += 1;
    }
    output
}

pub(super) fn pack_row_records(
    frame: &LayoutFrame<'_>,
    evidence: &TableEvidence,
) -> Option<Vec<String>> {
    if frame.mode != LayoutMode::Compact {
        return None;
    }
    let (lines, positions) = generated_table_lines(frame, evidence, &frame.lines, false);
    let mut candidates = HashMap::new();
    for (&reference, line) in &lines {
        let Some(&(parent, position)) = positions.get(reference) else {
            continue;
        };
        if line.record.parent.as_deref() != Some(parent) {
            continue;
        }
        if let Some(row) = single_table_row(line, parent, position, &lines, frame, evidence) {
            candidates.insert(row.start, row);
        }
    }
    let mut output = Vec::new();
    let mut index = 0;
    let mut changed = false;
    while index < frame.lines.len() {
        let Some(first) = candidates.get(&index) else {
            output.push(frame.lines[index].clone());
            index += 1;
            continue;
        };
        let mut members = vec![first];
        let mut end = first.end;
        while let Some(row) = candidates.get(&end) {
            if row.parent != first.parent
                || row.position != first.position + members.len()
                || row.indent != first.indent
                || row.layout != first.layout
                || row.index_label != first.index_label
                || row.cell_prefix != first.cell_prefix
                || row.column != first.column
            {
                break;
            }
            members.push(row);
            end = row.end;
        }
        let indent = first.indent;
        let mut packed = vec![
            format!("{indent}{ROW_RECORD_TITLE}"),
            format!(
                "{indent}row {} / cell Row | row | {}, Column: {}{ROW_RECORD_FOOTER}",
                first.index_label, first.cell_prefix, first.column,
            ),
        ];
        packed.extend(members.iter().map(|row| format!("{indent}{}", row.text)));
        let original = &frame.lines[index..end];
        if members.len() >= 3
            && enough_table_saving(original, &packed)
            && expand_row_records(&packed) == original
        {
            output.extend(packed);
            index = end;
            changed = true;
        } else {
            output.extend_from_slice(&frame.lines[index..first.end]);
            index = first.end;
        }
    }
    (changed && expand_row_records(&output) == frame.lines).then_some(output)
}

const TABLE_INTRO_TAIL: &str = " > child; siblings use ;.";
const TABLE_CELL_TEXT: &str = " with its own text";

struct MultiTableRow<'a> {
    start: usize,
    end: usize,
    parent: &'a str,
    position: usize,
    indent: &'a str,
    layout: &'a [(String, String)],
    row_prefix: &'a str,
    index_label: &'a str,
    outline: u64,
    cell_prefix: &'a str,
    columns: usize,
    text: String,
    cell_text: bool,
    reference: &'a str,
}

impl MultiTableRow<'_> {
    fn same_layout(&self, other: &Self) -> bool {
        self.parent == other.parent
            && self.indent == other.indent
            && self.layout == other.layout
            && self.row_prefix == other.row_prefix
            && self.index_label == other.index_label
            && self.outline == other.outline
            && self.cell_prefix == other.cell_prefix
            && self.columns == other.columns
    }
}

fn table_outline_row(table: &TableLine<'_>, row: &TableLine<'_>, evidence: &TableEvidence) -> bool {
    let table_ref = table.node.reference.as_str();
    let row_ref = row.node.reference.as_str();
    let Some(proof) = evidence.row_collections.get(table_ref) else {
        return false;
    };
    let Some(cells) = proof.cells.get(row_ref) else {
        return false;
    };
    let native_children = |reference| {
        evidence
            .facts
            .get(reference)
            .and_then(|node| node.native_children.as_ref())
    };
    proof.rows.iter().any(|reference| reference == row_ref)
        && native_children(table_ref) == Some(&proof.children)
        && native_children(row_ref) == Some(cells)
        && row
            .node
            .children
            .iter()
            .map(|child| child.reference.as_str())
            .eq(cells.iter().map(String::as_str))
}

fn table_cell_body<'a>(
    cell: &TableLine<'a>,
    row: u64,
    column: u64,
    has_name: bool,
) -> Option<&'a str> {
    let coordinates = format!("Row: {row}, Column: {column}");
    let body = cell.body.strip_prefix(cell.prefix)?.strip_prefix(' ')?;
    if body == coordinates {
        return (!has_name).then_some("");
    }
    let own = body.strip_suffix(&format!(", {coordinates}"))?;
    (!own.is_empty() && !own.contains(" > ")).then_some(own)
}

fn table_same_offset(item: &TableLine<'_>, parent: &TableLine<'_>) -> bool {
    item.indent.len() as i128 - item.record.depth as i128
        == parent.indent.len() as i128 - parent.record.depth as i128
}

fn table_branch(
    item: &TableLine<'_>,
    parent: &TableLine<'_>,
    expected_index: usize,
    lines: &TableLines<'_>,
    depth: usize,
) -> Option<(String, usize)> {
    if item.record.parent.as_deref() != Some(parent.node.reference.as_str())
        || !table_indentation(item.indent, parent.indent, 1)
        || item.index != expected_index
        || item.record.layout != parent.record.layout
        || !table_same_offset(item, parent)
        || item.node.children.len() > 1
        || depth > 2
        || item.body.contains(" > ")
    {
        return None;
    }
    let mut text = format!("{} {}", item.node.reference, item.body);
    let mut end = item.index + 1;
    if let Some(child) = item.node.children.first() {
        let (nested, next) = table_branch(
            lines.get(child.reference.as_str())?,
            item,
            end,
            lines,
            depth + 1,
        )?;
        text.push_str(" > ");
        text.push_str(&nested);
        end = next;
    }
    Some((text, end))
}

fn multi_table_row<'a>(
    item: &TableLine<'a>,
    parent: &'a str,
    position: usize,
    lines: &TableLines<'a>,
    working_lines: &[String],
    evidence: &TableEvidence,
) -> Option<MultiTableRow<'a>> {
    let table = lines.get(parent)?;
    let node = item.node;
    let row = evidence.facts.get(node.reference.as_str())?;
    let (index, outline) = (row.index?, row.outline?);
    if (table.node.role != "table"
        && (table.node.role != "outline" || !table_outline_row(table, item, evidence)))
        || node.role != "row"
        || item.record.parent.as_deref() != Some(parent)
        || item.record.layout != table.record.layout
        || !table_indentation(item.indent, table.indent, 1)
        || !(2..=5).contains(&node.children.len())
        || !table_same_offset(item, table)
        || row.has_name
    {
        return None;
    }
    let (index_label, tail) = row_index_label(
        item.body.strip_prefix(item.prefix)?.strip_prefix(' ')?,
        index,
    )?;
    if tail != format!(", Outline level: {outline}") {
        return None;
    }
    let mut cells = Vec::new();
    let mut shared = None;
    let mut has_cell_text = false;
    let mut end = item.index + 1;
    for (offset, child) in node.children.iter().enumerate() {
        let column = offset as u64 + 1;
        let cell = lines.get(child.reference.as_str())?;
        let facts = evidence.facts.get(child.reference.as_str())?;
        let row_number = facts.row?;
        if !matches!(child.role.as_str(), "cell" | "columnHeader")
            || child.children.len() > 6
            || facts.column != Some(column)
            || cell.record.parent.as_deref() != Some(node.reference.as_str())
            || cell.record.layout != item.record.layout
            || !table_indentation(cell.indent, item.indent, 1)
            || cell.index != end
            || !table_same_offset(cell, item)
        {
            return None;
        }
        let own = table_cell_body(cell, row_number, column, facts.has_name)?;
        has_cell_text |= !own.is_empty();
        let cell_type = (cell.prefix, child.role.as_str(), row_number);
        if *shared.get_or_insert(cell_type) != cell_type {
            return None;
        }
        let mut contents = Vec::new();
        end += 1;
        for child in &child.children {
            let (branch, next) =
                table_branch(lines.get(child.reference.as_str())?, cell, end, lines, 0)?;
            contents.push(branch);
            end = next;
        }
        let mut cell_text = child.reference.clone();
        if !own.is_empty() {
            cell_text.push(' ');
            cell_text.push_str(own);
        }
        if !contents.is_empty() {
            cell_text.push_str(" > ");
            cell_text.push_str(&contents.join(" ; "));
        }
        if cell_text.encode_utf16().count() > 320 {
            return None;
        }
        cells.push(cell_text);
    }
    if working_lines
        .get(end)
        .is_some_and(|line| line.starts_with(&format!("{}\t", item.indent)))
    {
        return None;
    }
    let (cell_prefix, _, row_number) = shared?;
    let text = format!(
        "{row_number} | {} ({index}) | {}",
        node.reference,
        cells.join(" | ")
    );
    (text.encode_utf16().count() <= 640).then_some(MultiTableRow {
        start: item.index,
        end,
        parent,
        position,
        indent: item.indent,
        layout: &item.record.layout,
        row_prefix: item.prefix,
        index_label,
        outline,
        cell_prefix,
        columns: cells.len(),
        text,
        cell_text: has_cell_text,
        reference: node.reference.as_str(),
    })
}

fn multi_table_intro(line: &str) -> Option<(&str, &str, &str, &str, bool)> {
    let body = line.trim_start_matches('\t');
    let indent = &line[..line.len() - body.len()];
    let body = body
        .strip_prefix("Rows: ")?
        .strip_suffix(TABLE_INTRO_TAIL)?;
    let (body, own_text) = body
        .strip_suffix(TABLE_CELL_TEXT)
        .map_or((body, false), |body| (body, true));
    let body = body.strip_suffix(". Entries: row-ref (Index); cell-ref")?;
    let (row, cell_prefix) = body.split_once("; cells: ")?;
    let (row_prefix, outline) = row.rsplit_once(", Outline level: ")?;
    if row_prefix.is_empty()
        || cell_prefix.is_empty()
        || row_prefix.contains([';', '\r', '\n'])
        || cell_prefix.contains([';', '\r', '\n'])
        || !table_number(outline)
    {
        return None;
    }
    Some((indent, row_prefix, outline, cell_prefix, own_text))
}

fn multi_table_heading<'a>(
    lines: &'a [String],
    start: usize,
    indent: &str,
) -> Option<(&'a str, usize)> {
    let heading = lines.get(start)?.strip_prefix(indent)?;
    let columns: Vec<_> = heading.split(" | ").collect();
    let &["Row", index_column, ref cells @ ..] = columns.as_slice() else {
        return None;
    };
    let index_label = index_column.strip_prefix("row (")?.strip_suffix(')')?;
    if !(2..=5).contains(&cells.len())
        || !matches!(index_label, "Index" | "Index (0-based)")
        || cells
            .iter()
            .enumerate()
            .any(|(index, cell)| *cell != format!("Column {}", index + 1))
        || lines.get(start + 1)?.strip_prefix(indent)? != vec!["---"; columns.len()].join(" | ")
    {
        return None;
    }
    Some((index_label, cells.len()))
}

fn expand_multi_table_member(
    text: &str,
    indent: &str,
    row_prefix: &str,
    index_label: &str,
    outline: &str,
    cell_prefix: &str,
    columns: usize,
    own_text: bool,
) -> Option<Vec<String>> {
    let parts: Vec<_> = text.split(" | ").collect();
    if parts.len() != columns + 2 || !table_number(parts[0]) {
        return None;
    }
    let (row_ref, index) = parts[1].strip_suffix(')')?.split_once(" (")?;
    if !table_number(row_ref) || !table_number(index) {
        return None;
    }
    let mut lines = vec![format!(
        "{indent}{row_ref} {row_prefix} {index_label}: {index}, Outline level: {outline}"
    )];
    for (offset, entry) in parts[2..].iter().enumerate() {
        let (cell, descendants) = entry
            .split_once(" > ")
            .map_or((*entry, None), |(cell, descendants)| {
                (cell, Some(descendants))
            });
        let (reference, own) = parse_table_alias(cell)?;
        if own.is_empty() && cell.contains(' ') || !own.is_empty() && !own_text {
            return None;
        }
        let body = if own.is_empty() {
            String::new()
        } else {
            format!("{own}, ")
        };
        lines.push(format!(
            "{indent}\t{reference} {cell_prefix} {body}Row: {}, Column: {}",
            parts[0],
            offset + 1
        ));
        if let Some(descendants) = descendants {
            for branch in descendants.split(" ; ") {
                for (depth, child) in branch.split(" > ").enumerate() {
                    let (_, body) = parse_table_alias(child)?;
                    if body.is_empty() {
                        return None;
                    }
                    lines.push(format!("{indent}{}{child}", "\t".repeat(depth + 2)));
                }
            }
        }
    }
    Some(lines)
}

fn expand_multi_column_records(lines: &[String]) -> Vec<String> {
    let mut output = Vec::new();
    let mut index = 0;
    while index < lines.len() {
        if let Some((indent, row_prefix, outline, cell_prefix, own_text)) =
            multi_table_intro(&lines[index])
        {
            if let Some((index_label, columns)) = multi_table_heading(lines, index + 1, indent) {
                let mut end = index + 3;
                let mut expanded = Vec::new();
                while let Some(line) = lines.get(end).and_then(|line| line.strip_prefix(indent)) {
                    let Some(restored) = expand_multi_table_member(
                        line,
                        indent,
                        row_prefix,
                        index_label,
                        outline,
                        cell_prefix,
                        columns,
                        own_text,
                    ) else {
                        break;
                    };
                    expanded.extend(restored);
                    end += 1;
                }
                if end - (index + 3) >= 3 && no_duplicate_table_refs(&expanded) {
                    output.extend(expanded);
                    index = end;
                    continue;
                }
            }
        }
        output.push(lines[index].clone());
        index += 1;
    }
    output
}

pub(super) fn pack_multi_column_layout(
    frame: &LayoutFrame<'_>,
    evidence: &TableEvidence,
) -> Option<MultiColumnLayout> {
    if frame.mode != LayoutMode::Compact {
        return None;
    }
    let expanded = expand_leaf_headers(&frame.lines);
    let (lines, positions) = generated_table_lines(frame, evidence, &expanded, true);
    let mut original_positions = HashMap::<&str, Vec<usize>>::new();
    for (index, line) in frame.lines.iter().enumerate() {
        original_positions.entry(line).or_default().push(index);
    }
    let mut candidates = HashMap::new();
    for (&reference, line) in &lines {
        let Some(&(parent, position)) = positions.get(reference) else {
            continue;
        };
        let Some(mut row) = multi_table_row(line, parent, position, &lines, &expanded, evidence)
        else {
            continue;
        };
        let Some(&[start]) = original_positions
            .get(expanded[row.start].as_str())
            .map(Vec::as_slice)
        else {
            continue;
        };
        let mut end = start + 1;
        let prefix = format!("{}\t", row.indent);
        while frame
            .lines
            .get(end)
            .is_some_and(|line| line.starts_with(&prefix))
        {
            end += 1;
        }
        if expand_leaf_headers(&frame.lines[start..end]) == expanded[row.start..row.end] {
            row.start = start;
            row.end = end;
            candidates.insert(start, row);
        }
    }
    let mut output = Vec::new();
    let mut tables = Vec::new();
    let mut index = 0;
    while index < frame.lines.len() {
        let Some(first) = candidates.get(&index) else {
            output.push(frame.lines[index].clone());
            index += 1;
            continue;
        };
        let mut members = vec![first];
        let mut end = first.end;
        while let Some(row) = candidates.get(&end) {
            if !row.same_layout(first) || row.position != first.position + members.len() {
                break;
            }
            members.push(row);
            end = row.end;
        }
        let indent = first.indent;
        let own_text = if members.iter().any(|row| row.cell_text) {
            TABLE_CELL_TEXT
        } else {
            ""
        };
        let mut headings = vec!["Row".to_owned(), format!("row ({})", first.index_label)];
        headings.extend((1..=first.columns).map(|column| format!("Column {column}")));
        let mut packed = vec![format!(
            "{indent}Rows: {}, Outline level: {}; cells: {}. Entries: row-ref (Index); cell-ref{own_text}{TABLE_INTRO_TAIL}",
            first.row_prefix, first.outline, first.cell_prefix,
        ), format!("{indent}{}", headings.join(" | ")), format!("{indent}{}", vec!["---"; first.columns + 2].join(" | "))];
        packed.extend(members.iter().map(|row| format!("{indent}{}", row.text)));
        let original = &frame.lines[index..end];
        if members.len() >= 3
            && enough_table_saving(original, &packed)
            && expand_multi_column_records(&packed) == expand_leaf_headers(original)
        {
            tables.push(TableLayoutEvidence {
                table: first.parent.to_owned(),
                layout: first.layout.to_vec(),
                introduction: packed[0].clone(),
                heading: packed[1].clone(),
                rows: members.iter().map(|row| row.reference.to_owned()).collect(),
            });
            output.extend(packed);
            index = end;
        } else {
            output.extend_from_slice(&frame.lines[index..first.end]);
            index = first.end;
        }
    }
    (!tables.is_empty() && expand_leaf_headers(&expand_multi_column_records(&output)) == expanded)
        .then_some(MultiColumnLayout {
            lines: output,
            tables,
        })
}

#[derive(Debug)]
pub(super) struct NativeFieldSource {
    pub(super) native: String,
    pub(super) scope: [String; 3],
    pub(super) name: Option<String>,
    pub(super) description: Option<String>,
    pub(super) url: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(super) enum FieldKind {
    Caption,
    Url,
}

impl FieldKind {
    fn label(self) -> &'static str {
        match self {
            Self::Caption => "Caption",
            Self::Url => "URL",
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct FieldPrefixReplacement {
    pub(super) reference: String,
    pub(super) native: String,
    pub(super) scope: [String; 3],
    pub(super) path: &'static [&'static str],
    pub(super) field: FieldKind,
    pub(super) value: String,
    pub(super) prefix: String,
    pub(super) suffix: String,
    pub(super) quoted: bool,
    pub(super) declaration: String,
    pub(super) replacement: String,
}

fn labelled_positions(body: &str, marker: &str, minimum: usize, first_field: bool) -> Vec<usize> {
    unquoted_positions(body)
        .filter(|&position| {
            if position < minimum || !body[position..].starts_with(marker) {
                return false;
            }
            position == minimum
                || body.as_bytes()[position.saturating_sub(2)..position] == *b", "
                || first_field
                    && position != 0
                    && body.as_bytes()[position - 1] == b' '
                    && !body[..position].contains(':')
        })
        .collect()
}

fn field_span(
    body: &str,
    reference: &str,
    prefix: &str,
    kind: FieldKind,
    encoded: &str,
) -> Option<(usize, usize)> {
    let node_prefix = format!("{reference} {prefix} ");
    body.strip_prefix(&node_prefix)?;
    let field_start = node_prefix.len();
    let (start, value_start) = match kind {
        FieldKind::Caption => (field_start, field_start),
        FieldKind::Url => {
            let positions: Vec<_> = unquoted_positions(body)
                .filter(|&position| {
                    body[position..].starts_with("URL: ")
                        && (position == field_start
                            || body.as_bytes()[position.saturating_sub(2)..position] == *b", ")
                })
                .collect();
            let [start] = positions.as_slice() else {
                return None;
            };
            (*start, start + 5)
        }
    };
    let tail = body.get(value_start..)?.strip_prefix(encoded)?;
    (tail.is_empty() || tail.starts_with(", ")).then_some((start, value_start + encoded.len()))
}

struct PrefixField<'a> {
    reference: &'a str,
    source: &'a NativeFieldSource,
    path: &'static [&'static str],
    kind: FieldKind,
    value: &'a str,
    quoted: bool,
    line: usize,
    start: usize,
    end: usize,
}

fn prefix_fields<'a>(frame: &'a LayoutFrame<'_>) -> Option<(usize, Vec<PrefixField<'a>>)> {
    let [root] = frame.roots else {
        return None;
    };
    if is_menu(&root.role) {
        return None;
    }
    let mut nodes = HashMap::new();
    let mut parents = HashMap::new();
    let mut occurrences = HashMap::new();
    let mut pending = vec![(root, None)];
    while let Some((node, parent)) = pending.pop() {
        let reference = node.reference.as_str();
        nodes.insert(reference, node);
        parents.insert(reference, parent);
        *occurrences.entry(reference).or_insert(0) += 1;
        pending.extend(node.children.iter().map(|child| (child, Some(reference))));
    }
    let mut records = HashMap::new();
    let mut counts = HashMap::new();
    for record in frame.rendered_nodes {
        records.insert(record.reference.as_str(), record);
        *counts.entry(record.reference.as_str()).or_insert(0) += 1;
    }
    let entries = unique_numbered_entries(&frame.lines);
    let root_entry = entries.get(root.reference.as_str())?;
    if counts.get(root.reference.as_str()) != Some(&1) {
        return None;
    }
    let expanded = expand_tree_chains(&expand_leaf_headers(&expand_multi_column_records(
        &expand_row_records(&frame.lines),
    )));
    let mut verified: HashMap<&str, Vec<&str>> = HashMap::new();
    for line in &expanded {
        if let Some(entry) = node_line(line) {
            verified
                .entry(entry.reference)
                .or_default()
                .push(&line[entry.depth..]);
        }
    }
    let mut output = Vec::new();
    for (reference, source) in frame.field_prefix_sources {
        let reference = reference.as_str();
        let (Some(&node), Some(&record), Some(entry), Some(prefix), Some(span)) = (
            nodes.get(reference),
            records.get(reference),
            entries.get(reference),
            frame.node_prefixes.get(reference),
            frame.node_line_ranges.get(reference),
        ) else {
            continue;
        };
        if prefix.is_empty()
            || span.is_empty()
            || occurrences[reference] != 1
            || counts[reference] != 1
            || source.scope[2] != root.reference
            || record.parent.as_deref() != parents[reference]
            || is_menu(&node.role)
        {
            continue;
        }
        let current = entry.expanded();
        if verified
            .get(reference)
            .is_none_or(|lines| lines.as_slice() != [current.as_ref()])
        {
            continue;
        }
        let original = record
            .text
            .split('\n')
            .next()
            .unwrap_or("")
            .trim_start_matches('\t');
        let mut fields: Vec<(FieldKind, &str, &'static [&'static str])> = Vec::new();
        if let Some(value) = node.name.as_deref() {
            let name = source.name.as_deref() == Some(value);
            let description = source.description.as_deref() == Some(value);
            if name != description {
                fields.push((
                    FieldKind::Caption,
                    value,
                    if name { &["name"] } else { &["description"] },
                ));
            }
        }
        if let Some(url) = source
            .url
            .as_deref()
            .filter(|url| node.url.as_deref() == Some(*url))
        {
            fields.push((FieldKind::Url, url, &["url"]));
        }
        for (kind, value, path) in fields {
            if !plain_text(value) {
                continue;
            }
            let encoded = super::render::field(value);
            if field_span(original, reference, prefix, kind, &encoded).is_none() {
                continue;
            }
            let Some((start, end)) =
                field_span(current.as_ref(), reference, prefix, kind, &encoded)
            else {
                continue;
            };
            let quoted = encoded.starts_with('"');
            if (quoted && read_layout_json(&encoded).as_deref() != Some(value))
                || (!quoted && encoded != value)
            {
                continue;
            }
            let removed = if entry.heading.is_empty() {
                0
            } else {
                entry.heading.len() + 1
            };
            let Some(local_start) = start.checked_sub(removed) else {
                continue;
            };
            if local_start < reference.len() + 1 {
                continue;
            }
            output.push(PrefixField {
                reference,
                source,
                path,
                kind,
                value,
                quoted,
                line: entry.line,
                start: entry.start + local_start,
                end: entry.start + end - removed,
            });
        }
    }
    Some((root_entry.line, output))
}

fn field_declaration(kind: FieldKind, prefix: &str, quoted: bool) -> String {
    let name = kind.label();
    let quoting = if quoted { "quoted " } else { "" };
    let prefix = super::render::quoted(prefix);
    format!("{name} prefix (prepend to {quoting}{name} suffix): {prefix}")
}

fn parse_field_declaration(line: &str) -> Option<(FieldKind, bool, &str)> {
    for kind in [FieldKind::Caption, FieldKind::Url] {
        let Some(value) = line
            .strip_prefix(kind.label())
            .and_then(|text| text.strip_prefix(" prefix (prepend to "))
        else {
            continue;
        };
        let (quoted, rest) = if let Some(rest) = value.strip_prefix("quoted ") {
            (true, rest)
        } else {
            (false, value)
        };
        let suffix = rest
            .strip_prefix(kind.label())?
            .strip_prefix(" suffix): ")?;
        return (!suffix.is_empty() && !suffix.contains('\n')).then_some((kind, quoted, suffix));
    }
    None
}

fn field_scope_boundary(line: &str) -> bool {
    line.starts_with("Application menu:")
        || line.starts_with("Application menu (")
        || line
            .strip_prefix("Window ")
            .and_then(|text| text.split_once(':'))
            .is_some_and(|(number, _)| {
                !number.is_empty() && number.bytes().all(|digit| digit.is_ascii_digit())
            })
}

fn expand_field_prefixes(lines: &[String]) -> Option<Vec<String>> {
    let mut output = Vec::new();
    let mut prefixes = HashMap::new();
    let mut indent = 0;
    let mut started = false;
    let mut entry_ranges: HashMap<usize, Vec<Range<usize>>> = HashMap::new();
    for entry in numbered_entries(lines) {
        entry_ranges
            .entry(entry.line)
            .or_default()
            .push(entry.start..entry.start + entry.body.len());
    }
    for (index, line) in lines.iter().enumerate() {
        let line_depth = depth(line);
        let stripped = &line[line_depth..];
        if field_scope_boundary(stripped) {
            prefixes.clear();
            started = false;
        }
        if let Some((kind, quoted, raw)) = parse_field_declaration(stripped) {
            if started {
                prefixes.clear();
                started = false;
            }
            if !prefixes.is_empty() && indent != line_depth || prefixes.contains_key(&kind) {
                return None;
            }
            let value = read_layout_json(raw).filter(|value| plain_text(value))?;
            prefixes.insert(kind, (value, quoted));
            indent = line_depth;
            continue;
        }
        if stripped.starts_with(IDENTIFIER_DECLARATION) {
            output.push(line.clone());
            continue;
        }
        if !prefixes.is_empty() && node_line(line).is_some() {
            if started && line_depth <= indent {
                prefixes.clear();
                started = false;
            } else if !started {
                if line_depth != indent {
                    return None;
                }
                started = true;
            }
        }
        let mut rendered = line.clone();
        if !prefixes.is_empty() && started {
            let mut edits = Vec::new();
            for position in unquoted_positions(line) {
                if position != 0 && line.as_bytes()[position - 1] != b' ' {
                    continue;
                }
                if entry_ranges
                    .get(&index)
                    .is_none_or(|ranges| !ranges.iter().any(|range| range.contains(&position)))
                {
                    continue;
                }
                let Some((kind, marker)) = [
                    (FieldKind::Caption, "Caption suffix: "),
                    (FieldKind::Url, "URL suffix: "),
                ]
                .into_iter()
                .find(|(_, marker)| line[position..].starts_with(marker)) else {
                    continue;
                };
                let (prefix, quoted) = prefixes.get(&kind)?;
                let start = position + marker.len();
                if !line[start..].starts_with('"') {
                    return None;
                }
                let (suffix, end) = scalar(line, start)?;
                if end != line.len()
                    && ![", ", " > ", " ; ", "; ", " | "]
                        .iter()
                        .any(|delimiter| line[end..].starts_with(delimiter))
                {
                    return None;
                }
                let value = format!("{prefix}{suffix}");
                let value = if *quoted {
                    super::render::quoted(&value)
                } else {
                    value
                };
                let restored = if kind == FieldKind::Url {
                    format!("URL: {value}")
                } else {
                    value
                };
                edits.push((position, end, restored));
            }
            for (start, end, replacement) in edits.into_iter().rev() {
                rendered.replace_range(start..end, &replacement);
            }
        }
        output.push(rendered);
    }
    Some(output)
}

fn common_prefix<'a>(left: &'a str, right: &str) -> &'a str {
    let end = left
        .char_indices()
        .zip(right.chars())
        .take_while(|((_, left), right)| left == right)
        .map(|((index, character), _)| index + character.len_utf8())
        .last()
        .unwrap_or(0);
    &left[..end]
}

struct PrefixGroup<'a> {
    saving: i64,
    prefix: &'a str,
    quoted: bool,
    members: Vec<&'a PrefixField<'a>>,
    declaration: String,
}

fn best_field_group<'a>(
    kind: FieldKind,
    members: &[&'a PrefixField<'a>],
    frame: &LayoutFrame<'_>,
    indent: &str,
) -> Option<PrefixGroup<'a>> {
    let mut best: Option<PrefixGroup<'a>> = None;
    for quoted in [false, true] {
        let mut ordered: Vec<_> = members
            .iter()
            .copied()
            .filter(|member| member.quoted == quoted)
            .collect();
        ordered.sort_by_key(|member| member.value);
        let mut prefixes = std::collections::BTreeSet::new();
        for pair in ordered.windows(2) {
            let common = common_prefix(pair[0].value, pair[1].value);
            if !common.is_empty() {
                prefixes.insert(common);
            }
            prefixes.extend(common.char_indices().filter_map(|(index, character)| {
                matches!(character, '.' | '/' | ':' | '_').then(|| &common[..index + 1])
            }));
        }
        for prefix in prefixes {
            let start = ordered.partition_point(|member| member.value < prefix);
            let matching: Vec<_> = ordered[start..]
                .iter()
                .copied()
                .take_while(|member| member.value.starts_with(prefix))
                .collect();
            if matching.len() < 2 {
                continue;
            }
            let declaration = field_declaration(kind, prefix, quoted);
            let saving = matching
                .iter()
                .map(|member| {
                    let replacement = kind.label().len()
                        + " suffix: ".len()
                        + super::render::quoted(&member.value[prefix.len()..]).len();
                    (member.end - member.start) as i64 - replacement as i64
                })
                .sum::<i64>()
                - (indent.len() + declaration.len() + 1) as i64;
            if saving >= 32
                && best
                    .as_ref()
                    .is_none_or(|previous| saving > previous.saving)
            {
                best = Some(PrefixGroup {
                    saving,
                    prefix,
                    quoted,
                    members: matching,
                    declaration,
                });
            }
        }
    }
    let _ = frame;
    best
}

fn group_field_prefixes(
    frame: &LayoutFrame<'_>,
) -> Option<(Vec<String>, Vec<FieldPrefixReplacement>)> {
    if frame.field_prefix_sources.is_empty() {
        return None;
    }
    let (root_index, members) = prefix_fields(frame)?;
    if members.len() < 2
        || members
            .iter()
            .any(|member| member.source.scope != members[0].source.scope)
    {
        return None;
    }
    let root_line = &frame.lines[root_index];
    let indent = &root_line[..depth(root_line)];
    let mut kinds: Vec<(FieldKind, Vec<&PrefixField<'_>>)> = Vec::new();
    for member in &members {
        if let Some((_, items)) = kinds.iter_mut().find(|(kind, _)| *kind == member.kind) {
            items.push(member);
        } else {
            kinds.push((member.kind, vec![member]));
        }
    }
    let mut edits: LineEdits = HashMap::new();
    let mut replacements = Vec::new();
    let mut declarations = Vec::new();
    for (kind, items) in kinds {
        let Some(group) = best_field_group(kind, &items, frame, indent) else {
            continue;
        };
        declarations.push(format!("{indent}{}", group.declaration));
        for member in group.members {
            let suffix = &member.value[group.prefix.len()..];
            let replacement = format!("{} suffix: {}", kind.label(), super::render::quoted(suffix));
            edits.entry(member.line).or_default().push((
                member.start,
                member.end,
                replacement.clone(),
            ));
            replacements.push(FieldPrefixReplacement {
                reference: member.reference.into(),
                native: member.source.native.clone(),
                scope: member.source.scope.clone(),
                path: member.path,
                field: member.kind,
                value: member.value.into(),
                prefix: group.prefix.into(),
                suffix: suffix.into(),
                quoted: group.quoted,
                declaration: group.declaration.clone(),
                replacement,
            });
        }
    }
    if replacements.is_empty() {
        return None;
    }
    for changes in edits.values_mut() {
        changes.sort_unstable();
        if changes.windows(2).any(|pair| pair[0].1 > pair[1].0) {
            return None;
        }
    }
    let mut output = replace_line_spans(&frame.lines, edits)?;
    output.splice(root_index..root_index, declarations);
    (expand_field_prefixes(&output).as_deref() == Some(frame.lines.as_slice()))
        .then_some((output, replacements))
}

const TABLE_ENTRIES_DECLARATION: &str = "Table entries in this root: ";
const TABLE_ENTRIES_GRAMMAR: &str = "row-ref (Index); cell-ref > child; siblings use ;.";
const TABLE_OWN_ENTRIES_GRAMMAR: &str =
    "row-ref (Index); cell-ref with its own text > child; siblings use ;.";

#[derive(Debug, PartialEq, Eq)]
pub(super) struct TableEntriesReplacement {
    pub(super) table: String,
    pub(super) root: String,
    pub(super) layout: Vec<(String, String)>,
    pub(super) rows: Vec<String>,
    pub(super) original: String,
    pub(super) replacement: String,
    pub(super) declaration: String,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct TableEntriesLayout {
    pub(super) lines: Vec<String>,
    pub(super) replacements: Vec<TableEntriesReplacement>,
}

impl TableEntriesLayout {
    /// The finalizer records coverage only while these exact lines are published.
    pub(super) fn published_replacements(
        &self,
        final_lines: &[String],
        rendered: &str,
    ) -> &[TableEntriesReplacement] {
        if self.lines == final_lines && rendered == final_lines.join("\n") {
            &self.replacements
        } else {
            &[]
        }
    }
}

fn unsigned_digits(value: &str) -> bool {
    !value.is_empty() && value.bytes().all(|value| value.is_ascii_digit())
}

fn table_entry_grammar(value: &str) -> bool {
    matches!(value, TABLE_ENTRIES_GRAMMAR | TABLE_OWN_ENTRIES_GRAMMAR)
}

fn table_entry_intro(line: &str) -> Option<(&str, Option<&str>)> {
    fn intro_stem(body: &str) -> bool {
        let Some(body) = body
            .strip_prefix("Rows: ")
            .and_then(|body| body.strip_suffix('.'))
        else {
            return false;
        };
        let Some((row, cell)) = body.split_once("; cells: ") else {
            return false;
        };
        let Some((row_prefix, outline)) = row.rsplit_once(", Outline level: ") else {
            return false;
        };
        !row_prefix.is_empty()
            && !cell.is_empty()
            && !row_prefix.contains([';', '\r', '\n'])
            && !cell.contains([';', '\r', '\n'])
            && unsigned_digits(outline)
    }
    let body = line.trim_start_matches('\t');
    let indent = &line[..line.len() - body.len()];
    if intro_stem(body) {
        return Some((indent, None));
    }
    for (position, _) in body.rmatch_indices(" Entries: ") {
        let (stem, tail) = body.split_at(position);
        let grammar = &tail[" Entries: ".len()..];
        if !grammar.is_empty() && !grammar.contains('\n') && intro_stem(stem) {
            return Some((indent, Some(grammar)));
        }
    }
    None
}

fn table_entry_row(line: &str) -> Option<&str> {
    let (position, rest) = line.split_once(" | ")?;
    let (row, _) = rest.split_once(" | ")?;
    let (reference, index) = row.strip_suffix(')')?.split_once(" (")?;
    (unsigned_digits(position) && unsigned_digits(reference) && unsigned_digits(index))
        .then_some(reference)
}

fn table_entries_block<'a>(
    lines: &'a [String],
    index: usize,
    grammar: &str,
) -> Option<Vec<&'a str>> {
    let (indent, own_grammar) = table_entry_intro(lines.get(index)?)?;
    if index + 5 >= lines.len() {
        return None;
    }
    let start = index + 3;
    let rows: Vec<_> = lines[start..]
        .iter()
        .map_while(|line| line.strip_prefix(indent).and_then(table_entry_row))
        .collect();
    if rows.len() < 3 || rows.iter().copied().collect::<HashSet<_>>().len() != rows.len() {
        return None;
    }
    let mut block = lines[index..start + rows.len()].to_vec();
    if own_grammar.is_none() {
        block[0].push_str(" Entries: ");
        block[0].push_str(grammar);
    }
    (expand_multi_column_records(&block) != block).then_some(rows)
}

pub(super) fn expand_table_entries(lines: &[String]) -> Vec<String> {
    let mut output = Vec::new();
    let mut index = 0;
    while index < lines.len() {
        let line = &lines[index];
        let body = line.trim_start_matches('\t');
        let indent = &line[..line.len() - body.len()];
        let grammar = body
            .strip_prefix(TABLE_ENTRIES_DECLARATION)
            .filter(|grammar| table_entry_grammar(grammar));
        let root = lines.get(index + 1).and_then(|line| node_line(line));
        let preceded = index.checked_sub(1).is_some_and(|previous| {
            lines[previous]
                .trim_start_matches('\t')
                .starts_with(TABLE_ENTRIES_DECLARATION)
        });
        if let Some(grammar) =
            grammar.filter(|_| root.is_some_and(|root| root.depth == indent.len()) && !preceded)
        {
            let mut end = index + 2;
            let prefix = format!("{indent}\t");
            while lines.get(end).is_some_and(|line| line.starts_with(&prefix)) {
                end += 1;
            }
            let mut restored = lines[index + 1..end].to_vec();
            let (mut changed, mut valid) = (0, true);
            for offset in 0..restored.len() {
                if let Some((_, own_grammar)) = table_entry_intro(&restored[offset]) {
                    if own_grammar.is_some()
                        || table_entries_block(&restored, offset, grammar).is_none()
                    {
                        valid = false;
                        break;
                    }
                    restored[offset].push_str(" Entries: ");
                    restored[offset].push_str(grammar);
                    changed += 1;
                }
            }
            if valid && changed >= 2 {
                output.extend(restored);
                index = end;
                continue;
            }
        }
        output.push(line.clone());
        index += 1;
    }
    output
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum PublicTableKind {
    Single,
    Multi,
}

fn table_row_prefix(line: &str, kind: PublicTableKind) -> bool {
    let Some((first, _)) = line.split_once(" | ") else {
        return false;
    };
    match kind {
        PublicTableKind::Multi => unsigned_digits(first),
        PublicTableKind::Single => first
            .split_once(" / ")
            .is_some_and(|(index, row)| unsigned_digits(index) && unsigned_digits(row)),
    }
}

fn public_table_lines(lines: &[String]) -> HashMap<usize, PublicTableKind> {
    let mut result = HashMap::new();
    for (index, line) in lines.iter().enumerate() {
        let body = line.trim_start_matches('\t');
        let indent = &line[..line.len() - body.len()];
        let (kind, heading_count) = if body.starts_with("Rows: ") {
            (PublicTableKind::Multi, 3)
        } else if body.starts_with("Records: each row contains its cell;") {
            (PublicTableKind::Single, 2)
        } else {
            continue;
        };
        let start = index + heading_count;
        let mut end = start;
        while lines
            .get(end)
            .and_then(|line| line.strip_prefix(indent))
            .is_some_and(|line| table_row_prefix(line, kind))
        {
            end += 1;
        }
        if end - start < 3 {
            continue;
        }
        let block = &lines[index..end];
        let expanded = match kind {
            PublicTableKind::Multi => expand_multi_column_records(block),
            PublicTableKind::Single => expand_row_records(block),
        };
        if expanded != block {
            result.extend((start..end).map(|position| (position, kind)));
        }
    }
    result
}

fn public_table_children(column: &str, offset: usize) -> Vec<(usize, &str)> {
    let mut output = Vec::new();
    let mut start = 0;
    for position in unquoted_positions(column) {
        if position < start {
            continue;
        }
        let tail = &column[position..];
        let separator = [" > ", " ; ", "; "]
            .into_iter()
            .find(|separator| tail.starts_with(separator));
        if let Some(separator) = separator {
            output.push((offset + start, &column[start..position]));
            start = position + separator.len();
        }
    }
    output.push((offset + start, &column[start..]));
    output
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct NumberedEntry<'a> {
    pub(super) reference: &'a str,
    pub(super) line: usize,
    pub(super) start: usize,
    pub(super) body: &'a str,
    pub(super) heading: &'a str,
    pub(super) role_explicit: bool,
}

impl NumberedEntry<'_> {
    pub(super) fn expanded(&self) -> std::borrow::Cow<'_, str> {
        if self.heading.is_empty() {
            return std::borrow::Cow::Borrowed(self.body);
        }
        std::borrow::Cow::Owned(format!(
            "{} {}{}",
            self.reference,
            self.heading,
            &self.body[self.reference.len()..]
        ))
    }
}

/// Find each displayed entry using the current public table, heading and chain grammar.
/// Offsets are UTF-8 byte positions, matching the caller's Rust string slices.
pub(super) fn numbered_entries(lines: &[String]) -> Vec<NumberedEntry<'_>> {
    let tables = public_table_lines(lines);
    let mut entries = Vec::new();
    let mut heading = None;
    for (index, line) in lines.iter().enumerate() {
        if let Some(found) = leaf_header(line) {
            heading = Some(found);
            continue;
        }
        let body = line.trim_start_matches('\t');
        let indent = line.len() - body.len();
        let mut parts = Vec::new();
        if let Some(kind) = tables.get(&index) {
            let skip = if *kind == PublicTableKind::Multi {
                2
            } else {
                3
            };
            let mut offset = indent;
            for (column, value) in body.split(" | ").enumerate() {
                if column >= skip {
                    parts.extend(
                        public_table_children(value, offset)
                            .into_iter()
                            .enumerate()
                            .map(|(part, (start, body))| {
                                (start, body, "", *kind != PublicTableKind::Multi || part > 0)
                            }),
                    );
                }
                offset += value.len() + " | ".len();
            }
        } else if let Some(node) = node_line(line) {
            if let Some((_, title)) = heading.filter(|(depth, _)| depth + 1 == node.depth) {
                parts.push((indent, body, title, false));
            } else {
                heading = None;
                let mut offset = indent;
                for member in chain_members(body) {
                    parts.push((offset, member, "", true));
                    offset += member.len() + " > ".len();
                }
            }
        } else {
            heading = None;
        }
        for (start, part, heading, role_explicit) in parts {
            let Some((reference, body)) = part.split_once(' ') else {
                continue;
            };
            if unsigned_digits(reference)
                && !body.is_empty()
                && !body.starts_with('|')
                && !body.contains('\n')
            {
                entries.push(NumberedEntry {
                    reference,
                    line: index,
                    start,
                    body: part,
                    heading,
                    role_explicit,
                });
            }
        }
    }
    entries
}

pub(super) fn unique_numbered_entries(lines: &[String]) -> HashMap<&str, NumberedEntry<'_>> {
    let mut entries = HashMap::new();
    let mut duplicates = HashSet::new();
    for entry in numbered_entries(lines) {
        if entries.insert(entry.reference, entry).is_some() {
            duplicates.insert(entry.reference);
        }
    }
    for reference in duplicates {
        entries.remove(reference);
    }
    entries
}

pub(super) fn pack_table_entries(
    frame: &LayoutFrame<'_>,
    layouts: &[TableLayoutEvidence],
) -> Option<TableEntriesLayout> {
    let [root] = frame.roots else { return None };
    if frame.mode != LayoutMode::Compact
        || matches!(root.role.as_str(), "menu" | "menuBar")
        || layouts.len() < 2
    {
        return None;
    }
    let root_ref = root.reference.as_str();
    let entries = unique_numbered_entries(&frame.lines);
    let root_position = entries.get(root_ref)?.line;
    let root_entry =
        node_line(&frame.lines[root_position]).filter(|entry| entry.reference == root_ref)?;
    let mut root_end = root_position + 1;
    let child_indent = "\t".repeat(root_entry.depth + 1);
    while frame
        .lines
        .get(root_end)
        .is_some_and(|line| line.starts_with(&child_indent))
    {
        root_end += 1;
    }
    let mut records = HashMap::new();
    let mut counts = HashMap::new();
    for record in frame.rendered_nodes {
        records.insert(record.reference.as_str(), record);
        *counts.entry(record.reference.as_str()).or_insert(0usize) += 1;
    }
    let mut nodes = HashMap::new();
    let mut pending = vec![(root, None)];
    while let Some((node, parent)) = pending.pop() {
        let reference = node.reference.as_str();
        if nodes.insert(reference, (node, parent)).is_some() {
            return None;
        }
        pending.extend(node.children.iter().map(|child| (child, Some(reference))));
    }
    let mut candidates = Vec::new();
    for evidence in layouts {
        let table = evidence.table.as_str();
        let position = entries.get(table)?.line;
        let record = records.get(table)?;
        if counts.get(table) != Some(&1)
            || record.layout != evidence.layout
            || !(root_position..root_end).contains(&position)
        {
            return None;
        }
        let mut ancestor = table;
        while ancestor != root_ref {
            let (node, parent) = nodes.get(ancestor)?;
            let record = records.get(ancestor)?;
            if counts.get(ancestor) != Some(&1)
                || record.parent.as_deref() != *parent
                || matches!(
                    node.role.as_str(),
                    "application" | "window" | "sheet" | "menu" | "menuBar"
                )
            {
                return None;
            }
            ancestor = (*parent)?;
        }
        let mut matches = (position + 1..root_end).filter(|&index| {
            frame.lines[index] == evidence.introduction
                && frame.lines.get(index + 1) == Some(&evidence.heading)
                && table_entries_block(&frame.lines, index, TABLE_ENTRIES_GRAMMAR).is_some_and(
                    |rows| {
                        rows.into_iter()
                            .eq(evidence.rows.iter().map(String::as_str))
                    },
                )
        });
        let index = matches.next()?;
        if matches.next().is_some() {
            return None;
        }
        let (indent, grammar) = table_entry_intro(&evidence.introduction)?;
        let grammar = grammar.filter(|grammar| table_entry_grammar(grammar))?;
        let table_indent = frame.lines[position]
            .bytes()
            .take_while(|&byte| byte == b'\t')
            .count();
        if indent.len() != table_indent + 1
            || frame.lines[position + 1..index]
                .iter()
                .any(|line| !line.starts_with(indent))
        {
            return None;
        }
        candidates.push((index, evidence, grammar));
    }
    let grammar = candidates.first()?.2;
    let mut used = HashSet::new();
    if candidates
        .iter()
        .any(|&(index, _, value)| value != grammar || !used.insert(index))
    {
        return None;
    }
    let declaration = format!(
        "{}{TABLE_ENTRIES_DECLARATION}{grammar}",
        "\t".repeat(root_entry.depth)
    );
    let mut lines = frame.lines.clone();
    let mut replacements = Vec::new();
    let suffix = format!(" Entries: {grammar}");
    for (index, evidence, _) in candidates {
        let replacement = evidence.introduction.strip_suffix(&suffix)?.to_owned();
        lines[index] = replacement.clone();
        replacements.push(TableEntriesReplacement {
            table: evidence.table.clone(),
            root: root_ref.to_owned(),
            layout: evidence.layout.clone(),
            rows: evidence.rows.clone(),
            original: evidence.introduction.clone(),
            replacement,
            declaration: declaration.clone(),
        });
    }
    lines.insert(root_position, declaration);
    (enough_table_saving(&frame.lines, &lines) && expand_table_entries(&lines) == frame.lines)
        .then_some(TableEntriesLayout {
            lines,
            replacements,
        })
}

type TableValue = super::value::Value;

#[derive(Debug, PartialEq, Eq)]
pub(super) struct TableCellEvidence {
    pub(super) reference: String,
    pub(super) row: u64,
    pub(super) column: u64,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct TableRowEvidence {
    pub(super) reference: String,
    pub(super) cells: Vec<TableCellEvidence>,
}

#[derive(Debug, PartialEq)]
pub(super) struct PublishedTableColumn {
    pub(super) reference: String,
    pub(super) source: String,
    pub(super) table: String,
    pub(super) column: u64,
    pub(super) targets: Vec<String>,
    pub(super) scope: [String; 3],
    pub(super) property: String,
    pub(super) relation: TableValue,
}

impl PublishedTableColumn {
    /// Keep the detached native relation with the same field order as the observation model.
    pub(super) fn to_record(&self) -> TableValue {
        let as_text = |value: &String| TableValue::Text(value.clone());
        let mut relation = Vec::new();
        for name in [
            "kind",
            "targets",
            "view",
            "positions",
            "total",
            "enumeration",
            "evidence",
            "offset",
            "inspected_count",
            "unavailable_positions",
        ] {
            let value = self
                .relation
                .get(name)
                .cloned()
                .unwrap_or_else(|| match name {
                    "enumeration" => TableValue::Text("enumerated".into()),
                    "evidence" => TableValue::Text("reported".into()),
                    "unavailable_positions" => TableValue::Seq(Vec::new()),
                    _ => TableValue::Null,
                });
            relation.push((name.to_owned(), value));
        }
        TableValue::Map(vec![
            ("ref".into(), as_text(&self.reference)),
            ("source".into(), as_text(&self.source)),
            ("table".into(), as_text(&self.table)),
            (
                "column".into(),
                i64::try_from(self.column).map_or_else(
                    |_| TableValue::BigInt(self.column.to_string()),
                    TableValue::Int,
                ),
            ),
            (
                "targets".into(),
                TableValue::Seq(self.targets.iter().map(as_text).collect()),
            ),
            (
                "scope".into(),
                TableValue::Seq(self.scope.iter().map(as_text).collect()),
            ),
            ("property".into(), as_text(&self.property)),
            ("relation".into(), TableValue::Map(relation)),
        ])
    }
}

#[derive(Debug, PartialEq)]
pub(super) struct NativeTableGrid {
    pub(super) reference: String,
    pub(super) scope: [String; 3],
    pub(super) rows: Vec<TableRowEvidence>,
    pub(super) columns: Vec<PublishedTableColumn>,
}

struct TablePublic<'a> {
    nodes: HashMap<&'a str, (&'a LayoutNode, Option<&'a str>)>,
    occurrences: HashMap<&'a str, usize>,
    records: HashMap<&'a str, &'a RenderedNode>,
    counts: HashMap<&'a str, usize>,
    displayed: HashMap<&'a str, Vec<(usize, &'a str)>>,
}

impl<'a> TablePublic<'a> {
    fn new(root: &'a LayoutNode, records: &'a [RenderedNode], lines: &'a [String]) -> Self {
        let mut result = Self {
            nodes: HashMap::new(),
            occurrences: HashMap::new(),
            records: HashMap::new(),
            counts: HashMap::new(),
            displayed: HashMap::new(),
        };
        let mut pending = vec![(root, None)];
        while let Some((node, parent)) = pending.pop() {
            let reference = node.reference.as_str();
            result.nodes.insert(reference, (node, parent));
            *result.occurrences.entry(reference).or_insert(0) += 1;
            pending.extend(
                node.children
                    .iter()
                    .rev()
                    .map(|child| (child, Some(reference))),
            );
        }
        for record in records {
            result.records.insert(record.reference.as_str(), record);
            *result.counts.entry(record.reference.as_str()).or_insert(0) += 1;
        }
        for (index, line) in lines.iter().enumerate() {
            if let Some(entry) = node_line(line) {
                result
                    .displayed
                    .entry(entry.reference)
                    .or_default()
                    .push((index, line));
            }
        }
        result
    }

    fn displayed(
        &self,
        reference: &str,
        parent: Option<&str>,
        layout: &[(String, String)],
    ) -> Option<(usize, &'a str)> {
        let record = self.records.get(reference)?;
        let (_, display_parent) = self.nodes.get(reference)?;
        let [entry] = self.displayed.get(reference)?.as_slice() else {
            return None;
        };
        if self.counts.get(reference) != Some(&1)
            || self.occurrences.get(reference) != Some(&1)
            || record.parent.as_deref() != parent
            || *display_parent != parent
            || record.layout != layout
            || record.partial
            || !record.hints.is_empty()
            || !record.related.is_empty()
            || record.text.contains('\n')
            || entry.1.trim_start_matches('\t') != record.text.trim_start_matches('\t')
        {
            return None;
        }
        Some(*entry)
    }
}

fn table_coordinates_present(line: &str, row: u64, column: u64) -> bool {
    let coordinates = format!("Row: {row}, Column: {column}");
    line.match_indices(&coordinates).any(|(start, matched)| {
        line[..start].ends_with(' ')
            && (start + matched.len() == line.len()
                || line[start + matched.len()..].starts_with(", "))
    })
}

/// Verify table evidence against the actual finalized text. `restored_lines`
/// is the final packed text after Entries, input, role and field-prefix inverses.
/// Failure of a preceding inverse leaves the caller without publication evidence.
pub(super) fn published_table_columns<'a>(
    frame: &LayoutFrame<'_>,
    evidence: &TableEvidence,
    grids: &'a [NativeTableGrid],
    restored_lines: &[String],
) -> Vec<&'a PublishedTableColumn> {
    let [root] = frame.roots else {
        return Vec::new();
    };
    if frame.mode != LayoutMode::Compact
        || grids.is_empty()
        || matches!(root.role.as_str(), "menu" | "menuBar" | "menuBarItem")
    {
        return Vec::new();
    }
    let expanded = expand_leaf_headers(&expand_multi_column_records(restored_lines));
    if expanded == expand_leaf_headers(restored_lines) {
        return Vec::new();
    }
    let public = TablePublic::new(root, frame.rendered_nodes, &expanded);
    let originals: HashSet<_> = restored_lines.iter().map(String::as_str).collect();
    let mut published = Vec::new();
    'tables: for grid in grids {
        let reference = grid.reference.as_str();
        let (Some(&(table, _)), Some(&record)) =
            (public.nodes.get(reference), public.records.get(reference))
        else {
            continue;
        };
        if !matches!(table.role.as_str(), "table" | "outline") || grid.scope[2] != root.reference {
            continue;
        }
        let Some((mut previous, line)) =
            public.displayed(reference, record.parent.as_deref(), &record.layout)
        else {
            continue;
        };
        let indent = line.bytes().take_while(|&byte| byte == b'\t').count();
        let row_refs = table
            .children
            .iter()
            .filter(|child| child.role == "row")
            .map(|child| child.reference.as_str());
        if !row_refs.eq(grid.rows.iter().map(|row| row.reference.as_str())) {
            continue;
        }
        let mut packed_rows = 0;
        for row in &grid.rows {
            let row_ref = row.reference.as_str();
            let Some((row_index, row_line)) =
                public.displayed(row_ref, Some(reference), &record.layout)
            else {
                continue 'tables;
            };
            let (node, _) = public.nodes[row_ref];
            if node.role != "row"
                || row_index <= previous
                || row_line.bytes().take_while(|&byte| byte == b'\t').count() != indent + 1
                || !node
                    .children
                    .iter()
                    .map(|child| child.reference.as_str())
                    .eq(row.cells.iter().map(|cell| cell.reference.as_str()))
            {
                continue 'tables;
            }
            packed_rows += usize::from(!originals.contains(row_line));
            previous = row_index;
            for cell in &row.cells {
                let cell_ref = cell.reference.as_str();
                let Some((cell_index, cell_line)) =
                    public.displayed(cell_ref, Some(row_ref), &record.layout)
                else {
                    continue 'tables;
                };
                let (child, _) = public.nodes[cell_ref];
                let Some(facts) = evidence.facts.get(cell_ref) else {
                    continue 'tables;
                };
                if !matches!(child.role.as_str(), "cell" | "columnHeader")
                    || cell_index <= previous
                    || cell_line.bytes().take_while(|&byte| byte == b'\t').count() != indent + 2
                    || facts.row != Some(cell.row)
                    || facts.column != Some(cell.column)
                    || !table_coordinates_present(cell_line, cell.row, cell.column)
                {
                    continue 'tables;
                }
                previous = cell_index;
            }
        }
        if packed_rows < 3 {
            continue;
        }
        for column in &grid.columns {
            let column_ref = column.reference.as_str();
            let Some((_, line)) = public.displayed(column_ref, Some(reference), &record.layout)
            else {
                continue;
            };
            if public.nodes[column_ref].0.role == "column"
                && line.bytes().take_while(|&byte| byte == b'\t').count() == indent + 1
            {
                published.push(column);
            }
        }
    }
    published
}

const ROLE_DECLARATION: &str = "Role labels (exact display labels): ";

#[derive(Debug)]
pub(super) struct NativeRoleSource {
    pub(super) native: String,
    pub(super) scope: [String; 3],
    pub(super) native_role: String,
    pub(super) display_label: String,
    pub(super) provider_role_detail: Option<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct RoleLabelReplacement {
    pub(super) reference: String,
    pub(super) native: String,
    pub(super) scope: [String; 3],
    pub(super) native_role: String,
    pub(super) display_label: String,
    pub(super) alias: String,
    pub(super) provider_role_detail: Option<String>,
    pub(super) declaration: String,
    pub(super) replacement: String,
}

fn role_alias(source: &NativeRoleSource) -> Option<&'static str> {
    Some(match source.native_role.as_str() {
        "group" if source.provider_role_detail.as_deref() == Some("AXCodeStyleGroup") => "code",
        "static_text" => "text",
        "window" => "window",
        "dialog" => "dialog",
        "alert" => "alert",
        "sheet" => "sheet",
        "popover" => "popover",
        "split_group" => "split group",
        "scroll_area" => "scroll area",
        "toolbar" => "toolbar",
        "separator" => "separator",
        "button" => "button",
        "toggle_button" => "toggle button",
        "checkbox" => "checkbox",
        "radio_button" => "radio button",
        "switch" => "switch",
        "menu_button" => "menu button",
        "popup_button" => "popup button",
        "combo_box" => "combo box",
        "text_field" => "text field",
        "text_area" => "text area",
        "slider" => "slider",
        "spinner" => "spinner",
        "scrollbar" => "scrollbar",
        "progress_indicator" => "progress indicator",
        "heading" => "heading",
        "image" => "image",
        "link" => "link",
        "document" => "document",
        "web_area" => "web area",
        "list" => "list",
        "list_item" => "list item",
        "tree" => "tree",
        "tree_item" => "tree item",
        "row" => "row",
        "column" => "column",
        "cell" => "cell",
        "table" => "table",
        "grid" => "grid",
        "row_header" => "row header",
        "column_header" => "column header",
        "tab_group" => "tab group",
        "tab" => "tab",
        "header_item" => "header item",
        "value_indicator" => "value indicator",
        "browser" => "browser",
        "ruler" => "ruler",
        "handle" => "handle",
        "color_well" => "color well",
        "date_field" => "date field",
        "time_field" => "time field",
        "disclosure_triangle" => "disclosure triangle",
        _ => return None,
    })
}

fn role_overlap(left: &str, right: &str) -> bool {
    left == right
        || left
            .strip_prefix(right)
            .is_some_and(|suffix| suffix.starts_with(' '))
        || right
            .strip_prefix(left)
            .is_some_and(|suffix| suffix.starts_with(' '))
}

fn role_starts(body: &str, prefix: &str) -> bool {
    body.strip_prefix(prefix).is_some_and(|suffix| {
        suffix.is_empty() || suffix.starts_with(' ') || suffix.starts_with(", ")
    })
}

fn excluded_role(role: &str) -> bool {
    is_menu(role) || role == "menuItem"
}

struct RoleSlot<'a> {
    entry: NumberedEntry<'a>,
    source: &'a NativeRoleSource,
    alias: &'static str,
}

fn role_slots<'a>(frame: &'a LayoutFrame<'_>) -> Option<(usize, Vec<RoleSlot<'a>>)> {
    let [root] = frame.roots else {
        return None;
    };
    if excluded_role(&root.role) {
        return None;
    }
    let mut nodes = HashMap::new();
    let mut parents = HashMap::new();
    let mut occurrences = HashMap::new();
    let mut pending = vec![(root, None)];
    while let Some((node, parent)) = pending.pop() {
        let reference = node.reference.as_str();
        nodes.insert(reference, node);
        parents.insert(reference, parent);
        *occurrences.entry(reference).or_insert(0) += 1;
        pending.extend(node.children.iter().map(|child| (child, Some(reference))));
    }
    let mut records = HashMap::new();
    let mut counts = HashMap::new();
    for record in frame.rendered_nodes {
        records.insert(record.reference.as_str(), record);
        *counts.entry(record.reference.as_str()).or_insert(0) += 1;
    }
    let entries = unique_numbered_entries(&frame.lines);
    let root_entry = entries.get(root.reference.as_str())?;
    if counts.get(root.reference.as_str()) != Some(&1) {
        return None;
    }
    let sources: Vec<_> = frame
        .role_sources
        .iter()
        .filter(|(reference, _)| nodes.contains_key(reference.as_str()))
        .collect();
    let scopes: HashSet<_> = sources
        .iter()
        .map(|(_, source)| &source.scope)
        .filter(|scope| scope[2] == root.reference)
        .collect();
    if scopes.len() != 1 {
        return None;
    }
    let mut source_counts = HashMap::new();
    let mut conflicts: HashMap<&str, HashSet<Option<&str>>> = HashMap::new();
    for (_, source) in &sources {
        *source_counts.entry(source.native.as_str()).or_insert(0) += 1;
        conflicts
            .entry(source.display_label.as_str())
            .or_default()
            .insert(role_alias(source));
    }
    let mut output = Vec::new();
    for (reference, source) in sources {
        let reference = reference.as_str();
        let (Some(&record), Some(&entry), Some(prefix), Some(span), Some(alias)) = (
            records.get(reference),
            entries.get(reference),
            frame.node_prefixes.get(reference),
            frame.node_line_ranges.get(reference),
            role_alias(source),
        ) else {
            continue;
        };
        let node = nodes[reference];
        let label = &source.display_label;
        if !entry.heading.is_empty()
            || !entry.role_explicit
            || prefix.is_empty()
            || span.is_empty()
            || excluded_role(&node.role)
            || occurrences[reference] != 1
            || counts[reference] != 1
            || source_counts[source.native.as_str()] != 1
            || source.scope[2] != root.reference
            || record.parent.as_deref() != parents[reference]
            || node.role_description.as_deref() != Some(label)
            || !plain_text(label)
            || super::render::field(label) != *label
            || conflicts[label.as_str()].len() != 1
            || prefix
                .strip_prefix(label)
                .is_none_or(|tail| !tail.is_empty() && !tail.starts_with(" ("))
        {
            continue;
        }
        let original = record
            .text
            .split('\n')
            .next()
            .unwrap_or("")
            .trim_start_matches('\t');
        let prefix = format!("{reference} {prefix}");
        if role_starts(original, &prefix) && role_starts(entry.body, &prefix) {
            output.push(RoleSlot {
                entry,
                source,
                alias,
            });
        }
    }
    Some((root_entry.line, output))
}

fn role_declaration<'a>(mapping: impl IntoIterator<Item = (&'a str, &'a str)>) -> String {
    let pairs: Vec<_> = mapping
        .into_iter()
        .map(|(alias, label)| {
            format!(
                "{}: {}",
                super::render::quoted(alias),
                super::render::quoted(label)
            )
        })
        .collect();
    format!("{ROLE_DECLARATION}{{{}}}", pairs.join(", "))
}

fn skip_json_space(text: &str, index: usize) -> usize {
    index
        + text.as_bytes()[index..]
            .iter()
            .take_while(|&&value| matches!(value, b' ' | b'\n' | b'\r' | b'\t'))
            .count()
}

fn parse_role_declaration(text: &str) -> Option<Vec<(String, String)>> {
    let begin = skip_json_space(text, 0);
    if text.as_bytes().get(begin) != Some(&b'{') {
        return None;
    }
    let mut offset = begin + 1;
    let mut labels: Vec<(String, String)> = Vec::new();
    loop {
        offset = skip_json_space(text, offset);
        if text.as_bytes().get(offset) != Some(&b'"') {
            return None;
        }
        let (alias, end) = scalar(text, offset)?;
        offset = skip_json_space(text, end);
        if text.as_bytes().get(offset) != Some(&b':') {
            return None;
        }
        offset = skip_json_space(text, offset + 1);
        if text.as_bytes().get(offset) != Some(&b'"') {
            return None;
        }
        let (original, end) = scalar(text, offset)?;
        if !alias
            .split(' ')
            .all(|part| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_lowercase()))
            || !plain_text(&original)
            || labels
                .iter()
                .any(|(previous, value)| role_overlap(previous, &alias) || value == &original)
        {
            return None;
        }
        labels.push((alias, original));
        offset = skip_json_space(text, end);
        match text.as_bytes().get(offset) {
            Some(b',') => offset += 1,
            Some(b'}') if skip_json_space(text, offset + 1) == text.len() => return Some(labels),
            _ => return None,
        }
    }
}

fn expand_role_labels(lines: &[String]) -> Option<Vec<String>> {
    let mut by_line: HashMap<usize, Vec<NumberedEntry<'_>>> = HashMap::new();
    for entry in numbered_entries(lines) {
        if entry.role_explicit && entry.heading.is_empty() {
            by_line.entry(entry.line).or_default().push(entry);
        }
    }
    let mut output = Vec::new();
    let mut mapping = Vec::new();
    let mut indent = 0;
    let mut started = false;
    for (index, line) in lines.iter().enumerate() {
        let line_depth = depth(line);
        let stripped = &line[line_depth..];
        if let Some(raw) = stripped.strip_prefix(ROLE_DECLARATION) {
            if !mapping.is_empty() && !started {
                return None;
            }
            mapping = parse_role_declaration(raw)?;
            indent = line_depth;
            started = false;
            continue;
        }
        if !mapping.is_empty() {
            if !started {
                if stripped.starts_with(IDENTIFIER_DECLARATION)
                    || stripped.starts_with("Caption prefix (prepend to ")
                    || stripped.starts_with("URL prefix (prepend to ")
                {
                    output.push(line.clone());
                    continue;
                }
                if line_depth != indent || node_line(line).is_none() {
                    return None;
                }
                started = true;
            } else if !stripped.is_empty() && line_depth <= indent {
                mapping.clear();
                started = false;
            }
        }
        let mut rendered = line.clone();
        if !mapping.is_empty() {
            let mut edits = Vec::new();
            for entry in by_line.get(&index).into_iter().flatten() {
                let start = entry.reference.len() + 1;
                let body = &entry.body[start..];
                let mut matches = mapping.iter().filter(|(alias, _)| role_starts(body, alias));
                if let Some((alias, original)) = matches.next() {
                    if matches.next().is_some() {
                        return None;
                    }
                    edits.push((
                        entry.start + start,
                        entry.start + start + alias.len(),
                        original,
                    ));
                }
            }
            edits.sort_unstable_by(|left, right| right.cmp(left));
            for (start, end, original) in edits {
                rendered.replace_range(start..end, original);
            }
        }
        output.push(rendered);
    }
    (mapping.is_empty() || started).then_some(output)
}

fn group_role_labels(frame: &LayoutFrame<'_>) -> Option<(Vec<String>, Vec<RoleLabelReplacement>)> {
    if frame.role_sources.is_empty() {
        return None;
    }
    let (root_index, slots) = role_slots(frame)?;
    let root = &frame.lines[root_index];
    let indent = &root[..depth(root)];
    let public_roles = frame.node_prefixes.values().map(|prefix| {
        prefix
            .split_once(" (")
            .map_or(prefix.as_str(), |(role, _)| role)
    });
    let mut groups: HashMap<(&str, &str), Vec<RoleSlot<'_>>> = HashMap::new();
    for slot in slots {
        let label = slot.source.display_label.as_str();
        if slot.alias != label
            && !public_roles
                .clone()
                .any(|role| role_overlap(slot.alias, role))
        {
            groups.entry((slot.alias, label)).or_default().push(slot);
        }
    }
    let mut candidates: Vec<_> = groups
        .into_iter()
        .filter_map(|((alias, label), members)| {
            let saving = members.len() as i64 * (label.len() as i64 - alias.len() as i64)
                - (indent.len() + role_declaration([(alias, label)]).len() + 1) as i64;
            (saving >= 8).then_some((saving, alias, label, members))
        })
        .collect();
    candidates.sort_unstable_by(|left, right| {
        right
            .0
            .cmp(&left.0)
            .then(left.1.cmp(right.1))
            .then(left.2.cmp(right.2))
    });
    let mut mapping = std::collections::BTreeMap::<&str, &str>::new();
    let mut selected = Vec::new();
    for (_, alias, label, members) in candidates {
        if mapping
            .iter()
            .any(|(&chosen, &value)| value == label || role_overlap(alias, chosen))
        {
            continue;
        }
        mapping.insert(alias, label);
        selected.extend(members);
    }
    if selected.is_empty() {
        return None;
    }
    let declaration = role_declaration(mapping);
    let mut edits: LineEdits = HashMap::new();
    let mut replacements = Vec::new();
    for slot in selected {
        let source = slot.source;
        let start = slot.entry.start + slot.entry.reference.len() + 1;
        edits.entry(slot.entry.line).or_default().push((
            start,
            start + source.display_label.len(),
            slot.alias.into(),
        ));
        replacements.push(RoleLabelReplacement {
            reference: slot.entry.reference.into(),
            native: source.native.clone(),
            scope: source.scope.clone(),
            native_role: source.native_role.clone(),
            display_label: source.display_label.clone(),
            alias: slot.alias.into(),
            provider_role_detail: source.provider_role_detail.clone(),
            declaration: declaration.clone(),
            replacement: slot.alias.into(),
        });
    }
    for changes in edits.values_mut() {
        changes.sort_unstable();
        if changes.windows(2).any(|pair| pair[0].1 > pair[1].0) {
            return None;
        }
    }
    let mut output = replace_line_spans(&frame.lines, edits)?;
    output.insert(root_index, format!("{indent}{declaration}"));
    let size = |lines: &[String]| lines.iter().map(|line| line.len() + 1).sum::<usize>();
    (size(&frame.lines).saturating_sub(size(&output)) >= 8
        && expand_role_labels(&output).as_deref() == Some(frame.lines.as_slice()))
    .then_some((output, replacements))
}

const UNKNOWN_INPUT_FIELDS: &str = "Click: unknown, Input: unknown, ";

#[derive(Debug, PartialEq, Eq)]
pub(super) struct InputFieldsReplacement {
    pub(super) reference: String,
    pub(super) table: String,
    pub(super) root: String,
    pub(super) layout: Vec<(String, String)>,
    pub(super) declaration: String,
    pub(super) original: String,
    pub(super) replacement: String,
}

fn value_position(entry: &NumberedEntry<'_>) -> Option<usize> {
    let positions = space_label_positions(entry.body, "Value: ");
    let [position] = positions.as_slice() else {
        return None;
    };
    Some(*position)
}

fn input_declaration(line: &str) -> Option<(usize, Vec<&str>)> {
    let indent = depth(line);
    let refs = line[indent..]
        .strip_prefix("Shared fields [elements ")?
        .strip_suffix("]: Click: unknown; Input: unknown")?
        .split(", ")
        .collect::<Vec<_>>();
    refs.iter()
        .all(|reference| {
            !reference.is_empty() && reference.bytes().all(|value| value.is_ascii_digit())
        })
        .then_some((indent, refs))
}

fn table_end(lines: &[String], line: usize, indent: usize) -> usize {
    let prefix = "\t".repeat(indent + 1);
    line + 1
        + lines[line + 1..]
            .iter()
            .take_while(|line| line.starts_with(&prefix))
            .count()
}

fn expand_input_fields(lines: &[String]) -> Option<Vec<String>> {
    let entries = unique_numbered_entries(lines);
    let mut edits: LineEdits = HashMap::new();
    let mut removed = HashSet::new();
    let mut claimed = HashSet::new();
    for (index, line) in lines.iter().enumerate().skip(1) {
        let Some((indent, refs)) = input_declaration(line) else {
            continue;
        };
        let Some(table) = node_line(&lines[index - 1]) else {
            continue;
        };
        if indent != table.depth + 1
            || refs.len() < 3
            || refs.iter().collect::<HashSet<_>>().len() != refs.len()
            || refs.iter().any(|reference| claimed.contains(reference))
        {
            continue;
        }
        let end = table_end(lines, index, table.depth);
        let members: Option<Vec<_>> = refs
            .iter()
            .map(|reference| {
                let entry = entries
                    .get(reference)
                    .filter(|entry| index < entry.line && entry.line < end)?;
                let position = value_position(entry)?;
                if entry.body[..position].ends_with(", ")
                    || entry.body[..position].contains(UNKNOWN_INPUT_FIELDS)
                {
                    return None;
                }
                Some((entry.line, entry.start, position))
            })
            .collect();
        let Some(members) = members else {
            continue;
        };
        if !members
            .windows(2)
            .all(|pair| (pair[0].0, pair[0].1) <= (pair[1].0, pair[1].1))
        {
            continue;
        }
        for (line, start, position) in members {
            edits.entry(line).or_default().push((
                start + position,
                start + position,
                UNKNOWN_INPUT_FIELDS.to_owned(),
            ));
        }
        claimed.extend(refs);
        removed.insert(index);
    }
    Some(
        replace_line_spans(lines, edits)?
            .into_iter()
            .enumerate()
            .filter_map(|(index, line)| (!removed.contains(&index)).then_some(line))
            .collect(),
    )
}

fn group_input_fields(
    frame: &LayoutFrame<'_>,
) -> Option<(Vec<String>, Vec<InputFieldsReplacement>)> {
    let [root] = frame.roots else {
        return None;
    };
    if matches!(root.role.as_str(), "menu" | "menuBar") {
        return None;
    }
    let mut nodes = HashMap::new();
    let mut parents = HashMap::new();
    let mut tables = HashMap::new();
    let mut order = Vec::new();
    let mut pending = vec![(root, None, None, true)];
    while let Some((node, parent, mut table, mut in_root)) = pending.pop() {
        let reference = node.reference.as_str();
        if nodes.insert(reference, node).is_some() {
            return None;
        }
        parents.insert(reference, parent);
        let scope = is_scope(&node.role) || node.role == "application";
        if parent.is_some() && scope {
            in_root = false;
        }
        if in_root && matches!(node.role.as_str(), "table" | "outline") {
            table = Some(reference);
        } else if !in_root || scope {
            table = None;
        }
        tables.insert(reference, table);
        order.push(reference);
        pending.extend(
            node.children
                .iter()
                .rev()
                .map(|child| (child, Some(reference), table, in_root)),
        );
    }
    let mut records = HashMap::new();
    let mut counts = HashMap::new();
    for record in frame.rendered_nodes {
        records.insert(record.reference.as_str(), record);
        *counts.entry(record.reference.as_str()).or_insert(0) += 1;
    }
    let entries = unique_numbered_entries(&frame.lines);
    let original_role_lines = expand_role_labels(&frame.lines)?;
    let original_roles = unique_numbered_entries(&original_role_lines);
    let mut groups: Vec<(&str, Vec<(NumberedEntry<'_>, usize)>)> = Vec::new();
    for reference in order {
        let node = nodes[reference];
        let (Some(table), Some(&record), Some(&entry), Some(original), Some(prefix), Some(span)) = (
            tables[reference],
            records.get(reference),
            entries.get(reference),
            original_roles.get(reference),
            frame.node_prefixes.get(reference),
            frame.node_line_ranges.get(reference),
        ) else {
            continue;
        };
        if counts[reference] != 1
            || record.parent.as_deref() != parents[reference]
            || prefix.is_empty()
            || span.end.checked_sub(span.start) != Some(1)
            || record.partial
            || !record.hints.is_empty()
            || !record.related.is_empty()
            || record.text.contains('\n')
            || node.incomplete_children
            || !node.children.is_empty()
            || node.name.as_deref().is_some_and(|value| !value.is_empty())
            || !matches!(node.role.as_str(), "textField" | "textArea" | "comboBox")
            || node.enabled == Some(false)
            || node.blocked
            || !node.unknown_input_pair
        {
            continue;
        }
        let expected = format!("{reference} {prefix} {UNKNOWN_INPUT_FIELDS}Value: ");
        if !record.text.trim_start_matches('\t').starts_with(&expected)
            || !original.expanded().starts_with(&expected)
        {
            continue;
        }
        let Some(position) = value_position(&entry)
            .and_then(|position| position.checked_sub(UNKNOWN_INPUT_FIELDS.len()))
        else {
            continue;
        };
        if !entry.body[position..].starts_with(UNKNOWN_INPUT_FIELDS) {
            continue;
        }
        let (Some(_), Some(&table_record)) = (entries.get(table), records.get(table)) else {
            continue;
        };
        if counts[table] != 1
            || record.layout != table_record.layout
            || table_record.partial
            || nodes[table].incomplete_children
            || table_record.parent.as_deref() != parents[table]
        {
            continue;
        }
        let mut ancestor = reference;
        let mut valid = true;
        while ancestor != table {
            let parent = parents[ancestor];
            if records.get(ancestor).is_none_or(|record| {
                counts[ancestor] != 1
                    || record.parent.as_deref() != parent
                    || record.layout != table_record.layout
                    || record.partial
                    || nodes[ancestor].incomplete_children
            }) {
                valid = false;
                break;
            }
            let Some(parent) = parent else {
                valid = false;
                break;
            };
            ancestor = parent;
        }
        if !valid {
            continue;
        }
        if let Some((_, members)) = groups.iter_mut().find(|(key, _)| *key == table) {
            members.push((entry, position));
        } else {
            groups.push((table, vec![(entry, position)]));
        }
    }
    let mut edits: LineEdits = HashMap::new();
    let mut declarations = HashMap::new();
    let mut replacements = Vec::new();
    for (table, members) in groups {
        let table_entry = entries[table];
        let Some(matched) = node_line(&frame.lines[table_entry.line]) else {
            continue;
        };
        if members.len() < 3
            || matched.reference != table
            || !members
                .windows(2)
                .all(|pair| (pair[0].0.line, pair[0].0.start) <= (pair[1].0.line, pair[1].0.start))
        {
            continue;
        }
        let end = table_end(&frame.lines, table_entry.line, matched.depth);
        if members
            .iter()
            .any(|(entry, _)| entry.line <= table_entry.line || entry.line >= end)
        {
            continue;
        }
        let refs = members
            .iter()
            .map(|(entry, _)| entry.reference)
            .collect::<Vec<_>>();
        let indent = "\t".repeat(matched.depth + 1);
        let declaration = format!(
            "{indent}Shared fields [elements {}]: Click: unknown; Input: unknown",
            refs.join(", ")
        );
        if (UNKNOWN_INPUT_FIELDS.len() * members.len()).saturating_sub(declaration.len() + 1) < 8 {
            continue;
        }
        declarations.insert(table_entry.line + 1, declaration.clone());
        for (entry, position) in members {
            let end = position + UNKNOWN_INPUT_FIELDS.len();
            edits.entry(entry.line).or_default().push((
                entry.start + position,
                entry.start + end,
                String::new(),
            ));
            let replacement = [&entry.body[..position], &entry.body[end..]].concat();
            replacements.push(InputFieldsReplacement {
                reference: entry.reference.into(),
                table: table.into(),
                root: root.reference.clone(),
                layout: records[entry.reference].layout.clone(),
                declaration: declaration.clone(),
                original: entry.body.into(),
                replacement,
            });
        }
    }
    if replacements.is_empty() {
        return None;
    }
    let mut output = Vec::new();
    for (index, line) in replace_line_spans(&frame.lines, edits)?
        .into_iter()
        .enumerate()
    {
        output.extend(declarations.remove(&index));
        output.push(line);
    }
    (expand_input_fields(&output).as_deref() == Some(frame.lines.as_slice()))
        .then_some((output, replacements))
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct LocalFieldSuffixReplacement {
    pub(super) reference: String,
    pub(super) native: String,
    pub(super) scope: [String; 3],
    pub(super) value: String,
    pub(super) anchor_value: String,
    pub(super) prefix: String,
    pub(super) quoted: bool,
    pub(super) statement: String,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct SharedDescriptionReplacement {
    pub(super) reference: String,
    pub(super) native: String,
    pub(super) scope: [String; 3],
    pub(super) value: String,
    pub(super) quoted: bool,
    pub(super) root: String,
    pub(super) declaration: String,
    pub(super) original: String,
    pub(super) anchor: &'static str,
}

fn ordinary_entry(entry: &NumberedEntry<'_>, lines: &[String]) -> bool {
    entry.role_explicit
        && entry.heading.is_empty()
        && entry.body == lines[entry.line].trim_start_matches('\t')
}

struct DescriptionEvidence<'a> {
    frame: &'a LayoutFrame<'a>,
    root: &'a str,
    root_scope: &'a [String; 3],
    nodes: HashMap<&'a str, (&'a LayoutNode, Option<&'a str>)>,
    occurrences: HashMap<&'a str, usize>,
    records: HashMap<&'a str, &'a RenderedNode>,
    record_counts: HashMap<&'a str, usize>,
    sources: HashMap<&'a str, &'a NativeFieldSource>,
    source_counts: HashMap<&'a str, usize>,
    spellings: HashMap<&'a str, &'a RoleLabelReplacement>,
    spelling_counts: HashMap<&'a str, usize>,
    table_members: HashSet<&'a str>,
}

impl<'a> DescriptionEvidence<'a> {
    fn build(frame: &'a LayoutFrame<'a>, spellings: &'a [RoleLabelReplacement]) -> Option<Self> {
        let [root] = frame.roots else {
            return None;
        };
        if is_menu(&root.role) {
            return None;
        }
        let root_ref = root.reference.as_str();
        let mut sources = HashMap::new();
        let mut source_counts = HashMap::new();
        for (reference, source) in frame.field_prefix_sources {
            sources.insert(reference.as_str(), source);
            *source_counts.entry(source.native.as_str()).or_insert(0) += 1;
        }
        let root_scope = &sources.get(root_ref)?.scope;
        if root_scope[2] != root_ref {
            return None;
        }
        let mut result = Self {
            frame,
            root: root_ref,
            root_scope,
            sources,
            source_counts,
            nodes: HashMap::new(),
            occurrences: HashMap::new(),
            records: HashMap::new(),
            record_counts: HashMap::new(),
            spellings: HashMap::new(),
            spelling_counts: HashMap::new(),
            table_members: HashSet::new(),
        };
        let mut pending = vec![(root, None)];
        while let Some((node, parent)) = pending.pop() {
            let reference = node.reference.as_str();
            result.nodes.insert(reference, (node, parent));
            *result.occurrences.entry(reference).or_insert(0) += 1;
            pending.extend(node.children.iter().map(|child| (child, Some(reference))));
        }
        for record in frame.rendered_nodes {
            result.records.insert(record.reference.as_str(), record);
            *result
                .record_counts
                .entry(record.reference.as_str())
                .or_insert(0) += 1;
        }
        for spelling in spellings {
            result
                .spellings
                .insert(spelling.reference.as_str(), spelling);
            *result
                .spelling_counts
                .entry(spelling.reference.as_str())
                .or_insert(0) += 1;
        }
        for grid in frame.native_tables {
            result.table_members.insert(grid.reference.as_str());
            result
                .table_members
                .extend(grid.columns.iter().map(|column| column.reference.as_str()));
            for row in &grid.rows {
                result.table_members.insert(row.reference.as_str());
                result
                    .table_members
                    .extend(row.cells.iter().map(|cell| cell.reference.as_str()));
            }
            let mut pending = result
                .nodes
                .get(grid.reference.as_str())
                .map(|(node, _)| *node)
                .into_iter()
                .collect::<Vec<_>>();
            while let Some(node) = pending.pop() {
                result.table_members.insert(node.reference.as_str());
                pending.extend(&node.children);
            }
        }
        Some(result)
    }

    fn source(&self, entry: &NumberedEntry<'a>) -> Option<DescriptionField<'a>> {
        let reference = entry.reference;
        let source = *self.sources.get(reference)?;
        let &(node, parent) = self.nodes.get(reference)?;
        let record = self.records.get(reference)?;
        let role = self.frame.node_prefixes.get(reference)?;
        if role.is_empty()
            || self
                .frame
                .node_line_ranges
                .get(reference)
                .is_none_or(Range::is_empty)
            || self.occurrences[reference] != 1
            || self.record_counts[reference] != 1
            || self.source_counts[source.native.as_str()] != 1
            || &source.scope != self.root_scope
            || record.parent.as_deref() != parent
            || is_menu(&node.role)
            || self.table_members.contains(reference)
            || !ordinary_entry(entry, &self.frame.lines)
            || record.text.contains('\n')
        {
            return None;
        }
        let description = source
            .description
            .as_deref()
            .filter(|value| plain_text(value) && node.description.as_deref() == Some(*value))?;
        let url = source
            .url
            .as_deref()
            .filter(|value| plain_text(value) && node.url.as_deref() == Some(*value))?;
        let current_role = if let Some(spelling) = self.spellings.get(reference) {
            if self.spelling_counts[reference] != 1
                || spelling.native != source.native
                || spelling.scope != source.scope
            {
                return None;
            }
            let suffix = role.strip_prefix(&spelling.display_label)?;
            if !suffix.is_empty() && !suffix.starts_with(" (") {
                return None;
            }
            format!("{}{suffix}", spelling.alias)
        } else {
            role.clone()
        };
        Some(DescriptionField {
            source,
            description,
            url,
            role,
            current_role,
            original: record.text.trim_start_matches('\t'),
        })
    }
}

struct DescriptionField<'a> {
    source: &'a NativeFieldSource,
    description: &'a str,
    url: &'a str,
    role: &'a str,
    current_role: String,
    original: &'a str,
}

fn ordered_unique_entries(lines: &[String]) -> Vec<NumberedEntry<'_>> {
    let all = numbered_entries(lines);
    let mut counts = HashMap::new();
    for entry in &all {
        *counts.entry(entry.reference).or_insert(0) += 1;
    }
    all.into_iter()
        .filter(|entry| counts[entry.reference] == 1)
        .collect()
}

fn literal_span(body: &str, prefix: &str, label: &str, encoded: &str) -> Option<(usize, usize)> {
    body.strip_prefix(prefix)?;
    let marker = format!("{label}: ");
    let positions = labelled_positions(body, &marker, prefix.len(), false);
    let [start] = positions.as_slice() else {
        return None;
    };
    let rest = body[*start..]
        .strip_prefix(&marker)?
        .strip_prefix(encoded)?;
    (rest.is_empty() || rest.starts_with(", "))
        .then_some((*start, start + marker.len() + encoded.len()))
}

fn expand_local_field_suffixes(lines: &[String]) -> Option<Vec<String>> {
    let mut output = lines.to_vec();
    for entry in ordered_unique_entries(lines) {
        if !ordinary_entry(&entry, lines) {
            continue;
        }
        let minimum = entry.reference.len() + 1;
        let starts = labelled_positions(entry.body, "URL: ", minimum, true);
        let [start] = starts.as_slice() else {
            continue;
        };
        let scalar_start = start + 5;
        if !entry.body[scalar_start..].starts_with('"') {
            continue;
        }
        let (prefix, end) = scalar(entry.body, scalar_start)?;
        let Some(rest) = entry.body[end..].strip_prefix(" + Description") else {
            continue;
        };
        if !rest.is_empty() && !rest.starts_with(", ") || !plain_text(&prefix) {
            return None;
        }
        let positions = labelled_positions(entry.body, "Description: ", minimum, true);
        let [description] = positions.as_slice() else {
            return None;
        };
        let (description, description_end) = scalar(entry.body, description + 13)?;
        if !plain_text(&description)
            || description_end > *start
            || description_end != entry.body.len()
                && !entry.body[description_end..].starts_with(", ")
        {
            return None;
        }
        let replacement = format!(
            "URL: {}",
            super::render::field(&format!("{prefix}{description}"))
        );
        output[entry.line].replace_range(
            entry.start + start..entry.start + entry.body.len() - rest.len(),
            &replacement,
        );
    }
    Some(output)
}

fn share_local_field_suffixes(
    frame: &LayoutFrame<'_>,
    spellings: &[RoleLabelReplacement],
) -> Option<(Vec<String>, Vec<LocalFieldSuffixReplacement>)> {
    let evidence = DescriptionEvidence::build(frame, spellings)?;
    let mut output = frame.lines.clone();
    let mut replacements = Vec::new();
    for entry in ordered_unique_entries(&frame.lines) {
        let Some(source) = evidence.source(&entry) else {
            continue;
        };
        let Some(prefix) = source
            .url
            .strip_suffix(source.description)
            .filter(|prefix| plain_text(prefix))
        else {
            continue;
        };
        let description = super::render::field(source.description);
        let url = super::render::field(source.url);
        let span = |body: &str, role: &str| {
            let prefix = format!("{} {role} ", entry.reference);
            let anchor = literal_span(body, &prefix, "Description", &description)?;
            let target = literal_span(body, &prefix, "URL", &url)?;
            (anchor.1 <= target.0).then_some(target)
        };
        if span(source.original, source.role).is_none() {
            continue;
        }
        let Some((start, end)) = span(entry.body, &source.current_role) else {
            continue;
        };
        let statement = format!("URL: {} + Description", super::render::quoted(prefix));
        if (end - start).saturating_sub(statement.len()) < 32 {
            continue;
        }
        output[entry.line].replace_range(entry.start + start..entry.start + end, &statement);
        replacements.push(LocalFieldSuffixReplacement {
            reference: entry.reference.into(),
            native: source.source.native.clone(),
            scope: source.source.scope.clone(),
            value: source.url.into(),
            anchor_value: source.description.into(),
            prefix: prefix.into(),
            quoted: url.starts_with('"'),
            statement,
        });
    }
    (!replacements.is_empty()
        && expand_local_field_suffixes(&output).as_deref() == Some(frame.lines.as_slice()))
    .then_some((output, replacements))
}

fn adjacent_description(body: &str, prefix: &str, description: &str, url: &str) -> Option<usize> {
    body.strip_prefix(prefix)?;
    let minimum = prefix.len();
    let descriptions = labelled_positions(body, "Description: ", minimum, true);
    let mut urls = labelled_positions(body, "URL: ", minimum, true);
    urls.extend(labelled_positions(body, "URL suffix: ", minimum, true));
    let ([start], [url_start]) = (descriptions.as_slice(), urls.as_slice()) else {
        return None;
    };
    let original = format!("Description: {description}, ");
    if body.get(*start..*url_start)? != original {
        return None;
    }
    let suffix = body[*url_start..].strip_prefix(url)?;
    (suffix.is_empty() || suffix.starts_with(", ")).then_some(*start)
}

fn literal_url_start(body: &str, reference: &str) -> Option<usize> {
    let minimum = reference.len() + 1;
    let mut urls = Vec::new();
    for label in ["URL: ", "URL suffix: "] {
        urls.extend(
            labelled_positions(body, label, minimum, true)
                .into_iter()
                .map(|position| (position, label)),
        );
    }
    let [(start, label)] = urls.as_slice() else {
        return None;
    };
    if !labelled_positions(body, "Description: ", minimum, true).is_empty() {
        return None;
    }
    let scalar_start = start + label.len();
    let (value, end) = scalar(body, scalar_start)?;
    if body[scalar_start..].starts_with('"') {
        if end != body.len() && !body[end..].starts_with(", ") {
            return None;
        }
    } else if *label == "URL suffix: " || value.is_empty() || value.contains(" + Description") {
        return None;
    }
    Some(*start)
}

fn description_declaration(line: &str) -> Option<(usize, Vec<&str>, &str)> {
    let indent = depth(line);
    let (refs, value) = line[indent..]
        .strip_prefix("Description [elements ")?
        .split_once("]: ")?;
    let refs: Vec<_> = refs.split(", ").collect();
    (refs.iter().all(|reference| canonical_reference(reference))
        && !value.is_empty()
        && !value.contains('\n'))
    .then_some((indent, refs, value))
}

fn expand_shared_descriptions(lines: &[String]) -> Option<Vec<String>> {
    let mut output = lines.to_vec();
    let mut hidden = HashSet::new();
    let mut used = HashSet::new();
    for (index, line) in lines.iter().enumerate() {
        let Some((indent, refs, value)) = description_declaration(line) else {
            continue;
        };
        let root_depth = indent.checked_sub(1)?;
        let root = numbered_entries(&lines[..index])
            .into_iter()
            .filter(|entry| {
                ordinary_entry(entry, lines)
                    && lines[entry.line].len() - entry.body.len() == root_depth
            })
            .max_by_key(|entry| entry.line)?;
        let end = (index + 1..lines.len())
            .find(|&line| {
                !lines[line].trim_matches(python_space).is_empty()
                    && depth(&lines[line]) <= root_depth
            })
            .unwrap_or(lines.len());
        if refs.len() < 2 || refs.iter().collect::<HashSet<_>>().len() != refs.len() {
            return None;
        }
        let value = read_layout_json(value).filter(|text| plain_text(text))?;
        let origin = root.line + 1;
        let entries = unique_numbered_entries(&lines[origin..end]);
        let original = format!("Description: {}, ", super::render::field(&value));
        for reference in refs {
            let entry = entries.get(reference)?;
            let position = origin + entry.line;
            if !used.insert((root.line, reference))
                || reference == root.reference
                || position <= index
                || !ordinary_entry(entry, &lines[origin..end])
            {
                return None;
            }
            let start = literal_url_start(entry.body, reference)?;
            let offset = entry.start + start;
            output[position] = format!(
                "{}{original}{}",
                &lines[position][..offset],
                &lines[position][offset..]
            );
        }
        hidden.insert(index);
    }
    Some(
        output
            .into_iter()
            .enumerate()
            .filter_map(|(index, line)| (!hidden.contains(&index)).then_some(line))
            .collect(),
    )
}

struct DescriptionMember<'a> {
    reference: &'a str,
    source: &'a NativeFieldSource,
    line: usize,
    start: usize,
    original: String,
    anchor: &'static str,
}

fn group_field_descriptions(
    frame: &LayoutFrame<'_>,
    spellings: &[RoleLabelReplacement],
    prefixes: &[FieldPrefixReplacement],
    local_suffixes: &[LocalFieldSuffixReplacement],
) -> Option<(Vec<String>, Vec<SharedDescriptionReplacement>)> {
    let evidence = DescriptionEvidence::build(frame, spellings)?;
    let entries = ordered_unique_entries(&frame.lines);
    let root = entries
        .iter()
        .find(|entry| entry.reference == evidence.root && ordinary_entry(entry, &frame.lines))?;
    let local: HashSet<_> = local_suffixes
        .iter()
        .map(|item| item.reference.as_str())
        .collect();
    let mut urls = HashMap::new();
    let mut url_counts = HashMap::new();
    for item in prefixes.iter().filter(|item| item.path == ["url"]) {
        urls.insert(item.reference.as_str(), item);
        *url_counts.entry(item.reference.as_str()).or_insert(0) += 1;
    }
    let mut groups: Vec<(&str, Vec<DescriptionMember<'_>>)> = Vec::new();
    for entry in &entries {
        let reference = entry.reference;
        if reference == evidence.root || entry.line <= root.line || local.contains(reference) {
            continue;
        }
        let Some(source) = evidence.source(entry) else {
            continue;
        };
        let original_url = format!("URL: {}", super::render::field(source.url));
        let (current_url, anchor) = if let Some(item) = urls.get(reference) {
            if url_counts[reference] != 1
                || item.field != FieldKind::Url
                || item.value != source.url
                || item.native != source.source.native
                || item.scope != source.source.scope
            {
                continue;
            }
            (
                item.replacement.as_str(),
                if item.replacement.starts_with("URL suffix: ") {
                    "URL suffix"
                } else {
                    "URL"
                },
            )
        } else {
            (original_url.as_str(), "URL")
        };
        let encoded = super::render::field(source.description);
        let original_prefix = format!("{reference} {} ", source.role);
        if adjacent_description(source.original, &original_prefix, &encoded, &original_url)
            .is_none()
        {
            continue;
        }
        let current_prefix = format!("{reference} {} ", source.current_role);
        let Some(start) = adjacent_description(entry.body, &current_prefix, &encoded, current_url)
        else {
            continue;
        };
        let member = DescriptionMember {
            reference,
            source: source.source,
            line: entry.line,
            start: entry.start + start,
            original: format!("Description: {encoded}, "),
            anchor,
        };
        if let Some((_, members)) = groups
            .iter_mut()
            .find(|(key, _)| *key == source.description)
        {
            members.push(member);
        } else {
            groups.push((source.description, vec![member]));
        }
    }
    let root_indent = &frame.lines[root.line][..root.start];
    let mut output = frame.lines.clone();
    let mut declarations = Vec::new();
    let mut replacements = Vec::new();
    for (value, mut members) in groups.into_iter().filter(|(_, members)| members.len() >= 2) {
        members.sort_by_key(|member| member.line);
        let refs = members
            .iter()
            .map(|member| member.reference)
            .collect::<Vec<_>>();
        let declaration = format!(
            "Description [elements {}]: {}",
            refs.join(", "),
            super::render::quoted(value)
        );
        let public_line = format!("{root_indent}\t{declaration}");
        let saving = members
            .iter()
            .map(|member| member.original.len())
            .sum::<usize>()
            .saturating_sub(public_line.len() + 1);
        if saving < 32 {
            continue;
        }
        declarations.push(public_line);
        for member in members {
            output[member.line]
                .replace_range(member.start..member.start + member.original.len(), "");
            replacements.push(SharedDescriptionReplacement {
                reference: member.reference.into(),
                native: member.source.native.clone(),
                scope: member.source.scope.clone(),
                value: value.into(),
                quoted: super::render::field(value).starts_with('"'),
                root: evidence.root.into(),
                declaration: declaration.clone(),
                original: member.original,
                anchor: member.anchor,
            });
        }
    }
    if replacements.is_empty() {
        return None;
    }
    output.splice(root.line + 1..root.line + 1, declarations);
    (expand_shared_descriptions(&output).as_deref() == Some(frame.lines.as_slice()))
        .then_some((output, replacements))
}

struct TableCapture<'a> {
    nodes: &'a TableValue,
    aliases: HashMap<&'a str, &'a str>,
    issues: &'a [TableValue],
}

impl<'a> TableCapture<'a> {
    fn new(observation: &'a TableValue, refs: &'a HashMap<String, String>) -> Option<Self> {
        let mut counts = HashMap::new();
        for token in refs.values() {
            *counts.entry(token.as_str()).or_insert(0usize) += 1;
        }
        let aliases = refs
            .iter()
            .filter(|(_, token)| counts.get(token.as_str()) == Some(&1))
            .map(|(reference, token)| (token.as_str(), reference.as_str()))
            .collect();
        Some(Self {
            nodes: observation.get("nodes")?,
            aliases,
            issues: observation
                .get("issues")
                .and_then(TableValue::as_seq)
                .unwrap_or_default(),
        })
    }

    fn clean(&self, token: &str, relative_path: &[&str], include_global: bool) -> bool {
        let mut query = Vec::with_capacity(relative_path.len() + 2);
        query.extend(["nodes", token]);
        query.extend_from_slice(relative_path);
        !self
            .issues
            .iter()
            .filter_map(|issue| issue.get("path").and_then(TableValue::as_seq))
            .any(|path| {
                let relevant = include_global
                    || path.len() >= 2
                        && path[0].as_str() == Some("nodes")
                        && path[1].as_str() == Some(token);
                relevant
                    && query
                        .iter()
                        .zip(path)
                        .all(|(part, item)| item.as_str() == Some(part))
            })
    }

    fn complete(
        &self,
        token: &str,
        kind: &str,
        include_global_issues: bool,
    ) -> Option<NativeTableRelation<'a>> {
        let relations = self.nodes.get(token)?.get("relations")?.as_map()?;
        let mut matching = relations.iter().filter(|(_, relation)| {
            relation.get("kind").and_then(TableValue::as_str) == Some(kind)
        });
        let (key, value) = matching.next()?;
        let targets = value.get("targets")?.as_seq()?;
        let positions = value.get("positions")?.as_seq()?;
        let expected_count = i64::try_from(targets.len()).ok()?;
        if matching.next().is_some()
            || !matches!(
                value.get("evidence").and_then(TableValue::as_str),
                Some("reported" | "verified")
            )
            || value.get("enumeration").and_then(TableValue::as_str) != Some("enumerated")
            || !matches!(
                value.get("offset"),
                None | Some(TableValue::Null) | Some(TableValue::Int(0))
            )
            || value.get("total").and_then(TableValue::as_i64) != Some(expected_count)
            || !positions
                .iter()
                .map(TableValue::as_i64)
                .eq((0..expected_count).map(Some))
            || !self.clean(token, &["relations", key], include_global_issues)
        {
            return None;
        }
        let mut seen = HashSet::new();
        for target in targets {
            let target = target.as_str()?;
            if !seen.insert(target)
                || !self.aliases.contains_key(target)
                || self.nodes.get(target).is_none()
            {
                return None;
            }
        }
        Some(NativeTableRelation {
            property: key,
            value,
            targets,
        })
    }

    fn role(&self, token: &str) -> Option<&'a str> {
        self.nodes.get(token)?.get("role")?.as_str()
    }

    fn same_row_order(
        &self,
        children: &NativeTableRelation<'_>,
        rows: &NativeTableRelation<'_>,
    ) -> bool {
        children
            .targets
            .iter()
            .filter(|child| child.as_str().and_then(|child| self.role(child)) == Some("row"))
            .eq(rows.targets)
    }

    fn scope<'s>(
        &self,
        token: &str,
        source_scopes: &'s HashMap<String, String>,
    ) -> Option<&'s str> {
        let reference = self.aliases.get(token)?;
        source_scopes.get(*reference).map(String::as_str)
    }

    fn parent_is(&self, token: &str, expected: &str) -> bool {
        self.complete(token, "parent", false).is_some_and(
            |value| matches!(value.targets, [target] if target.as_str() == Some(expected)),
        )
    }
}

struct NativeTableRelation<'a> {
    property: &'a str,
    value: &'a TableValue,
    targets: &'a [TableValue],
}

impl NativeTableRelation<'_> {
    fn references(&self, capture: &TableCapture<'_>) -> Vec<String> {
        self.targets
            .iter()
            .map(|target| {
                capture.aliases[target.as_str().expect("validated table target")].to_owned()
            })
            .collect()
    }
}

/// Original native `tree` rows and row children used to qualify a public outline.
/// Unlike table-column publication, a collection-wide issue overlaps this proof.
pub(super) fn capture_row_collections(
    observation: &TableValue,
    refs: &HashMap<String, String>,
) -> HashMap<String, TableRowCollection> {
    let Some(capture) = TableCapture::new(observation, refs) else {
        return HashMap::new();
    };
    let mut collections = HashMap::new();
    for (token, _) in capture.nodes.as_map().into_iter().flatten() {
        let Some(&reference) = capture.aliases.get(token.as_str()) else {
            continue;
        };
        if capture.role(token) != Some("tree") {
            continue;
        }
        let (Some(children), Some(rows)) = (
            capture.complete(token, "children", true),
            capture.complete(token, "rows", true),
        ) else {
            continue;
        };
        if rows.targets.is_empty() || !capture.same_row_order(&children, &rows) {
            continue;
        }
        let mut cells = HashMap::new();
        for row in rows.targets {
            let row = row.as_str().expect("validated row identity");
            if let Some(members) = capture.complete(row, "children", true) {
                cells.insert(
                    capture.aliases[row].to_owned(),
                    members.references(&capture),
                );
            }
        }
        collections.insert(
            reference.to_owned(),
            TableRowCollection {
                rows: rows.references(&capture),
                children: children.references(&capture),
                cells,
            },
        );
    }
    collections
}

fn native_grid_rows(
    capture: &TableCapture<'_>,
    token: &str,
    rows: &NativeTableRelation<'_>,
    source_scopes: &HashMap<String, String>,
    table_scope: &str,
) -> Option<Vec<TableRowEvidence>> {
    let mut native_rows = Vec::new();
    let mut row_numbers = HashSet::new();
    for row in rows.targets {
        let row = row.as_str()?;
        let members = capture.complete(row, "children", false)?;
        if capture.role(row) != Some("row")
            || !capture.clean(row, &["role"], false)
            || capture.scope(row, source_scopes) != Some(table_scope)
            || !capture.parent_is(row, token)
            || members.targets.is_empty()
        {
            return None;
        }
        let mut cells = Vec::new();
        let mut row_number = None;
        for (position, cell) in members.targets.iter().enumerate() {
            let cell = cell.as_str()?;
            let structure = capture.nodes.get(cell)?.get("structure")?;
            let row_index = u64::try_from(structure.get("row_index")?.as_i64()?).ok()?;
            let column = i64::try_from(position).ok()?;
            if !matches!(capture.role(cell), Some("cell" | "column_header"))
                || !capture.clean(cell, &["role"], false)
                || ["row_index", "column_index", "row_span", "column_span"]
                    .iter()
                    .any(|field| !capture.clean(cell, &["structure", field], false))
                || structure.get("column_index").and_then(TableValue::as_i64) != Some(column)
                || structure.get("row_span").and_then(TableValue::as_i64) != Some(1)
                || structure.get("column_span").and_then(TableValue::as_i64) != Some(1)
                || capture.scope(cell, source_scopes) != Some(table_scope)
                || !capture.parent_is(cell, row)
                || *row_number.get_or_insert(row_index + 1) != row_index + 1
            {
                return None;
            }
            cells.push(TableCellEvidence {
                reference: capture.aliases[cell].to_owned(),
                row: row_index + 1,
                column: position as u64 + 1,
            });
        }
        if native_rows
            .first()
            .is_some_and(|first: &TableRowEvidence| first.cells.len() != cells.len())
            || !row_numbers.insert(row_number?)
        {
            return None;
        }
        native_rows.push(TableRowEvidence {
            reference: capture.aliases[row].to_owned(),
            cells,
        });
    }
    Some(native_rows)
}

/// Capture native column edges independently of unrelated collection partiality.
pub(super) fn capture_table_grids(
    observation: &TableValue,
    refs: &HashMap<String, String>,
    source_scopes: &HashMap<String, String>,
) -> Vec<NativeTableGrid> {
    let Some(capture) = TableCapture::new(observation, refs) else {
        return Vec::new();
    };
    let Some(session) = observation.get("session").and_then(TableValue::as_str) else {
        return Vec::new();
    };
    let Some(identity) = observation.get("id").and_then(TableValue::as_str) else {
        return Vec::new();
    };
    if capture
        .issues
        .iter()
        .filter_map(|issue| {
            issue
                .get("path")
                .and_then(TableValue::as_seq)
                .map(|path| (path, issue))
        })
        .any(|(path, issue)| {
            path.is_empty()
                || matches!(path, [part] if part.as_str() == Some("nodes"))
                    && issue.get("state").and_then(TableValue::as_str) != Some("partial")
        })
    {
        return Vec::new();
    }
    let mut result = Vec::new();
    for (token, _) in capture.nodes.as_map().into_iter().flatten() {
        let Some(&reference) = capture.aliases.get(token.as_str()) else {
            continue;
        };
        let Some(table_scope) = capture.scope(token, source_scopes) else {
            continue;
        };
        if !matches!(capture.role(token), Some("table" | "tree"))
            || !capture.clean(token, &["role"], false)
        {
            continue;
        }
        let (Some(children), Some(rows)) = (
            capture.complete(token, "children", false),
            capture.complete(token, "rows", false),
        ) else {
            continue;
        };
        if rows.targets.is_empty() || !capture.same_row_order(&children, &rows) {
            continue;
        }
        let Some(native_rows) =
            native_grid_rows(&capture, token, &rows, source_scopes, table_scope)
        else {
            continue;
        };
        let mut columns = Vec::new();
        let scope = [
            session.to_owned(),
            identity.to_owned(),
            table_scope.to_owned(),
        ];
        let count = native_rows[0].cells.len();
        for column in children.targets {
            let column = column.as_str().expect("validated child identity");
            let index = capture
                .nodes
                .get(column)
                .and_then(|node| node.get("structure"))
                .and_then(|structure| structure.get("index"))
                .and_then(TableValue::as_i64)
                .and_then(|index| usize::try_from(index).ok())
                .filter(|&index| index < count);
            let Some(index) = index else { continue };
            if capture.role(column) != Some("column")
                || !capture.clean(column, &["role"], false)
                || !capture.clean(column, &["structure", "index"], false)
                || capture.scope(column, source_scopes) != Some(table_scope)
                || !capture.parent_is(column, token)
            {
                continue;
            }
            let Some(members) = capture.complete(column, "children", false) else {
                continue;
            };
            let targets = members.references(&capture);
            if !targets
                .iter()
                .eq(native_rows.iter().map(|row| &row.cells[index].reference))
            {
                continue;
            }
            columns.push(PublishedTableColumn {
                reference: capture.aliases[column].to_owned(),
                source: column.to_owned(),
                table: reference.to_owned(),
                column: index as u64 + 1,
                targets,
                scope: scope.clone(),
                property: members.property.to_owned(),
                relation: members.value.clone(),
            });
        }
        if !columns.is_empty() {
            result.push(NativeTableGrid {
                reference: reference.to_owned(),
                scope,
                rows: native_rows,
                columns,
            });
        }
    }
    result
}

#[cfg(test)]
mod table_publication_tests {
    use super::*;

    fn table_text(lines: &[&str]) -> Vec<String> {
        lines.iter().map(|line| (*line).to_owned()).collect()
    }

    fn block(indent: &str, base: usize, grammar: bool) -> Vec<String> {
        let mut lines = vec![
            format!(
                "{indent}Rows: row, Outline level: 1; cells: cell.{}",
                if grammar {
                    " Entries: row-ref (Index); cell-ref > child; siblings use ;."
                } else {
                    ""
                },
            ),
            format!("{indent}Row | row (Index) | Column 1 | Column 2"),
            format!("{indent}--- | --- | --- | ---"),
        ];
        for row in 0..3 {
            let reference = base + row * 4;
            lines.push(format!(
                "{indent}{} | {reference} ({row}) | {} > {} text 内容 | {}",
                row + 1,
                reference + 1,
                reference + 2,
                reference + 3
            ));
        }
        lines
    }

    #[test]
    fn numbered_entries_distinguish_headings_and_validated_table_columns() {
        let mut lines = table_text(&[
            "0 group \"中 > 文\" > 50 button",
            "\tElements [button (focused)]:",
            "\t\t1 Title > 2 is part of the name",
        ]);
        lines.extend(block("\t", 10, true));
        let entries = unique_numbered_entries(&lines);
        assert_eq!(entries["50"].body, "50 button");
        assert_eq!(entries["50"].start, lines[0].find("50 button").unwrap());
        assert_eq!(
            entries["1"].expanded(),
            "1 button (focused) Title > 2 is part of the name"
        );
        assert!(!entries["1"].role_explicit);
        assert!(!entries.contains_key("2"));
        assert!(!entries.contains_key("10")); // Row and cell-only references have no own body here.
        assert!(!entries.contains_key("11"));
        assert_eq!(entries["12"].body, "12 text 内容");
        assert!(entries["12"].role_explicit);
        assert_eq!(entries["12"].start, lines[6].find("12 text 内容").unwrap());
    }

    #[test]
    fn shared_entries_restore_only_the_immediate_root_and_require_two_tables() {
        let declaration = format!("{TABLE_ENTRIES_DECLARATION}{TABLE_ENTRIES_GRAMMAR}");
        let mut shared = vec![declaration, "0 window".into(), "\t1 table".into()];
        shared.extend(block("\t\t", 10, false));
        shared.push("\t2 table".into());
        shared.extend(block("\t\t", 30, false));
        shared.push("3 window".into());
        shared.extend(block("\t", 60, false));
        let mut original = table_text(&["0 window", "\t1 table"]);
        original.extend(block("\t\t", 10, true));
        original.push("\t2 table".into());
        original.extend(block("\t\t", 30, true));
        original.push("3 window".into());
        original.extend(block("\t", 60, false));
        assert_eq!(expand_table_entries(&shared), original);
        let incomplete: Vec<_> = shared[..9].to_vec();
        assert_eq!(expand_table_entries(&incomplete), incomplete);
        let doubled = [shared[..1].to_vec(), shared.clone()].concat();
        assert_eq!(expand_table_entries(&doubled), doubled);
    }

    fn object<const N: usize>(values: [(&str, TableValue); N]) -> TableValue {
        TableValue::Map(
            values
                .into_iter()
                .map(|(key, value)| (key.to_owned(), value))
                .collect(),
        )
    }

    fn text(value: &str) -> TableValue {
        TableValue::Text(value.to_owned())
    }

    fn relation(kind: &str, targets: &[&str]) -> TableValue {
        object([
            ("kind", text(kind)),
            (
                "targets",
                TableValue::Seq(targets.iter().map(|target| text(target)).collect()),
            ),
            (
                "positions",
                TableValue::Seq((0..targets.len() as i64).map(TableValue::Int).collect()),
            ),
            ("total", TableValue::Int(targets.len() as i64)),
            ("enumeration", text("enumerated")),
            ("evidence", text("reported")),
        ])
    }

    fn observation(issues: &[&[&str]]) -> TableValue {
        object([
            ("session", text("session")),
            ("id", text("capture")),
            (
                "issues",
                TableValue::Seq(
                    issues
                        .iter()
                        .map(|path| {
                            object([
                                (
                                    "path",
                                    TableValue::Seq(path.iter().map(|part| text(part)).collect()),
                                ),
                                ("state", text("partial")),
                            ])
                        })
                        .collect(),
                ),
            ),
            (
                "nodes",
                object([
                    (
                        "tree",
                        object([
                            ("role", text("tree")),
                            (
                                "relations",
                                object([
                                    ("children", relation("children", &["row", "column"])),
                                    ("rows", relation("rows", &["row"])),
                                ]),
                            ),
                        ]),
                    ),
                    (
                        "row",
                        object([
                            ("role", text("row")),
                            (
                                "relations",
                                object([
                                    ("children", relation("children", &["cell"])),
                                    ("parent", relation("parent", &["tree"])),
                                ]),
                            ),
                        ]),
                    ),
                    (
                        "cell",
                        object([
                            ("role", text("cell")),
                            (
                                "relations",
                                object([("parent", relation("parent", &["row"]))]),
                            ),
                            (
                                "structure",
                                object([
                                    ("row_index", TableValue::Int(0)),
                                    ("column_index", TableValue::Int(0)),
                                    ("row_span", TableValue::Int(1)),
                                    ("column_span", TableValue::Int(1)),
                                ]),
                            ),
                        ]),
                    ),
                    (
                        "column",
                        object([
                            ("role", text("column")),
                            ("structure", object([("index", TableValue::Int(0))])),
                            (
                                "relations",
                                object([
                                    ("children", relation("children", &["cell"])),
                                    ("parent", relation("parent", &["tree"])),
                                ]),
                            ),
                        ]),
                    ),
                ]),
            ),
        ])
    }

    #[test]
    fn native_columns_distinguish_collection_partiality_from_missing_local_evidence() {
        let refs: HashMap<_, _> = [("0", "tree"), ("1", "row"), ("2", "cell"), ("3", "column")]
            .map(|(reference, token)| (reference.to_owned(), token.to_owned()))
            .into();
        let scopes = refs
            .keys()
            .map(|reference| (reference.clone(), "0".to_owned()))
            .collect();
        let complete = observation(&[]);
        let rows = capture_row_collections(&complete, &refs);
        assert_eq!(rows["0"].rows, ["1"]);
        assert_eq!(rows["0"].children, ["1", "3"]);
        assert_eq!(rows["0"].cells["1"], ["2"]);
        let partial = observation(&[&["nodes"]]);
        assert!(capture_row_collections(&partial, &refs).is_empty());
        let grids = capture_table_grids(&partial, &refs, &scopes);
        let [grid] = grids.as_slice() else {
            panic!("expected one exact native column grid")
        };
        assert_eq!(grid.scope, ["session", "capture", "0"]);
        assert_eq!(grid.rows[0].cells[0].reference, "2");
        assert_eq!(grid.columns[0].targets, ["2"]);
        assert_eq!(
            grid.columns[0]
                .to_record()
                .get("relation")
                .unwrap()
                .get("view"),
            Some(&TableValue::Null)
        );
        let partial_cell = observation(&[&["nodes", "cell", "structure", "row_index"]]);
        assert!(capture_table_grids(&partial_cell, &refs, &scopes).is_empty());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lines(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_owned()).collect()
    }

    fn node(reference: &str, role: &str, children: Vec<LayoutNode>) -> LayoutNode {
        LayoutNode::new(reference.into(), role.into(), children)
    }

    fn frame<'a>(
        values: Vec<String>,
        roots: &'a [LayoutNode],
        spans: &'a HashMap<String, Range<usize>>,
        actions: &'a HashMap<String, ActionCatalogue>,
    ) -> LayoutFrame<'a> {
        static NO_TABLE: std::sync::LazyLock<TableEvidence> =
            std::sync::LazyLock::new(TableEvidence::default);
        static NO_SLICES: std::sync::LazyLock<HashMap<String, TextSliceSource>> =
            std::sync::LazyLock::new(HashMap::new);
        static NO_IDENTIFIERS: std::sync::LazyLock<HashMap<String, IdentifierSource>> =
            std::sync::LazyLock::new(HashMap::new);
        static NO_PREFIXES: std::sync::LazyLock<HashMap<String, String>> =
            std::sync::LazyLock::new(HashMap::new);
        static NO_TEXT_PARTS: std::sync::LazyLock<HashMap<String, TextPartLine>> =
            std::sync::LazyLock::new(HashMap::new);
        LayoutFrame {
            lines: values,
            mode: LayoutMode::Compact,
            public_api: true,
            record_stages: true,
            roots,
            rendered_nodes: &[],
            node_line_ranges: spans,
            child_hints: &[],
            empty_value_lines: &[],
            action_sources: actions,
            text_parts: &NO_TEXT_PARTS,
            node_prefixes: &NO_PREFIXES,
            unexpanded_menus: &[],
            identifier_sources: &NO_IDENTIFIERS,
            text_slice_sources: &NO_SLICES,
            table_evidence: &NO_TABLE,
            field_prefix_sources: &[],
            native_tables: &[],
            role_sources: &[],
        }
    }

    #[test]
    fn groups_proven_coverage_at_common_parent_and_keeps_unpacked_hints() {
        let original = lines(&[
            "0 window",
            "\t2 menu item",
            "\t\tChildren: 0/20 read; partial; read()",
            "\t1 menu item",
            "\t\tChildren: 0/20 read; partial; read()",
            "\t3 menu item",
            "\t\tChildren: 0/20 read; partial; read()",
        ]);
        let ranges = HashMap::from([
            ("0".into(), 0..1),
            ("2".into(), 1..3),
            ("1".into(), 3..5),
            ("3".into(), 5..7),
        ]);
        let rendered = [
            ("0", 0, 0..1),
            ("2", 1, 1..3),
            ("1", 1, 3..5),
            ("3", 1, 5..7),
        ]
        .map(|(reference, depth, span)| {
            let hints = original[span.start + 1..span.end].to_vec();
            RenderedNode {
                reference: reference.into(),
                parent: (depth > 0).then(|| "0".to_owned()),
                depth,
                text: original[span].join("\n"),
                hints,
                ..RenderedNode::default()
            }
        });
        let hints = [("2", 2), ("1", 4), ("3", 6)].map(|(reference, index)| ChildCoverageHint {
            reference: reference.into(),
            scope: "observed-window".into(),
            total: 20,
            partial_reason: "depth".into(),
            line_index: index,
            text: original[index].clone(),
        });
        let mut roots = [node(
            "0",
            "window",
            vec![
                node("2", "menuItem", vec![]),
                node("1", "menuItem", vec![]),
                node("3", "menuItem", vec![]),
            ],
        )];
        for child in &mut roots[0].children {
            child.incomplete_children = true;
        }
        let actions = HashMap::new();
        let mut input = frame(original.clone(), &roots, &ranges, &actions);
        input.rendered_nodes = &rendered;
        input.child_hints = &hints;
        let output = pack(input).unwrap();
        assert_eq!(
            output.lines,
            lines(&[
                "0 window",
                "\tChildren [elements 1–3]: 0/20 read; partial; each read()",
                "\t2 menu item",
                "\t1 menu item",
                "\t3 menu item",
            ])
        );
        assert_eq!(
            output.records[0],
            LayoutRecord {
                name: "group_child_coverage",
                before_lines: 7,
                after_lines: 5,
                shared_fields: None,
            }
        );
        assert_eq!(rendered[1].hints, original[2..3]);

        let mut unconfirmed = frame(original.clone(), &roots, &ranges, &actions);
        unconfirmed.public_api = false;
        unconfirmed.rendered_nodes = &rendered;
        unconfirmed.child_hints = &hints;
        assert_eq!(pack(unconfirmed).unwrap().lines, original);
    }

    #[test]
    fn groups_adjacent_equal_empty_cells_without_inventing_one() {
        let mut cells = [
            ("4", "A1", 1, 1),
            ("5", "B1", 2, 1),
            ("6", "C1", 3, 1),
            ("7", "C2", 3, 2),
        ]
        .map(|(reference, address, column, row)| EmptyValueLine {
            reference: reference.into(),
            parent: "3".into(),
            address: address.into(),
            column,
            row,
            indent: "\t".into(),
            properties: "cell".into(),
            fields: lines(&["Value: \"\"", "Available: true"]),
            text: format!("\t{reference} cell {address}, Value: \"\", Available: true"),
        });
        let original: Vec<_> = cells.iter().map(|cell| cell.text.clone()).collect();
        assert_eq!(
            group_empty_values(&original, &cells).unwrap(),
            vec![
                "\tEmpty values [cell; Value: \"\", Available: true]: 4 A1, 5 B1, 6 C1".to_owned(),
                original[3].clone(),
            ]
        );
        cells[1].fields.push("Changed: true".into());
        assert!(group_empty_values(&original, &cells).is_none());
        cells[1].fields.pop();
        let mut duplicated = original.clone();
        duplicated.push(original[1].clone());
        assert!(group_empty_values(&duplicated, &cells).is_none());
    }

    #[test]
    fn shared_actions_require_exact_labels_and_complete_display_scope() {
        let labels = lines(&["展开详细设置", "Copy native identifier", "Copy source text"]);
        let action = format!("Secondary Actions: {}", labels.join("; "));
        let original = vec![
            "0 window".into(),
            format!("\t2 button, {action}"),
            format!("\t1 button, {action}"),
        ];
        let roots = [node(
            "0",
            "window",
            vec![node("2", "button", vec![]), node("1", "button", vec![])],
        )];
        let actions = HashMap::from([
            (
                "1".into(),
                ActionCatalogue {
                    key: "same native catalogue".into(),
                    labels: labels.clone(),
                },
            ),
            (
                "2".into(),
                ActionCatalogue {
                    key: "same native catalogue".into(),
                    labels: labels.clone(),
                },
            ),
        ]);
        let spans = HashMap::new();
        let output = pack(frame(original.clone(), &roots, &spans, &actions)).unwrap();
        assert_eq!(
            output.lines,
            vec![
                "0 window".into(),
                format!("\tSecondary Actions [elements 1, 2]: {}", labels.join("; ")),
                "\t2 button".into(),
                "\t1 button".into()
            ]
        );
        assert_eq!(output.records[1].before_lines, 3);
        assert_eq!(output.records[1].after_lines, 4);
        let quoted = format!("\t1 button \"{action}\"");
        assert!(without_actions(&quoted, &labels).is_none());
        let incomplete = [node(
            "0",
            "window",
            vec![
                node("2", "button", vec![]),
                node("1", "button", vec![]),
                node("3", "button", vec![]),
            ],
        )];
        assert_eq!(
            pack(frame(original.clone(), &incomplete, &spans, &actions))
                .unwrap()
                .lines,
            original
        );
    }

    #[test]
    fn full_group_chains_keep_states_and_following_indentation() {
        let mut child = node("2", "group", vec![node("3", "textField", vec![])]);
        child.anonymous_group = true;
        let mut root = node("1", "group", vec![child]);
        root.anonymous_group = true;
        let roots = [root, node("4", "window", vec![])];
        let original = lines(&[
            "1 container (focused)",
            "\t2 container (expanded)",
            "\t\t3 text field",
            "4 window",
        ]);
        let packed = compact_tree_chains(&original, &layout_nodes(&roots), false).unwrap();
        assert_eq!(
            packed,
            lines(&[
                "1 (focused) > 2 container (expanded)",
                "\t3 text field",
                "4 window"
            ])
        );
        assert_eq!(expand_tree_chains(&packed), original);
    }

    #[test]
    fn visual_chain_requires_a_reversible_line_within_utf16_budget() {
        let mut root = node("1", "group", vec![node("2", "button", vec![])]);
        root.visual_chain_child = Some("2".into());
        let roots = [root];
        let good = lines(&["1 container \"A > B\"", "\t2 button \"按下\""]);
        assert_eq!(
            join_visual_chains(&good, &roots),
            Some(lines(&["1 container \"A > B\" > 2 button \"按下\""]))
        );
        let bad = lines(&["1 container A > B", "\t2 button"]);
        assert_eq!(join_visual_chains(&bad, &roots), None);
        let long = vec![
            format!("1 container {}", "🦀".repeat(160)),
            "\t2 button".into(),
        ];
        assert_eq!(join_visual_chains(&long, &roots), None);
    }
    #[test]
    fn full_caption_parts_keep_exact_sibling_order_and_evidence() {
        let value = "原文をそのまま保持する長いテキストの断片";
        let original = vec![
            format!("\t7 静的テキスト Value: {value}, Secondary Actions: Scroll into view"),
            format!("\t3 静的テキスト Value: {value}, Secondary Actions: Scroll into view"),
        ];
        let mut parts: HashMap<_, _> = ["7", "3"]
            .iter()
            .enumerate()
            .map(|(position, reference)| {
                (
                    reference.to_string(),
                    TextPartLine {
                        parent_ordinal: 1,
                        sibling_position: position,
                        role_label: "静的テキスト".into(),
                        formatted_value: value.into(),
                        quoted_value: format!("\"{value}\""),
                        action_and_capability_key: "[null,[]]".into(),
                    },
                )
            })
            .collect();
        assert_eq!(join_text_parts(&original, &parts), Some(vec![format!(
            "\tText parts [静的テキスト; Secondary Actions: Scroll into view]: 7 \"{value}\", 3 \"{value}\"",
        )]));
        parts.get_mut("3").unwrap().parent_ordinal = 2;
        assert!(join_text_parts(&original, &parts).is_none());
        assert!(!in_unicode_ranges('\u{0345}', LETTER_OR_NUMBER));
        assert!(!plain_text("foo\u{e000}bar"));
    }

    #[test]
    fn identical_leaf_shorthand_keeps_nonascending_reference_order() {
        let roots = [node(
            "0",
            "window",
            vec![
                node("9", "text", vec![]),
                node("4", "text", vec![]),
                node("5", "text", vec![]),
                node("6", "text", vec![]),
            ],
        )];
        let original = lines(&[
            "0 window",
            "\t9 static text value",
            "\t4 static text value",
            "\t5 static text value",
            "\t6 static text value",
        ]);
        assert_eq!(
            group_identical_leaves(&original, &roots),
            Some(lines(&[
                "0 window",
                "\tElements [static text value]: 9, 4–6"
            ]))
        );
        let mut attached = original;
        attached.insert(3, "\t\tText detail".into());
        assert!(group_identical_leaves(&attached, &roots).is_none());
    }

    #[test]
    fn leaf_headers_keep_distinct_caption_text_and_source_layout() {
        let root = node(
            "0",
            "window",
            vec![
                node("7", "link", vec![]),
                node("8", "link", vec![]),
                node("9", "link", vec![]),
            ],
        );
        let roots = [root];
        let prefix = "primary action link (reported disabled)";
        let members = [
            ("7", "第一个链接"),
            ("8", "第二个链接"),
            ("9", "第三个链接"),
        ];
        let mut original = vec!["0 window".to_owned()];
        let mut rendered_nodes = Vec::new();
        let mut node_prefixes = HashMap::new();
        for (reference, tail) in members {
            let text = format!("\t{reference} {prefix} {tail}");
            original.push(text.clone());
            rendered_nodes.push(RenderedNode {
                reference: reference.into(),
                parent: Some("0".into()),
                depth: 1,
                text,
                ..RenderedNode::default()
            });
            node_prefixes.insert(reference.into(), prefix.to_owned());
        }
        let ranges = HashMap::new();
        let actions = HashMap::new();
        let mut input = frame(original.clone(), &roots, &ranges, &actions);
        input.rendered_nodes = &rendered_nodes;
        input.node_prefixes = &node_prefixes;
        let output = pack(input).unwrap();
        assert_eq!(
            output.lines,
            vec![
                "0 window".into(),
                format!("\tElements [{prefix}]:"),
                "\t\t7 第一个链接".into(),
                "\t\t8 第二个链接".into(),
                "\t\t9 第三个链接".into()
            ]
        );
        assert_eq!(expand_leaf_headers(&output.lines), original);
    }

    #[test]
    fn identifier_preamble_keeps_other_preamble_and_stops_at_peer_root() {
        let original = lines(&[
            "ID prefix (prepend to ID suffix): \"com.example.form.\"",
            "URL prefix (prepend to quoted URL suffix): \"https://example.test/\"",
            "0 window ID suffix: \"Root\"",
            "\t1 button ID suffix: \"Close\"",
            "2 other ID suffix: \"Unrelated\"",
        ]);
        assert_eq!(
            expand_identifier_prefixes(&original),
            Some(lines(&[
                "URL prefix (prepend to quoted URL suffix): \"https://example.test/\"",
                "0 window ID: com.example.form.Root",
                "\t1 button ID: com.example.form.Close",
                "2 other ID suffix: \"Unrelated\"",
            ]))
        );
    }
    #[test]
    fn every_selected_layout_stage_keeps_the_original_sequence() {
        let spans = HashMap::new();
        let actions = HashMap::new();
        let names = |mode| {
            let mut input = frame(vec!["0 window".into()], &[], &spans, &actions);
            input.mode = mode;
            pack(input)
                .unwrap()
                .records
                .into_iter()
                .map(|record| record.name)
                .collect::<Vec<_>>()
        };
        assert_eq!(names(LayoutMode::Unpacked), ["group_empty_values"]);
        assert_eq!(
            names(LayoutMode::Full),
            [
                "group_empty_values",
                "join_container_chains",
                "join_text_parts"
            ]
        );
        assert_eq!(
            names(LayoutMode::Compact),
            [
                "group_child_coverage",
                "group_sibling_actions",
                "group_empty_values",
                "join_visual_chains",
                "group_identical_leaves",
                "group_leaf_headers",
                "group_row_records",
                "group_multi_column_records",
                "share_exact_text_slices",
                "group_identifier_prefix",
                "group_field_prefixes",
                "publish_table_containment",
                "group_role_labels",
                "group_input_fields",
                "share_table_entries",
                "share_local_field_suffixes",
                "group_field_descriptions",
            ]
        );
    }

    #[test]
    fn exact_text_slices_retain_utf16_and_native_ancestor_accounting() {
        let child_text = "独立した子要素の完全なテキストを保持します".repeat(4);
        let root_text = format!("🐈 {child_text} 独立した末尾");
        let mut child = node("1", "staticText", vec![]);
        child.value = Some(child_text.clone());
        let mut root = node("0", "window", vec![child]);
        root.name = Some(root_text.clone());
        let roots = [root];
        let source_scope = ["session".to_owned(), "capture".to_owned(), "0".to_owned()];
        let sources = HashMap::from([
            (
                "0".into(),
                TextSliceSource {
                    native: "n0".into(),
                    scope: source_scope.clone(),
                    parent: None,
                    name: Some(root_text.clone()),
                    value: None,
                },
            ),
            (
                "1".into(),
                TextSliceSource {
                    native: "n1".into(),
                    scope: source_scope.clone(),
                    parent: Some("0".into()),
                    name: None,
                    value: Some(child_text.clone()),
                },
            ),
        ]);
        let original = vec![
            format!("0 window {root_text}"),
            format!("\t1 static text {child_text}"),
        ];
        let nodes = [
            RenderedNode {
                reference: "0".into(),
                text: original[0].clone(),
                ..RenderedNode::default()
            },
            RenderedNode {
                reference: "1".into(),
                parent: Some("0".into()),
                depth: 1,
                text: original[1].clone(),
                ..RenderedNode::default()
            },
        ];
        let labels = HashMap::from([
            ("0".into(), "window".into()),
            ("1".into(), "static text".into()),
        ]);
        let spans = HashMap::new();
        let actions = HashMap::new();
        let mut input = frame(original.clone(), &roots, &spans, &actions);
        input.rendered_nodes = &nodes;
        input.node_prefixes = &labels;
        input.text_slice_sources = &sources;
        let (packed, slices) = share_exact_text_slices(&input).unwrap();
        assert_eq!(slices.len(), 1);
        assert_eq!(slices[0].native, "n1");
        assert_eq!(slices[0].anchor_native, "n0");
        assert_eq!(slices[0].scope, source_scope);
        assert_eq!(slices[0].start, 3);
        assert_eq!(slices[0].end, 3 + child_text.encode_utf16().count());
        assert_eq!(expand_exact_text_slices(&packed).unwrap(), original);
    }

    #[test]
    fn local_urls_and_shared_descriptions_keep_independent_native_fields() {
        let description =
            "Full original description with independent provenance ".repeat(4) + "end";
        let local_url = format!("https://example.test/local/{description}");
        let other_urls = [
            "https://example.test/item/one",
            "https://example.test/item/two",
        ];
        let mut children = Vec::new();
        let scope = ["session".to_owned(), "capture".to_owned(), "0".to_owned()];
        let mut sources = vec![(
            "0".into(),
            NativeFieldSource {
                native: "n0".into(),
                scope: scope.clone(),
                name: None,
                description: None,
                url: None,
            },
        )];
        let mut original = vec!["0 window".to_owned()];
        let mut records = vec![RenderedNode {
            reference: "0".into(),
            text: original[0].clone(),
            ..RenderedNode::default()
        }];
        let mut spans = HashMap::from([("0".into(), 0..1)]);
        let mut prefixes = HashMap::from([("0".into(), "window".into())]);
        for (ordinal, url) in [local_url.as_str(), other_urls[0], other_urls[1]]
            .into_iter()
            .enumerate()
        {
            let index = ordinal + 1;
            let reference = index.to_string();
            let mut child = node(&reference, "link", vec![]);
            child.description = Some(description.clone());
            child.url = Some(url.into());
            children.push(child);
            sources.push((
                reference.clone(),
                NativeFieldSource {
                    native: format!("n{index}"),
                    scope: scope.clone(),
                    name: None,
                    description: Some(description.clone()),
                    url: Some(url.into()),
                },
            ));
            let text = format!("\t{reference} link Description: {description}, URL: {url}");
            original.push(text.clone());
            records.push(RenderedNode {
                reference: reference.clone(),
                parent: Some("0".into()),
                depth: 1,
                text,
                ..RenderedNode::default()
            });
            spans.insert(reference.clone(), index..index + 1);
            prefixes.insert(reference, "link".into());
        }
        let roots = [node("0", "window", children)];
        let actions = HashMap::new();
        let mut input = frame(original.clone(), &roots, &spans, &actions);
        input.rendered_nodes = &records;
        input.node_prefixes = &prefixes;
        input.field_prefix_sources = &sources;
        let (local, suffixes) = share_local_field_suffixes(&input, &[]).unwrap();
        assert_eq!(suffixes.len(), 1);
        assert_eq!(suffixes[0].native, "n1");
        assert_eq!(suffixes[0].anchor_value, description);
        assert_eq!(expand_local_field_suffixes(&local).unwrap(), original);
        input.lines = local.clone();
        let (shared, replacements) = group_field_descriptions(&input, &[], &[], &suffixes).unwrap();
        assert_eq!(
            replacements
                .iter()
                .map(|item| item.native.as_str())
                .collect::<Vec<_>>(),
            ["n2", "n3"]
        );
        assert_eq!(expand_shared_descriptions(&shared).unwrap(), local);
    }

    #[test]
    fn field_prefix_keeps_non_ascii_character_boundaries() {
        let spans = HashMap::new();
        let actions = HashMap::new();
        let frame = frame(Vec::new(), &[], &spans, &actions);
        let prefix = format!("https://example.test/{}/", "云文档".repeat(12));
        let values = [format!("{prefix}one"), format!("{prefix}two")];
        let source = NativeFieldSource {
            native: "native".to_owned(), scope: ["session".to_owned(), "capture".to_owned(), "0".to_owned()],
            name: None, description: None, url: None,
        };
        let fields = values.each_ref().map(|value| PrefixField {
            reference: "0", source: &source, path: &["url"], kind: FieldKind::Url, value,
            quoted: false, line: 0, start: 0, end: value.len() + "URL: ".len(),
        });
        let group = best_field_group(FieldKind::Url, &[&fields[0], &fields[1]], &frame, "").unwrap();
        assert_eq!(group.prefix, prefix);
    }
}

#[cfg(test)]
mod table_tests {
    use super::*;

    const CELL_PREFIX: &str = "cell (selected, selectable, focusable, reports focus)";

    fn make_node(reference: usize, role: &str, children: Vec<LayoutNode>) -> LayoutNode {
        LayoutNode::new(reference.to_string(), role.into(), children)
    }

    struct Fixture {
        roots: Vec<LayoutNode>,
        lines: Vec<String>,
        records: Vec<RenderedNode>,
        prefixes: HashMap<String, String>,
        ranges: HashMap<String, Range<usize>>,
        evidence: TableEvidence,
    }

    impl Fixture {
        fn add(
            &mut self,
            reference: usize,
            parent: Option<usize>,
            depth: usize,
            prefix: &str,
            body: String,
        ) {
            let reference = reference.to_string();
            let text = format!("{}{reference} {prefix}{body}", "\t".repeat(depth));
            let position = self.lines.len();
            self.lines.push(text.clone());
            self.records.push(RenderedNode {
                reference: reference.clone(),
                parent: parent.map(|parent| parent.to_string()),
                depth,
                text,
                ..RenderedNode::default()
            });
            self.prefixes.insert(reference.clone(), prefix.to_owned());
            self.ranges.insert(reference, position..position + 1);
        }

        fn build(row_count: usize, columns: usize, table_role: &str) -> Self {
            let mut fixture = Self {
                roots: Vec::new(),
                lines: Vec::new(),
                records: Vec::new(),
                prefixes: HashMap::new(),
                ranges: HashMap::new(),
                evidence: TableEvidence::default(),
            };
            fixture.add(0, None, 0, table_role, String::new());
            let mut rows = Vec::new();
            for index in 0..row_count {
                let row = 1 + index * (columns + 1);
                fixture.add(
                    row,
                    Some(0),
                    1,
                    "row",
                    format!(" Index (0-based): {index}, Outline level: 1"),
                );
                fixture.evidence.facts.insert(
                    row.to_string(),
                    TableNodeEvidence {
                        index: Some(index as u64),
                        outline: Some(1),
                        ..TableNodeEvidence::default()
                    },
                );
                let mut cells = Vec::new();
                for column in 1..=columns {
                    let cell = row + column;
                    fixture.add(
                        cell,
                        Some(row),
                        2,
                        CELL_PREFIX,
                        format!(" Row: {}, Column: {column}", index + 1),
                    );
                    fixture.evidence.facts.insert(
                        cell.to_string(),
                        TableNodeEvidence {
                            row: Some(index as u64 + 1),
                            column: Some(column as u64),
                            ..TableNodeEvidence::default()
                        },
                    );
                    cells.push(make_node(cell, "cell", Vec::new()));
                }
                rows.push(make_node(row, "row", cells));
            }
            fixture.roots.push(make_node(0, table_role, rows));
            fixture
        }

        fn frame<'a>(
            &'a self,
            actions: &'a HashMap<String, ActionCatalogue>,
            parts: &'a HashMap<String, TextPartLine>,
            identifiers: &'a HashMap<String, IdentifierSource>,
        ) -> LayoutFrame<'a> {
            static NO_SLICES: std::sync::LazyLock<HashMap<String, TextSliceSource>> =
                std::sync::LazyLock::new(HashMap::new);
            LayoutFrame {
                lines: self.lines.clone(),
                mode: LayoutMode::Compact,
                public_api: true,
                record_stages: false,
                roots: &self.roots,
                rendered_nodes: &self.records,
                node_prefixes: &self.prefixes,
                unexpanded_menus: &[],
                node_line_ranges: &self.ranges,
                child_hints: &[],
                empty_value_lines: &[],
                action_sources: actions,
                text_parts: parts,
                identifier_sources: identifiers,
                text_slice_sources: &NO_SLICES,
                table_evidence: &self.evidence,
                field_prefix_sources: &[],
                native_tables: &[],
                role_sources: &[],
            }
        }
    }

    #[test]
    fn single_cell_rows_use_the_original_exact_header_and_independent_refs() {
        let fixture = Fixture::build(8, 1, "outline");
        let actions = HashMap::new();
        let parts = HashMap::new();
        let identifiers = HashMap::new();
        let frame = fixture.frame(&actions, &parts, &identifiers);
        let packed = pack_row_records(&frame, &fixture.evidence).unwrap();
        assert_eq!(packed.len(), 11);
        assert_eq!(packed[1], format!("\t{ROW_RECORD_TITLE}"));
        assert_eq!(packed[2], format!("\trow Index (0-based) / cell Row | row | {CELL_PREFIX}, Column: 1{ROW_RECORD_FOOTER}"));
        assert_eq!(packed[3], "\t0 / 1 | 1, Outline level: 1 | 2 | —");
        assert_eq!(expand_row_records(&packed), fixture.lines);
        assert_eq!(fixture.records[2].reference, "2");
        assert_eq!(fixture.records[2].parent.as_deref(), Some("1"));

        let mut incomplete = Fixture::build(8, 1, "outline");
        incomplete.roots[0].incomplete_children = true;
        assert!(pack_row_records(
            &incomplete.frame(&actions, &parts, &identifiers),
            &incomplete.evidence
        )
        .is_none());
    }

    #[test]
    fn multiple_cells_preserve_column_positions_and_publish_exact_table_evidence() {
        let mut fixture = Fixture::build(5, 2, "table");
        let actions = HashMap::new();
        let parts = HashMap::new();
        let identifiers = HashMap::new();
        let packed = pack_multi_column_layout(
            &fixture.frame(&actions, &parts, &identifiers),
            &fixture.evidence,
        )
        .unwrap();
        assert_eq!(expand_multi_column_records(&packed.lines), fixture.lines);
        assert_eq!(packed.lines[1], format!("\tRows: row, Outline level: 1; cells: {CELL_PREFIX}. Entries: row-ref (Index); cell-ref{TABLE_INTRO_TAIL}"));
        assert_eq!(
            packed.lines[2],
            "\tRow | row (Index (0-based)) | Column 1 | Column 2"
        );
        assert_eq!(packed.lines[4], "\t1 | 1 (0) | 2 | 3");
        assert_eq!(
            packed.tables,
            [TableLayoutEvidence {
                table: "0".into(),
                layout: Vec::new(),
                introduction: packed.lines[1].clone(),
                heading: packed.lines[2].clone(),
                rows: ["1", "4", "7", "10", "13"].map(str::to_owned).to_vec(),
            }]
        );
        fixture.evidence.facts.get_mut("8").unwrap().column = Some(2);
        assert!(pack_multi_column_layout(
            &fixture.frame(&actions, &parts, &identifiers),
            &fixture.evidence
        )
        .is_none());
    }

    #[test]
    fn multi_cell_outline_requires_original_row_and_cell_membership() {
        let mut fixture = Fixture::build(5, 2, "outline");
        let actions = HashMap::new();
        let parts = HashMap::new();
        let identifiers = HashMap::new();
        assert!(pack_multi_column_layout(
            &fixture.frame(&actions, &parts, &identifiers),
            &fixture.evidence
        )
        .is_none());
        let rows: Vec<_> = fixture.roots[0]
            .children
            .iter()
            .map(|row| row.reference.clone())
            .collect();
        let mut cells = HashMap::new();
        for row in &fixture.roots[0].children {
            let refs: Vec<_> = row
                .children
                .iter()
                .map(|cell| cell.reference.clone())
                .collect();
            fixture
                .evidence
                .facts
                .get_mut(&row.reference)
                .unwrap()
                .native_children = Some(refs.clone());
            cells.insert(row.reference.clone(), refs);
        }
        fixture.evidence.facts.insert(
            "0".into(),
            TableNodeEvidence {
                native_children: Some(rows.clone()),
                ..TableNodeEvidence::default()
            },
        );
        fixture.evidence.row_collections.insert(
            "0".into(),
            TableRowCollection {
                rows: rows.clone(),
                children: rows,
                cells,
            },
        );
        assert!(pack_multi_column_layout(
            &fixture.frame(&actions, &parts, &identifiers),
            &fixture.evidence
        )
        .is_some());
        fixture
            .evidence
            .facts
            .get_mut("0")
            .unwrap()
            .native_children
            .as_mut()
            .unwrap()
            .swap(0, 1);
        assert!(pack_multi_column_layout(
            &fixture.frame(&actions, &parts, &identifiers),
            &fixture.evidence
        )
        .is_none());
    }
}
