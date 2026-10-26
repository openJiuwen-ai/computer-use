"""RESOURCE: create_native_operation; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""

from computer_use_linux.runtime.native_operation import NativeOperation


def create_native_operation(target, intent, writable, ctx, selection_factory, click_contract=None):
    operation = NativeOperation(target, ctx.deadline, intent, writable)
    operation.click_contract = click_contract
    if writable:
        operation.selection_facts = selection_factory()
    return operation


CONTRACT = {
    "input": ("target", "intent", "writable", "ctx", "selection_factory", "click_contract"),
    "output": "original native stage result",
    "effects": ("RESOURCE",),
    "borrows": ("target", "intent", "writable", "ctx", "selection_factory", "click_contract"),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
