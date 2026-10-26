"""Native stage send_native_unit; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from computer_use_linux.contracts.protocol import Failure


def send_native_unit(obj, index, target_service, click_ledger=None):
    if click_ledger is not None:
        click_ledger.action_entered()
    ok = target_service.call("Action", "do_action", obj, index)
    if click_ledger is not None:
        click_ledger.action_returned(ok)
    if not ok:
        raise Failure("Native action did not confirm acceptance; inspect before continuing.", "execution_unknown")


CONTRACT = {
    "input": ("obj", "index", "target_service", "click_ledger"),
    "output": "original native stage result",
    "effects": ("DISPATCH",),
    "borrows": ("obj", "index", "target_service", "click_ledger"),
    "writes": ("optional click call journal",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
