"""Children paging must not silently drop a table's independent content graph.

The seam models raw provider responses, while collection, normalization and
model presentation are production code. Native cursor enforcement is covered
by the existing public client paging tests and the real table acceptance.
"""

import unittest
from types import SimpleNamespace

from observation_fixture import reply

from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.native.contracts import Binding
from computer_use.protocol.execution import decodeInspectQuery


class Provider:
    def __init__(self, *, supported=True, content_count=1):
        self.connection = SimpleNamespace(
            platform="macos", capabilities={"nativeNodePages", *(["nativeFollowRelations"] if supported else [])}
        )
        self.calls = []
        self.content_count = content_count

    async def nodes(self, operation, target, fields, max_nodes, max_depth, timeout, **options):
        query = dict(kind="nodes", fields=fields, maxNodes=max_nodes, maxDepth=max_depth)
        for snake, camel in (
            ("children_page", "childrenPage"),
            ("follow_relations", "followRelations"),
            ("advertised_fields", "advertisedFields"),
        ):
            if snake in options:
                query[camel] = options[snake]
        decodeInspectQuery(query)  # The raw-facts seam must accept only valid requests.
        self.calls.append(dict(options=options, max_nodes=max_nodes, max_depth=max_depth))
        followed = options.get("follow_relations", ())
        properties = ["AXChildren", *followed]
        count = min(self.content_count, max_nodes - 1) if "AXVisibleCells" in followed and max_depth > 0 else 0
        tokens = ["cell", "other-cell"][:count]
        raw = [("table", dict(AXRole="AXTable", AXTitle="Grid"))]
        for token in tokens:
            raw.append((token, dict(AXRole="AXCell", AXValue="visible-cell" if token == "cell" else token)))
        nodes = [
            dict(
                node=token,
                requestedFields=fields,
                values={k: v for k, v in values.items() if k in fields},
                fieldStatus={k: dict(state="unsupported") for k in fields if k not in values},
                actions=dict(state="complete", items=[]),
            )
            for token, values in raw
        ]
        relations = []
        for token, _ in raw:
            for prop in properties:
                edge = dict(source=token, property=prop, targets=[], state="complete", certainty="confirmed")
                if token == "table" and prop in ("AXVisibleCells", "AXRows"):
                    # Two independent membership edges refer to one native cell.
                    edge.update(
                        targets=tokens,
                        total=self.content_count,
                        offset=0,
                        state="complete" if count == self.content_count else "partial",
                    )
                    if count < self.content_count:
                        edge["cursor"] = "membership:" + prop + ":" + str(count)
                relations.append(edge)
        return reply(
            dict(
                kind="nodes",
                session=target.session,
                surface=target.surface,
                platform="macos",
                observed=dict(id="observation-" + str(len(self.calls)), startedAt=1, completedAt=2),
                roots=["table"],
                nodes=nodes,
                requestedRelations=properties,
                relations=relations,
                unread=[],
                stopReasons=[] if count == self.content_count or not followed else ["content node budget"],
            )
        )


class ElementContentProfileTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def read_context():
        return SimpleNamespace(
            target=Binding("session", "instance", "root", "surface", "table"), token="operation", remaining=lambda: 1000
        )

    async def test_default_profile_keeps_membership_for_first_and_continued_children_pages(self):
        port = Provider()
        reader = NativeReader(port)
        identities = []
        for page in (None, dict(cursor=None, limit=128), dict(cursor="children:128", limit=128)):
            with self.subTest(page=page):
                tree = await reader.collect(self.read_context(), children_page=page)
                self.assertIn("visible-cell", tree.render()["text"])
                self.assertEqual({n["node"] for n in tree.facts["nodes"]}, {"table", "cell"})
                self.assertEqual(sum(value == "cell" for value in tree.refs.values()), 1)
                children = next(
                    r for r in tree.facts["relations"] if r["source"] == "table" and r["kind"] == "children"
                )
                self.assertEqual(children["targets"], [], "Membership must not become containment")
                identities.append(next(ref for ref, node in tree.refs.items() if node == "cell"))
                self.assertEqual(port.calls[-1]["options"].get("children_page"), page)
        self.assertEqual(len(set(identities)), 1, "Read profiles retain the same native reference")

    async def test_paged_read_preserves_explicit_profiles_and_capability_gate(self):
        page = dict(cursor=None, limit=128)
        for supported, fields in ((False, None), (True, ["AXRole"])):
            with self.subTest(supported=supported, fields=fields):
                port = Provider(supported=supported)
                tree = await NativeReader(port).collect(self.read_context(), fields=fields, children_page=page)
                self.assertNotIn("follow_relations", port.calls[-1]["options"])
                self.assertNotIn("visible-cell", tree.render()["text"])
                self.assertEqual(port.calls[-1]["options"]["children_page"], page)

    async def test_paging_preserves_bounded_incomplete_content_instead_of_claiming_empty(self):
        port = Provider(content_count=2)
        tree = await NativeReader(port).collect(
            self.read_context(), children_page=dict(cursor=None, limit=1), max_nodes=2, max_depth=1
        )
        relation = next(r for r in tree.facts["relations"] if r["kind"] == "visible_cells")
        self.assertEqual(
            (relation["state"], relation["total"], relation["cursor"]), ("partial", 2, "membership:AXVisibleCells:1")
        )
        self.assertEqual(relation["targets"], ["cell"])
        self.assertEqual(len(tree.facts["nodes"]), 2)
        rendered = tree.render()["text"]
        self.assertIn("visible-cell", rendered)
        self.assertNotIn("other-cell", rendered)
        self.assertIn("visible_cells", rendered)
        self.assertIn('"state":"partial"', rendered)
        self.assertIn('"cursor":"membership:AXVisibleCells:1"', rendered)
        self.assertEqual((port.calls[-1]["max_nodes"], port.calls[-1]["max_depth"]), (2, 1))
