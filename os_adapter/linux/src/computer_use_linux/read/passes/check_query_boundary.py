# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PURE: check_query_boundary; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""

from computer_use_linux.contracts.protocol import require


def check_query_boundary(scope, binding_keys):
    operation, target = scope.operation, scope.target
    kind, launch, directory = scope.kind, scope.launch, scope.directory
    require(
        operation is not None and operation.token == scope.operation_token, "Native operation expired.", "stale_target"
    )
    require(
        launch == ("application" in operation.target)
        and (
            target == operation.target
            if launch
            else all(target[key] == operation.target[key] for key in binding_keys if key != "node")
        ),
        "Observation cannot change the active native scope.",
        "stale_target",
    )
    require(launch == (kind == "launch"), "Launch query requires the installation scope.")
    require(
        launch
        or (kind == "inputContext" and (target["surface"] is not None or target["capture"] is not None))
        or (
            target["capture"] is None
            and (target["surface"] is None) == directory
            and (directory or target["node"] is not None)
        ),
        "Inspect query and target scope disagree.",
    )


CONTRACT = {
    "input": ("scope", "binding_keys"),
    "output": "original native stage result",
    "effects": ("PURE",),
    "borrows": ("scope", "binding_keys"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
