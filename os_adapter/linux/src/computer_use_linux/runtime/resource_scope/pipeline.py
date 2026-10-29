# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The sole operation Owner; resource lifetime still spans protocol frames."""

import time
from collections import OrderedDict

from .passes.acquire_native_resources import acquire_native_resources
from .passes.create_native_operation import create_native_operation
from .passes.publish_native_operation import publish_native_operation
from .passes.release_native_resources import release_native_resources


class NativeResourceScope:
    def __init__(self, *, store, coordinate, selection_factory):
        self.store, self.coordinate, self.selection_factory = store, coordinate, selection_factory
        self.current = None
        self.finished = OrderedDict()

    def current_view(self):
        return self.current

    def acquire(self, target, intent, writable, ctx, click_contract=None):
        operation = create_native_operation(
            target, intent, writable, ctx, lambda: self.selection_factory(self.current_view), click_contract
        )
        acquire_native_resources(operation, ctx, self.coordinate)
        return publish_native_operation(operation, target, ctx, self, self.store)

    def release_native_resources(self, operation):
        return release_native_resources(operation, self, self.store)

    def expire(self):
        if self.current and time.monotonic() >= self.current.deadline:
            self.release_native_resources(self.current.token)
