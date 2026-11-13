// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Collect inline cells in source order before emitting their local grid or row.

use std::{collections::HashMap, rc::Rc};

use super::{compact_grid, floating_row, Block, BlockId, LayoutContext, Result, TextError, Writer};

struct Grid<'a> {
    block: &'a Block,
    depth: usize,
    context: LayoutContext,
    next_row: usize,
    matrix: Vec<Vec<String>>,
    irregular: HashMap<BlockId, String>,
    annotations: HashMap<BlockId, Vec<String>>,
}

struct GridRow<'a> {
    id: BlockId,
    block: &'a Block,
    cells: Vec<String>,
    placed: Vec<(Option<usize>, String)>,
    trailing: Vec<String>,
}

impl<'a> Grid<'a> {
    fn save_row(&mut self, row: GridRow<'a>) -> Result<()> {
        self.annotations.insert(row.id, row.trailing);
        if row.block.column_slots.contains(&None) {
            self.irregular
                .insert(row.id, floating_row(&row.placed, row.cells.len())?);
        } else {
            self.matrix.push(row.cells);
        }
        Ok(())
    }

    fn rows(mut self, writer: &mut Writer<'a>) -> Result<()> {
        while let Some(&id) = self.block.children.get(self.next_row) {
            let block = writer.block(id)?;
            if block.column_slots.len() != block.children.len() {
                return Err(TextError::Invalid("geometric grid row slots"));
            }
            let row = GridRow {
                id,
                block,
                cells: vec![String::new(); self.block.columns.len()],
                placed: Vec::new(),
                trailing: Vec::new(),
            };
            self.next_row += 1;
            if !block.children.is_empty() {
                return self.cells(writer, row);
            }
            self.save_row(row)?;
        }
        let mut ordinary = compact_grid(&self.matrix, &self.block.column_alignments)?.into_iter();
        let indent = "\t".repeat(self.depth);
        for id in &self.block.children {
            let row = self
                .irregular
                .remove(id)
                .or_else(|| ordinary.next())
                .ok_or(TextError::Invalid("geometric grid row"))?;
            writer.lines.push(format!("{indent}{row}"));
            writer
                .lines
                .extend(self.annotations.remove(id).unwrap_or_default());
            writer.declarations(*id, self.depth, None);
        }
        Ok(())
    }

    fn cells(mut self, writer: &mut Writer<'a>, mut row: GridRow<'a>) -> Result<()> {
        let index = row.placed.len();
        let Some(&member) = row.block.children.get(index) else {
            self.save_row(row)?;
            return self.rows(writer);
        };
        let slot = row.block.column_slots[index];
        let context = Rc::clone(&self.context);
        writer.capture(member, self.depth, &context, move |writer, main, rest| {
            if let Some(slot) = slot {
                row.cells
                    .get_mut(slot)
                    .ok_or(TextError::Invalid("geometric grid column"))?
                    .clone_from(&main);
            }
            row.placed.push((slot, main));
            row.trailing.extend(rest);
            self.cells(writer, row)
        });
        Ok(())
    }
}

impl<'a> Writer<'a> {
    pub(super) fn write_visual_row(
        &mut self,
        children: &'a [BlockId],
        depth: usize,
        context: LayoutContext,
        mut cells: Vec<String>,
        mut trailing: Vec<String>,
    ) -> Result<()> {
        let Some((&child, children)) = children.split_first() else {
            if !cells.is_empty() {
                self.lines
                    .push(format!("{}{}", "\t".repeat(depth), cells.join(" | ")));
            }
            self.lines.extend(trailing);
            return Ok(());
        };
        let capture_context = Rc::clone(&context);
        self.capture(child, depth, &capture_context, move |writer, main, rest| {
            if !main.is_empty() {
                cells.push(main);
            }
            trailing.extend(rest);
            writer.write_visual_row(children, depth, context, cells, trailing)
        });
        Ok(())
    }

    pub(super) fn write_grid(
        &mut self,
        id: BlockId,
        block: &'a Block,
        mut depth: usize,
        mut context: LayoutContext,
    ) -> Result<()> {
        if let Some(columns) = self.options.grid_columns.get(&id) {
            let heading = format!("{}Visual grid ({columns} columns):", "\t".repeat(depth));
            self.lines.push(heading.clone());
            context = super::nested(&context, &block.key, &heading).into();
            depth += 1;
        }
        Grid {
            block,
            depth,
            context,
            next_row: 0,
            matrix: Vec::new(),
            irregular: HashMap::new(),
            annotations: HashMap::new(),
        }
        .rows(self)
    }
}
