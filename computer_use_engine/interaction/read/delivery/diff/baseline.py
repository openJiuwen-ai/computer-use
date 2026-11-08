"""Small immutable comparison facts; no retained native captures or rendering IR."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from functools import cached_property
from hashlib import sha256
from typing import TYPE_CHECKING

from computer_use.identity import WeakIdentityMap
from computer_use.interaction.read.presentation.document import DisplayTree
from computer_use.interaction.read.presentation.sizing import DocumentSize

if TYPE_CHECKING:
    from collections.abc import Hashable

    from computer_use.interaction.read.presentation.document import DisplayDocument
    from computer_use.interaction.read.presentation.preparation import PreparedNode


STATE_FIELDS = frozenset(
    (
        "enabled",
        "selected",
        "focused",
        "busy",
        "toggleState",
        "expanded",
        "disclosing",
        "visible",
        "showing",
        "modal",
        "_boundsOutsideWindow",
    )
)

_comparison_trees = WeakIdentityMap()


class _Signatures:
    """Hash structured facts, sharing work for immutable layout records.

    Baselines need equality and containment, not retained copies of every table
    layout on every cell. Type tags and sorted mapping keys make signatures
    independent of mapping insertion order without parsing rendered text.
    """

    def __init__(self, checkpoint=None):
        self.memo = {}
        self.checkpoint, self._steps = checkpoint, 0

    def of(self, value):
        self._steps += 1
        if self.checkpoint is not None and self._steps % 128 == 1:
            self.checkpoint()
        if isinstance(value, (Mapping, tuple, list, set, frozenset)):
            cached = self.memo.get(id(value))
            if cached is not None and cached[0] is value:
                return cached[1]
            if isinstance(value, Mapping):
                entries = sorted((self.of(key), self.of(item)) for key, item in value.items())
                encoded = b"mapping" + b"".join(key + item for key, item in entries)
            elif isinstance(value, (set, frozenset)):
                encoded = b"set" + b"".join(sorted(self.of(item) for item in value))
            else:
                encoded = b"sequence" + b"".join(self.of(item) for item in value)
            digest = sha256(encoded).digest()
            self.memo[id(value)] = (value, digest)
            return digest
        return sha256((type(value).__name__ + ":" + repr(value)).encode("utf-8", "surrogatepass")).digest()


@dataclass(frozen=True)
class ComparisonNode:
    ref: str
    parent: str | None
    depth: int
    signature: str
    content: str

    @classmethod
    def capture(cls, node: PreparedNode, signatures: _Signatures):
        # preserveGroup is a compression barrier. Its visible consequences are
        # already represented by containment/layout and current read notes.
        # Toggling the barrier alone does not change a displayed element.
        fields = {key: value for key, value in node.fields.items() if key != "preserveGroup"}
        return cls(
            node.ref,
            node.parent,
            node.depth,
            signatures.of(fields).hex(),
            signatures.of({key: value for key, value in fields.items() if key not in STATE_FIELDS}).hex(),
        )


@dataclass(frozen=True)
class ComparisonTree:
    scope: tuple[Hashable, ...]
    nodes: tuple[ComparisonNode, ...]
    details: bool
    compact: bool
    public_api: bool
    root_contexts: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    versions: tuple[tuple[str, object], ...]
    failed: bool

    @classmethod
    def capture(cls, tree: DisplayTree, *, checkpoint=None):
        # Planning and baseline publication consume the same frozen DisplayTree.
        # Cache its small hash/ref record, never the native capture or a render.
        # WeakIdentityMap checks the live key object, including after id reuse.
        cached = _comparison_trees.get(tree)
        if cached is not None:
            return cached
        prepared = tree.prepared
        signatures = _Signatures(checkpoint)
        comparison = cls(
            tree.scope,
            tuple(ComparisonNode.capture(node, signatures) for node in tree.nodes),
            tree.details,
            prepared.request.compact,
            prepared.request.public_api,
            tuple(prepared.snapshot.root_contexts.items()),
            tuple(
                (root.root_ref, root.interface.format_version if root.interface else None) for root in prepared.roots
            ),
            any(root.failure is not None for root in prepared.roots),
        )
        _comparison_trees.set(tree, comparison)
        return comparison


@dataclass(frozen=True)
class ComparisonDocument:
    trees: tuple[ComparisonTree, ...]

    @classmethod
    def capture(cls, document: DisplayDocument, *, checkpoint=None):
        return cls(tuple(
            ComparisonTree.capture(part, checkpoint=checkpoint)
            for part in document.parts if isinstance(part, DisplayTree)
        ))

    @cached_property
    def estimated_bytes(self):
        return DocumentSize().data(self)

    def to_data(self):
        return asdict(self)

    @classmethod
    def from_data(cls, data):
        from computer_use.interaction.read.presentation.preparation import freeze

        return cls(
            tuple(
                ComparisonTree(
                    tuple(tree["scope"]),
                    tuple(ComparisonNode(**node) for node in tree["nodes"]),
                    tree["details"],
                    tree["compact"],
                    tree["public_api"],
                    freeze(tree["root_contexts"]),
                    freeze(tree["versions"]),
                    tree["failed"],
                )
                for tree in data["trees"]
            )
        )
