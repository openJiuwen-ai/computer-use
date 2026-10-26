"""One addressed AT-SPI action; semantic selection belongs to the SDK.

Action indices are transient provider positions, never public element IDs or
persistent action identities. Resolve the exact declared name again at dispatch.
"""

from copy import deepcopy

from computer_use_linux.contracts.protocol import Failure, fields, integer, require, string
from computer_use_linux.control.native_target import NativeTarget


def action_intent(value):
    fields(value, ("kind", "parameters"))
    kind = value["kind"]
    if kind == "click":
        p = fields(value["parameters"], (), ("button", "count"))
        if "button" in p:
            require(p["button"] in ("left", "right", "middle"), "Invalid click button.")
        if "count" in p:
            integer(p["count"], 1, 2)
        require(
            p.get("button", "left") == "left" and p.get("count", 1) == 1,
            "This native action unit does not synthesize pointer clicks.",
            "mode_unsupported",
        )
    elif kind == "action":
        p = fields(value["parameters"], ("action",))
        require(len(string(p["action"])) <= 16384, "Native action name or description is too long.")
    elif kind == "openMenu":
        fields(value["parameters"], ())
    else:
        raise Failure("This Linux v5 driver does not implement this input intent.", "mode_unsupported")
    return deepcopy(value)


def action_matches(catalogue, selected):
    """Exact native identity, including description when it was observed.

    Names may be duplicated by a provider. An explicit unique description can
    distinguish them; an unread description cannot be assumed to differ.
    """
    if catalogue["state"] != "complete":
        return []
    matches = [(i, a) for i, a in enumerate(catalogue["items"]) if a["name"] == selected["name"]]
    if "description" in selected:
        if any(a.get("descriptionStatus", {}).get("state") not in (None, "absent") for _, a in matches):
            return []
        matches = [(i, a) for i, a in matches if a.get("description") == selected["description"]]
    return [i for i, _ in matches]


class NativeAction:
    def __init__(self, backend, store, directory, facts):
        self.ax, self.store, self.facts = backend.ax, store, facts
        self.target = NativeTarget(backend, store, directory)

    def execute(self, operation, command, ctx):
        from .action.pipeline import NativeActionPipeline

        return NativeActionPipeline(self.ax, self.target, self.facts).run(operation, command, ctx)

    def inspect(self, operation, target, within, ctx):
        obj, _, _, role, enabled = self.target.read(target, within, ctx)
        operation.bind(target, within, self.store)
        catalogue = self.facts.action_catalogue(obj, ctx)
        complete = catalogue["state"] == "complete"
        primitives = []
        if complete:
            for action in catalogue["items"]:
                selected = dict(name=action["name"])
                if "description" in action and "descriptionStatus" not in action:
                    selected["description"] = action["description"]
                identity = dict(name=action["name"])
                if (
                    operation.intent["kind"] == "action"
                    and operation.intent["parameters"]["action"] != action["name"]
                    and "description" in selected
                ):
                    identity["description"] = selected["description"]
                # Ordinary click/named action does not depend on descriptive
                # text. Bind it only when the request actually uses that text.
                pointer_only = operation.intent["kind"] == "click" and (
                    operation.intent["parameters"].get("button", "left") != "left"
                    or operation.intent["parameters"].get("count", 1) != 1
                )
                usable = not pointer_only and len(action_matches(catalogue, identity)) == 1
                identifier = operation.contract(
                    "native_action", target, within, action=selected, identity=identity, role=role, usable=usable
                )
                # Preserve ambiguous declarations as unsupported facts. Dropping
                # them would falsely tell Python that a primary action is absent.
                primitives.append(
                    dict(
                        id=identifier,
                        unit="native_action",
                        channel="object",
                        supported="yes" if usable else "no",
                        readiness="yes" if enabled and usable else "no",
                        pending=[] if enabled else ["enabled"],
                        backgroundPreparation="no",
                        action=selected,
                    )
                )
        return self.target.context(target, within, primitives, enabled, role, actions_complete=complete)
