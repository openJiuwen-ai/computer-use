"""Issue lookup preserves capture order while avoiding cross-node rescans."""

import random
import unittest

from computer_use.interaction.read.facts.index import _IssueIndex
from computer_use.protocol.observation import ReadIssue


class CountingIssues:
    def __init__(self, items):
        self.items = items
        self.visits = 0

    def __iter__(self):
        for item in self.items:
            self.visits += 1
            yield item


class IssueIndexTests(unittest.TestCase):
    def test_prefix_restrictions_preserve_original_order_and_scope(self):
        issues = (
            ReadIssue(("nodes", "a", "text", "content"), "partial"),
            ReadIssue((), "error"),
            ReadIssue(("nodes", "a"), "stale"),
            ReadIssue(("nodes", "a", "text"), "timeout"),
            ReadIssue(("nodes",), "partial"),
            ReadIssue(("nodes", "b"), "unavailable"),
            ReadIssue(("nodes", "a", "text", "content"), "error"),
        )
        index = _IssueIndex(issues)
        self.assertEqual(
            index.applicable(("nodes", "a", "text", "content")), [issues[0], issues[2], issues[3], issues[6]]
        )
        self.assertEqual(index.applicable(("nodes", "a", "actions")), [issues[2]])
        self.assertEqual(index.applicable(("nodes", "c", "name")), [])
        self.assertEqual(index.applicable(()), [issues[1]])
        self.assertEqual(index.applicable(("nodes",)), [issues[4]])

    def test_mixed_paths_match_linear_reference_semantics(self):
        randomizer = random.Random(42)
        paths = [(), ("nodes",)] + [
            ("nodes", str(node), *field)
            for node in range(8)
            for field in (
                (),
                ("name",),
                ("text",),
                ("text", "selections"),
                ("text", "selections", 0),
                ("relations", "a.b"),
            )
        ]
        issues = tuple(
            ReadIssue(randomizer.choice(paths), randomizer.choice(("partial", "error", "absent"))) for _ in range(200)
        )
        index = _IssueIndex(issues)
        for path in paths:
            expected = [
                issue
                for issue in issues
                if path[: len(issue.path)] == issue.path and (issue.path == path or len(issue.path) >= 2)
            ]
            self.assertEqual(index.applicable(path), expected)

    def test_repeated_field_queries_do_not_retraverse_unrelated_issues(self):
        issues = CountingIssues(
            [
                ReadIssue(("nodes", str(node), "field", field), "unsupported")
                for node in range(300)
                for field in range(30)
            ]
        )
        index = _IssueIndex(issues)
        self.assertEqual(issues.visits, 9000)
        for node in range(300):
            for field in range(30):
                self.assertEqual(len(index.applicable(("nodes", str(node), "field", field))), 1)
        self.assertEqual(issues.visits, 9000, "Each observation issue is indexed once, not scanned per field")
        self.assertEqual(len(index.nodes["42"]), 30)


if __name__ == "__main__":
    unittest.main()
