// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

//! Batch delivery work against the original call's checkpoint.

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct DeliveryInterrupted;

#[derive(Default)]
pub(crate) struct DeliveryBudget<'a> {
    checkpoint: Option<&'a mut dyn FnMut() -> Result<(), DeliveryInterrupted>>,
    completed: usize,
}

impl<'a> DeliveryBudget<'a> {
    pub(crate) fn new(
        checkpoint: Option<&'a mut dyn FnMut() -> Result<(), DeliveryInterrupted>>,
    ) -> Self {
        Self {
            checkpoint,
            completed: 0,
        }
    }

    pub(crate) fn step(&mut self) -> Result<(), DeliveryInterrupted> {
        if let Some(checkpoint) = &mut self.checkpoint {
            self.completed += 1;
            if self.completed == 128 {
                self.completed = 0;
                checkpoint()?;
            }
        }
        Ok(())
    }
}
