// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Heap-owned continuations for the geometric writer.

use std::rc::Rc;

use super::{BlockId, Context, Result, Writer};

pub(super) type LayoutContext = Rc<Context>;

type Continuation<'a> = Box<dyn FnOnce(&mut Writer<'a>) -> Result<()> + 'a>;

pub(super) enum Work<'a> {
    Block {
        id: BlockId,
        depth: usize,
        context: LayoutContext,
        inherited_prefix: bool,
    },
    Declarations {
        id: BlockId,
        depth: usize,
        at: usize,
    },
    Continue(Continuation<'a>),
}

impl<'a> Writer<'a> {
    pub(super) fn schedule(
        &mut self,
        id: BlockId,
        depth: usize,
        context: &LayoutContext,
        inherited_prefix: bool,
    ) {
        self.work.push(Work::Block {
            id,
            depth,
            context: Rc::clone(context),
            inherited_prefix,
        });
    }

    pub(super) fn children(
        &mut self,
        children: &[BlockId],
        depth: usize,
        context: &LayoutContext,
        inherited_prefix: bool,
    ) {
        for &id in children.iter().rev() {
            self.schedule(id, depth, context, inherited_prefix);
        }
    }

    pub(super) fn defer(&mut self, then: impl FnOnce(&mut Self) -> Result<()> + 'a) {
        self.work.push(Work::Continue(Box::new(then)));
    }

    pub(super) fn capture(
        &mut self,
        id: BlockId,
        depth: usize,
        context: &LayoutContext,
        then: impl FnOnce(&mut Self, String, Vec<String>) -> Result<()> + 'a,
    ) {
        let start = self.lines.len();
        self.defer(move |writer| {
            let (head, trailing) = writer.capture_lines(id, depth, start)?;
            then(writer, head, trailing)
        });
        self.schedule(id, depth, context, false);
    }

    pub(super) fn run(&mut self) -> Result<()> {
        self.children(&self.plan.roots, 0, &Rc::from([]), false);
        while let Some(work) = self.work.pop() {
            match work {
                Work::Block {
                    id,
                    depth,
                    context,
                    inherited_prefix,
                } => {
                    if self
                        .options
                        .declarations
                        .get(&id)
                        .is_some_and(|lines| !lines.is_empty())
                    {
                        self.work.push(Work::Declarations {
                            id,
                            depth,
                            at: self.lines.len() + 1,
                        });
                    }
                    self.write_block(id, depth, context, inherited_prefix)?;
                }
                Work::Declarations { id, depth, at } => self.declarations(id, depth, Some(at)),
                Work::Continue(then) => then(self)?,
            }
        }
        Ok(())
    }
}
