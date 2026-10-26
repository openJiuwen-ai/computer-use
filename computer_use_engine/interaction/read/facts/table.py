"""Pure table/selection presentation; no target resolution or native input."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.protocol.primitives import safeInteger

if TYPE_CHECKING:
    from typing import Mapping


def selection_summaries(native: FactCollection, refs: Mapping[str, str]) -> list[str]:
    """Describe the container's claim without changing any node's state.

    Args:
        native: Native execution port or unmodified provider facts for this domain.
        refs: Display-to-native identity map owned by the observation/session service.
    """
    aliases = {token: ref for ref, token in refs.items()}
    nodes = {node["node"]: node for node in native["nodes"]}
    labels = dict(
        selected_children="children", selected_cells="cells", selected_rows="rows", selected_columns="columns"
    )
    output = []
    for relation in native["relations"]:
        kind = relation["kind"]
        if kind not in labels or relation["state"] in ("absent", "unsupported"):
            continue
        details = []
        if relation["certainty"] != "confirmed":
            details.append("membership " + relation["certainty"])
        if relation["state"] != "complete":
            details.append(relation["state"])
        if "total" in relation:
            details.append(str(len(relation["targets"])) + " listed of " + str(relation["total"]))
        if relation.get("offset", 0):
            details.append("offset " + str(relation["offset"]))
        unread = [
            aliases[token]
            for token in relation["targets"]
            if token not in nodes or not nodes[token].get("observed", True)
        ]
        if unread:
            details.append("not expanded: " + ", ".join(unread))
        selected = "states.selected"
        conflicts = [
            aliases[token]
            for token in relation["targets"]
            if token in nodes
            and nodes[token]["values"].get(selected) is False
            and selected not in nodes[token]["fieldStatus"]
        ]
        if conflicts:
            details.append("node reports selected=false: " + ", ".join(conflicts))
        source = nodes[relation["source"]]
        count = source["values"].get("structure.selected_count")
        if (
            safeInteger(count)
            and count >= 0
            and "structure.selected_count" not in source["fieldStatus"]
            and "total" in relation
            and count != relation["total"]
        ):
            details.append("separate count reports " + str(count))
        targets = ", ".join(aliases[token] for token in relation["targets"])
        if not targets:
            targets = (
                "none"
                if relation["state"] == "complete" and relation["certainty"] == "confirmed"
                else "no confirmed items collected"
            )
        output.append(
            "Selected "
            + labels[kind]
            + " of "
            + aliases[relation["source"]]
            + ": "
            + targets
            + (" (" + "; ".join(details) + ")" if details else "")
        )
    return output


if TYPE_CHECKING:
    from computer_use.protocol.records import FactCollection
