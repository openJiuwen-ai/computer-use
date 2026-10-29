# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Optional timing projection; never an execution precondition or journal.

The existing native-input trace switch sends bounded JSON to stderr, where the
SDK's backend diagnostic reader already collects it. No wire fields or UI reads
are added, and neither clock nor sink failures may affect the native operation.
"""

import json
import logging
import os
import sys
import time
from contextlib import contextmanager


class ClickDiagnostics:
    def __init__(self, operation):
        self.operation = operation
        self.stages = []
        try:
            self.enabled = os.environ.get("COMPUTER_USE_TRACE_NATIVE_INPUT") == "1"
        except BaseException:
            self.enabled = False
        self.started = self._clock() if self.enabled else None

    @staticmethod
    def _clock():
        try:
            return time.perf_counter_ns()
        except BaseException:
            return None

    @contextmanager
    def stage(self, name):
        started = self._clock() if self.enabled else None
        error_type = None
        try:
            yield
        except BaseException as error:
            error_type = type(error).__name__
            raise
        finally:
            if self.enabled:
                try:
                    completed = self._clock()
                    self.stages.append(
                        dict(
                            name=name,
                            durationMs=(
                                max(0, completed - started) / 1_000_000
                                if completed is not None and started is not None
                                else None
                            ),
                            outcome="error" if error_type else "returned",
                            errorType=error_type,
                        )
                    )
                except BaseException:
                    pass

    def publish(self, plan, receipt):
        if not self.enabled:
            return
        try:
            completed = self._clock()
            coordinate_source = (
                None
                if plan is None
                else "object"
                if plan.delivery == "object_action"
                else "atspiComponent"
                if plan.contract.get("elementPointer")
                else "capture"
            )
            value = dict(
                kind="native_click_timing",
                operation=self.operation.token,
                step=self.operation.high_step,
                target=dict(self.operation.target),
                routeFamily=plan.route_family if plan else None,
                delivery=plan.delivery if plan else None,
                coordinateSource=coordinate_source,
                stages=self.stages,
                durationMs=(
                    max(0, completed - self.started) / 1_000_000
                    if completed is not None and self.started is not None
                    else None
                ),
            )
            if receipt is not None:
                value.update(
                    {
                        key: receipt[key]
                        for key in ("dispatch", "confirmation", "cleanup", "errorCode", "retryReason", "click")
                    }
                )
            # This structured diagnostic stream must retain its JSON Lines format.
            handler = logging.StreamHandler(sys.stderr)
            handler.handle(
                logging.LogRecord(
                    __name__,
                    logging.INFO,
                    __file__,
                    0,
                    json.dumps(dict(message=value), ensure_ascii=True, allow_nan=False, separators=(",", ":")),
                    (),
                    None,
                )
            )
        except BaseException:
            pass
