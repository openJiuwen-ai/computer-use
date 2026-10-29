# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native keyboard preparation composition; no requested content is sent."""

from .passes.observe_preparation import observe_preparation
from .passes.prepare_foreground import prepare_foreground
from .passes.prepare_native_receiver import prepare_native_receiver


class NativePreparePipeline:
    def __init__(self, guard, route_key, desktop, target, mode):
        self.guard, self.route_key, self.desktop, self.target, self.mode = guard, route_key, desktop, target, mode

    def run(self, operation, contract, foreground, ctx, preparation):
        # NativePreparePipeline: resolve/check -> optional activation -> addressed
        # focus -> bounded observation -> receipt. The request's content is never
        # sent here. Each wait retains the original operation deadline.
        r = self.guard(contract, ctx)
        if r["ready"]:
            return True
        self.desktop.require_idle_input()
        key = self.route_key(contract["target"], contract["within"], contract["role"], contract.get("selection"))
        r = prepare_foreground(
            operation, contract, foreground, ctx, preparation, r, key, self.mode, self.desktop, self.guard
        )
        prepare_native_receiver(r, ctx, preparation, self.desktop, self.target)
        if observe_preparation(contract, ctx, self.guard):
            return True
        if not foreground:
            operation.background_unavailable.add(key)
        return False
