// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Serialize prepared logical table and outline plans without recovering new rows.

use super::super::super::collections::{
    CollectionMemberPrefix, CollectionPrefixPlan, TableCellPlan, TablePlan,
};
use super::super::atoms::{field, slot_value, Slot};
use super::{nested, BlockId, Context, LayoutContext, NodeId, Result, TextError, Writer};

fn header(prefix: Option<&CollectionPrefixPlan>) -> Result<String> {
    let Some(prefix) = prefix else {
        return Ok(String::new());
    };
    let mut text = field(&prefix.role);
    if !prefix.states.is_empty() {
        text.push_str(&format!(" ({})", prefix.states.join(", ")));
    }
    let slots = prefix
        .slots
        .iter()
        .map(|slot| {
            let value = slot_value(slot)?;
            Ok(slot
                .label
                .map_or_else(|| value.clone(), |label| format!("{label}: {value}")))
        })
        .collect::<Result<Vec<_>>>()?;
    if !slots.is_empty() {
        text.push_str(", ");
        text.push_str(&slots.join(", "));
    }
    Ok(format!(" [{text}]"))
}

impl<'a> Writer<'a> {
    fn collection_slots<'p>(
        &self,
        table: &TablePlan,
        reference: NodeId,
        prefix: Option<&'p CollectionPrefixPlan>,
    ) -> Result<(Vec<Slot>, Option<&'p CollectionMemberPrefix>)> {
        let prefix = prefix.and_then(|prefix| {
            prefix
                .members
                .iter()
                .find(|item| item.reference == reference)
        });
        let visible = self
            .atom(reference)?
            .slots
            .iter()
            .filter(|slot| {
                !table
                    .detail_fields
                    .iter()
                    .any(|item| item.reference == reference && item.slot.key == slot.key)
            })
            .cloned()
            .collect();
        Ok((visible, prefix))
    }

    fn cell_branch(
        &mut self,
        table: &TablePlan,
        reference: NodeId,
        depth: usize,
        row_depth: usize,
        cell: &TableCellPlan,
        context: &Context,
        trailing: &mut Vec<String>,
    ) -> Result<String> {
        let column = table
            .column_indices
            .iter()
            .position(|column| *column == cell.column)
            .ok_or(TextError::Invalid("collection cell column"))?;
        enum CellWork {
            Node(NodeId, usize),
            Separator(&'static str),
        }
        let mut work = vec![CellWork::Node(reference, depth)];
        let mut output = String::new();
        while let Some(work_item) = work.pop() {
            let (reference, depth) = match work_item {
                CellWork::Node(reference, depth) => (reference, depth),
                CellWork::Separator(value) => {
                    output.push_str(value);
                    continue;
                }
            };
            self.remember(reference, depth, context, None)?;
            let prefix = table
                .column_prefixes
                .get(column)
                .and_then(Option::as_ref)
                .filter(|_| reference == cell.reference);
            let (slots, member) = self.collection_slots(table, reference, prefix)?;
            let lines =
                self.atom_lines_with_prefix(reference, false, true, Some(&slots), member)?;
            let (own, tail) = self.pack_lines(reference, row_depth, lines)?;
            trailing.extend(tail);
            output.push_str(&own);
            let node = self
                .projection
                .node(reference)
                .ok_or(TextError::Invalid("collection projected cell"))?;
            if node.children.is_empty() {
                continue;
            }
            output.push_str(" > ");
            if node.children.len() > 1 {
                output.push('{');
                work.push(CellWork::Separator("}"));
            }
            for (index, &child) in node.children.iter().enumerate().rev() {
                work.push(CellWork::Node(child, depth + 1));
                if index != 0 {
                    work.push(CellWork::Separator("; "));
                }
            }
        }
        Ok(output)
    }

    fn table_location(&self, reference: NodeId) -> Result<BlockId> {
        self.plan
            .ref_locations
            .get(&reference)
            .copied()
            .ok_or(TextError::Invalid("collection source block"))
    }

    fn schedule_table_source(
        &mut self,
        reference: NodeId,
        depth: usize,
        context: LayoutContext,
        skip_seen: bool,
    ) {
        self.defer(move |writer| {
            if !skip_seen || !writer.seen.contains(&reference) {
                let id = writer.table_location(reference)?;
                writer.schedule(id, depth, &context, false);
            }
            Ok(())
        });
    }

    pub(super) fn write_table(
        &mut self,
        table: &'a TablePlan,
        depth: usize,
        context: LayoutContext,
    ) -> Result<()> {
        self.write_atom(table.reference, depth, &context, false)?;
        let indent = "\t".repeat(depth + 1);
        let mut headings = vec![format!("Rows{}", header(table.row_prefix.as_ref())?)];
        for ((column, prefix), label) in table
            .column_indices
            .iter()
            .zip(&table.column_prefixes)
            .zip(&table.column_labels)
        {
            let label = label.as_ref().map_or_else(
                || {
                    if table.layout == "outline" {
                        "Cells".to_owned()
                    } else {
                        format!("Column {}", column + 1)
                    }
                },
                |label| field(label),
            );
            headings.push(format!("{label}{}", header(prefix.as_ref())?));
        }
        let (label, separator) = if table.layout == "outline" {
            ("Outline", " > ")
        } else {
            ("Records", " | ")
        };
        let heading = format!("{indent}{label}: {}", headings.join(separator));
        self.lines.push(heading.clone());
        if !table.detail_fields.is_empty() {
            self.lines.push(format!(
                "{indent}Captured structure details: indices and cell extents"
            ));
        }
        let owner = self.atom(table.reference)?.reference.as_str();
        let row_context: LayoutContext =
            nested(&context, &format!("table:{owner}"), &heading).into();
        let row_depth = depth + 2;
        for &reference in table.remaining_refs.iter().rev() {
            self.schedule_table_source(reference, depth + 1, context.clone(), true);
        }
        for column in table.columns.iter().rev() {
            self.schedule_table_source(column.reference, depth + 1, row_context.clone(), true);
        }
        if table.layout == "rows" {
            for row in table.rows.iter().rev() {
                let context = row_context.clone();
                self.defer(move |writer| {
                    writer.write_atom(row.reference, row_depth, &context, false)?;
                    writer.declarations(writer.table_location(row.reference)?, row_depth, None);
                    for cell in row.cells.iter().rev() {
                        writer.schedule_table_source(
                            cell.reference,
                            row_depth + 1,
                            context.clone(),
                            false,
                        );
                    }
                    Ok(())
                });
            }
            return Ok(());
        }
        for row in &table.rows {
            self.remember(row.reference, row_depth, &row_context, None)?;
            let (slots, member) =
                self.collection_slots(table, row.reference, table.row_prefix.as_ref())?;
            let lines =
                self.atom_lines_with_prefix(row.reference, false, true, Some(&slots), member)?;
            let (row_text, mut trailing) = self.pack_lines(row.reference, row_depth, lines)?;
            let mut cells = vec![row_text];
            for cell in &row.cells {
                cells.push(self.cell_branch(
                    table,
                    cell.reference,
                    row_depth,
                    row_depth,
                    cell,
                    &row_context,
                    &mut trailing,
                )?);
            }
            self.lines
                .push(format!("{indent}\t{}", cells.join(separator)));
            self.lines.extend(trailing);
            let mut pending = vec![self.table_location(row.reference)?];
            while let Some(block) = pending.pop() {
                self.declarations(block, row_depth, None);
                pending.extend(self.block(block)?.children.iter().rev().copied());
            }
        }
        Ok(())
    }
}
