// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Terminal rendering of captured trees. Identity and comparison remain upstream.

use super::diff::{DeltaPlan, Mode, TreeDelta};

pub(super) mod atoms;
pub(super) mod details;
pub(super) mod fallback;
pub(super) mod geometric;
pub(super) mod lines;
pub(super) mod text;
pub(super) use text::TextError;

#[derive(Debug, Default, PartialEq, Eq)]
pub(super) struct RenderedNode {
    pub(super) reference: String,
    pub(super) parent: Option<String>,
    pub(super) depth: usize,
    pub(super) text: String,
    pub(super) content: String,
    pub(super) hints: Vec<String>,
    pub(super) related: Vec<String>,
    pub(super) partial: bool,
    pub(super) layout: Vec<(String, String)>,
}

#[derive(Debug, Default, PartialEq, Eq)]
pub(super) struct RenderedSection {
    pub(super) text: String,
    pub(super) nodes: Vec<RenderedNode>,
    pub(super) prefix: String,
    pub(super) suffix: String,
    pub(super) refresh: bool,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) enum RenderedPart {
    Text(String),
    Literal { full: String, incremental: String },
    Section(RenderedSection),
}

impl RenderedPart {
    pub(super) fn text(&self) -> &str {
        match self {
            Self::Text(text) => text,
            Self::Literal { full, .. } => full,
            Self::Section(section) => &section.text,
        }
    }
}

#[derive(Debug, Default, PartialEq, Eq)]
pub(super) struct RenderedSnapshot {
    pub(super) parts: Vec<RenderedPart>,
    pub(super) details: String,
    pub(super) detail_supplement: Option<String>,
    pub(super) content_directory: Option<(String, String)>,
}

impl RenderedSnapshot {
    pub(super) fn text(&self) -> String {
        self.parts.iter().map(RenderedPart::text).collect()
    }
}

#[derive(Debug, PartialEq, Eq)]
pub(super) enum DocumentPart {
    Text {
        text: String,
        full_heading: Option<String>,
        detail_hint: bool,
    },
    Tree {
        snapshot: usize,
        indent: usize,
        details: bool,
    },
}

fn python_line_separator(value: char) -> bool {
    matches!(
        value,
        '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
    )
}

fn javascript_whitespace(value: char) -> bool {
    matches!(
        value,
        '\t' | '\u{b}'
            | '\u{c}'
            | ' '
            | '\u{a0}'
            | '\u{feff}'
            | '\n'
            | '\r'
            | '\u{2028}'
            | '\u{2029}'
            | '\u{1680}'
            | '\u{2000}'..='\u{200a}' | '\u{202f}' | '\u{205f}' | '\u{3000}'
    )
}

fn physical_lines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut characters = text.char_indices().peekable();
    let mut line_start = 0;
    while let Some((index, value)) = characters.next() {
        if !python_line_separator(value) {
            continue;
        }
        lines.push(&text[line_start..index]);
        line_start = index + value.len_utf8();
        if value == '\r' && characters.peek().is_some_and(|(_, next)| *next == '\n') {
            characters.next();
            line_start += 1;
        }
    }
    if line_start < text.len() {
        lines.push(&text[line_start..]);
    }
    lines
}

pub(super) fn indent(text: &str, depth: usize) -> String {
    if text.is_empty() || depth == 0 {
        return text.to_owned();
    }
    let padding = "\t".repeat(depth);
    let mut output = String::with_capacity(text.len().saturating_add(depth));
    for (index, line) in physical_lines(text).into_iter().enumerate() {
        if index != 0 {
            output.push('\n');
        }
        output.push_str(&padding);
        output.push_str(line);
    }
    output
}

pub(super) fn quoted(text: &str) -> String {
    let mut output = String::with_capacity(text.len().saturating_add(2));
    text::write_quoted(&mut output, text, text::JsonFormat::Literal);
    output
}

pub(super) fn field(text: &str) -> String {
    if text.is_empty()
        || text != text.trim_matches(javascript_whitespace)
        || text.starts_with(['<', '+', '~', '|'])
        || text.contains("(partial:")
        || text.contains("(showing ")
        || text.contains(|value| {
            matches!(value, ',' | '"' | '>' | '\\' | '\0'..='\u{1f}' | '\u{7f}'..='\u{9f}' | '\u{2028}' | '\u{2029}')
        })
    {
        quoted(text)
    } else {
        text.to_owned()
    }
}

fn header(mut text: String, mode: &str) -> String {
    let title = format!("Tree: {mode}");
    let mut start = 0;
    for line in text.split('\n') {
        if line.starts_with("Tree: ") {
            text.replace_range(start..start + line.len(), &title);
            return text;
        }
        start += line.len() + 1;
    }
    if let Some(first_newline) = text.find('\n') {
        text.insert_str(first_newline + 1, &format!("{title}\n"));
    } else if text.is_empty() {
        text.push_str(&title);
    } else {
        text.push('\n');
        text.push_str(&title);
    }
    text
}

fn section_delta(
    section: &RenderedSection,
    delta: &TreeDelta,
    missing: &[String],
    plan: &DeltaPlan,
    depth: usize,
) -> String {
    let (unobserved, removed): (Vec<_>, Vec<_>) = missing
        .iter()
        .map(String::as_str)
        .partition(|reference| plan.not_observed.contains(*reference) || plan.retained.contains(*reference));
    let mut lines = Vec::new();
    if !section.prefix.is_empty() {
        lines.push(indent(&section.prefix, depth));
    }
    if !removed.is_empty() {
        lines.push(format!("Removed element IDs: {}", removed.join(", ")));
    }
    if !unobserved.is_empty() {
        lines.push(format!("Not observed in this read: {}", unobserved.join(", ")));
    }
    let mut layout: &[(String, String)] = &[];
    for node in &section.nodes {
        let changed = delta.change(&node.reference);
        let context = delta.context.contains(&node.reference);
        let expanded = delta.expanded.contains(&node.reference);
        if section.refresh || changed.is_some() || context {
            let common = layout
                .iter()
                .zip(&node.layout)
                .take_while(|((old_key, old_text), (key, text))| {
                    old_key == key && (old_text == text || depth > 0 && indent(old_text, depth) == indent(text, depth))
                })
                .count();
            lines.extend(node.layout[common..].iter().map(|(_, text)| indent(text, depth)));
            layout = &node.layout;
        }
        if section.refresh || changed.is_some() {
            let marker = changed.map_or('~', |change| change.marker());
            lines.push(format!("{marker}{}", indent(&node.text, depth)));
        } else if context {
            let text = indent(&node.text, depth);
            let context_text = if expanded {
                &text
            } else {
                text.split('\n').next().unwrap_or("")
            };
            lines.push(format!(" {context_text}"));
        }
        if !section.refresh && changed.is_none() && !expanded {
            for hint in &node.hints {
                let hint = indent(hint, depth);
                if hint.trim_start_matches('\t').starts_with("Omitted: ") {
                    lines.push(format!(
                        "Element {}: {}",
                        node.reference,
                        hint.trim_start_matches(javascript_whitespace)
                    ));
                } else {
                    lines.push(hint);
                }
            }
        }
    }
    if !section.suffix.is_empty() {
        lines.push(indent(&section.suffix, depth));
    }
    lines.join("\n")
}

pub(super) fn tree_part(
    snapshot: &RenderedSnapshot, depth: usize, details: bool, plan: Option<&DeltaPlan>, tree_index: usize,
) -> (String, bool) {
    let full = || {
        let text = if details {
            snapshot.detail_supplement.as_deref().unwrap_or(&snapshot.details).to_owned()
        } else {
            snapshot.text()
        };
        indent(&text, depth)
    };
    let Some(plan) = plan.filter(|plan| plan.mode != Mode::Full) else {
        return (full(), false);
    };
    match (plan.trees.get(tree_index), details) {
        (Some(None), _) => (format!("Full tree for this scope:\n{}", full()), false),
        (_, true) => (full(), false),
        (None, false) => (format!("Presentation refresh for this scope:\n{}", full()), true),
        (Some(Some(delta)), false) => {
            let mut body = String::new();
            let mut refreshed = false;
            let mut missing = delta.missing.as_slice();
            for part in &snapshot.parts {
                match part {
                    RenderedPart::Text(text) => body.push_str(&indent(text, depth)),
                    RenderedPart::Literal { incremental, .. } => body.push_str(&indent(incremental, depth)),
                    RenderedPart::Section(section) => {
                        body.push_str(&section_delta(section, delta, missing, plan, depth));
                        missing = &[];
                        refreshed |= section.refresh;
                    }
                }
            }
            (body, refreshed)
        }
    }
}

pub(super) fn document(
    parts: &[DocumentPart],
    snapshots: &[RenderedSnapshot],
    plan: Option<&DeltaPlan>,
) -> Option<String> {
    let incremental = plan.filter(|plan| plan.mode != Mode::Full);
    let mut body = String::new();
    let mut detail_hint = false;
    let mut refreshed = false;
    let mut tree_index = 0;
    let mut seen = vec![false; snapshots.len()];
    let mut directories = Vec::new();
    let mut payloads = Vec::new();
    for part in parts {
        match part {
            DocumentPart::Text {
                text,
                full_heading,
                detail_hint: hint,
            } => {
                body.push_str(if incremental.is_none() {
                    full_heading.as_deref().unwrap_or(text)
                } else {
                    text
                });
                detail_hint |= hint;
            }
            DocumentPart::Tree {
                snapshot,
                indent: depth,
                details,
            } => {
                let index = *snapshot;
                let snapshot = snapshots.get(index)?;
                if incremental.is_none() && *details && !seen[index] {
                    seen[index] = true;
                    if let Some((directory, payload)) = &snapshot.content_directory {
                        if !directory.is_empty() {
                            directories.push(directory.as_str());
                            payloads.push(payload.as_str());
                        }
                    }
                }
                let (text, part_refreshed) = tree_part(snapshot, *depth, *details, incremental, tree_index);
                body.push_str(&text);
                refreshed |= part_refreshed;
                tree_index += 1;
            }
        }
    }
    if detail_hint {
        body.push_str("\nCaptured details available.");
    }
    if let Some(plan) = incremental {
        return Some(header(
            body,
            if refreshed || plan.mode == Mode::Diff {
                "diff"
            } else {
                "unchanged"
            },
        ));
    }
    if !directories.is_empty() {
        directories.extend(payloads);
        directories.push(&body);
        return Some(directories.join("\n\n"));
    }
    Some(body)
}

#[cfg(test)]
mod tests;
