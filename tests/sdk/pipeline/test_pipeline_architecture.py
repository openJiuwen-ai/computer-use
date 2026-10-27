import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sdk.interaction.write.test_execution_engine import KEY, OBJECT, POINTER, READY, facts

from computer_use.interaction.write.routes.policy import (
    Attempts,
    has_independent_text_route,
    has_ready_route,
    needs_pointer_member,
)
from computer_use.interaction.write.routes.selection.pipeline import CandidateGenerationPipeline
from computer_use.native.contracts import Intent
from computer_use.pipeline import Contract, Control, Effect, Pass, Select, Sequence
from checks.check import architecture

"Architectural direction and a local extension rehearsal, independent of GUI."


class ArchitectureImportTests(unittest.TestCase):
    def project(self, **sources):
        directory = tempfile.TemporaryDirectory(prefix="v6-architecture-")
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        base = root / "computer_use_engine"
        (base / "protocol").mkdir(parents=True)
        (base / "native").mkdir(parents=True)
        (base / "native/semantics.py").write_text("from dataclasses import dataclass\n")
        for path, source in sources.items():
            file = base / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(source)
        return root

    @staticmethod
    def check(root):
        with redirect_stdout(io.StringIO()):
            architecture(root)

    def test_sdk_cannot_import_host_tool_or_extension_from_any_layer(self):
        cases = (
            ("client/consumer.py", "computer_use_mcp.runtime"),
            ("diagnostics.py", "computer_use_react_demo.ui"),
            ("interaction/query/consumer.py", "computer_use_decision.session"),
            ("native/query/consumer.py", "computer_use_native_tools.native"),
            ("session/consumer.py", "computer_use_experience.execution"),
            ("pipeline/consumer.py", "computer_use_devtools.check"),
        )
        for path, module in cases:
            with (
                self.subTest(path=path, module=module),
                self.assertRaisesRegex(ValueError, "SDK must not depend on external"),
            ):
                self.check(self.project(**{path: f"from {module} import consume\n"}))

    def test_extensions_can_consume_sdk_but_cannot_import_their_hosts(self):
        for directory in ("extensions/experience", "experiments/decision"):
            root = self.project()
            file = root / directory / "consumer.py"
            file.parent.mkdir(parents=True)
            file.write_text("from computer_use import Computer\n")
            self.check(root)
            for host in (
                "computer_use_mcp.journal",
                "computer_use_react_demo.cli",
                "computer_use_devtools.check",
            ):
                file.write_text(f"from {host} import consume\n")
                with (
                    self.subTest(component=directory, host=host),
                    self.assertRaisesRegex(ValueError, "must not depend on its host"),
                ):
                    self.check(root)

    def test_native_driver_sources_are_not_misclassified_as_sdk_modules(self):
        root = self.project(
            **{
                "backends/linux/src/computer_use_linux/pipeline.py": "import subprocess\nfrom computer_use_mcp import runtime\n",
                "_resources/example.py": "from computer_use_react_demo import cli\n",
            }
        )
        self.check(root)
        # The exact same source is forbidden if placed inside the SDK.
        (root / "computer_use_engine/client").mkdir()
        (root / "computer_use_engine/client/consumer.py").write_text("from computer_use_mcp import runtime\n")
        with self.assertRaisesRegex(ValueError, "SDK must not depend on external"):
            self.check(root)

    def test_static_runner_can_use_local_modules_and_generic_standard_library(self):
        self.check(
            self.project(
                **{
                    "pipeline/core.py": "from dataclasses import dataclass\nfrom .contracts import Contract\n",
                    "pipeline/contracts.py": "from enum import Enum\n",
                }
            )
        )

    def test_runner_rejects_domain_and_native_io_dependencies(self):
        for source in (
            "from ..native.contracts import Facts\n",
            "import computer_use.interaction.read as read\n",
            "import subprocess\n",
            "import AppKit\n",
        ):
            with (
                self.subTest(source=source),
                self.assertRaisesRegex(ValueError, "pipeline runner must remain domain-independent"),
            ):
                self.check(self.project(**{"pipeline/core.py": source}))

    def test_write_rejects_read_imports_at_every_depth_and_import_form(self):
        for source in (
            "from ....read.presentation.rendering.formatting import field\n",
            "import computer_use.interaction.read.presentation as read\n",
            "from .... import read\n",
            "def local():\n    from computer_use.interaction.read import facts\n",
        ):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "write must not depend on read"):
                self.check(self.project(**{"interaction/write/routes/selection/rule.py": source}))

    def test_display_rejects_execution_policy_and_runtime_reading(self):
        for source in (
            "from computer_use.interaction.write.routes.policy import choose\n",
            "from computer_use.interaction.read.acquisition import facts\n",
            "from computer_use.client.computer import Computer\n",
            "from computer_use.native.port import NativePort\n",
            "from computer_use.session.state import AppState\n",
        ):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "(presentation|interaction) must not"):
                self.check(self.project(**{"interaction/read/presentation/compression/rule.py": source}))

    def test_shared_records_pure_fact_helpers_and_explicit_launch_coordinator_are_allowed(self):
        self.check(
            self.project(
                **{
                    "interaction/read/presentation/contracts.py": (
                        "from computer_use.native.contracts import Facts\nfrom "
                        "computer_use.interaction.read.facts.table import "
                        "selection_summaries\n"
                    ),
                    "interaction/read/app_read/pipeline.py": "from computer_use.interaction.write.pipeline import WritePipeline\n",
                    "interaction/query/helpers.py": "from computer_use.native.query.path import TargetQuery\n",
                }
            )
        )

    def test_native_has_no_back_reference_to_consumers_or_session(self):
        for source in (
            "from computer_use.session.state import AppState\n",
            "from computer_use.interaction.read.model import AppObservation\n",
            "def local():\n    from computer_use.client import results\n",
        ):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "native must not depend"):
                self.check(self.project(**{"native/query/service.py": source}))

    def test_session_cannot_launch_flows_query_systems_or_import_public_wrappers(self):
        for source in (
            "from computer_use.interaction.read.pipeline import ReadPipeline\n",
            "from computer_use.native.query.directory import NativeDirectory\n",
            "from computer_use.native.connection import Connection\n",
            "from computer_use.client.computer import App\n",
        ):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "session must not"):
                self.check(self.project(**{"session/state.py": source}))
        self.check(
            self.project(
                **{
                    "session/state.py": (
                        "from computer_use.native.contracts import Binding\nfrom "
                        "computer_use.native.scopes import "
                        "WorkingWindow\n"
                    )
                }
            )
        )

    def test_obsolete_parallel_top_level_flows_are_rejected(self):
        for folder in ("execution", "observation"):
            with self.subTest(folder=folder), self.assertRaisesRegex(ValueError, "obsolete top-level branch"):
                self.check(self.project(**{folder + "/pipeline.py": "class Pipeline: pass\n"}))

    def test_leaf_pass_cannot_call_registered_sibling_or_start_a_runner(self):
        for body in (
            "return second(value, control)",
            'return Sequence("hidden").run(value)',
            'flow = Sequence("hidden")\n    return flow.run(value)',
        ):
            source = (
                "from computer_use.pipeline import Pass, Sequence, Contract\n"
                "def first(value, control):\n    " + body + "\n"
                "def second(value, control):\n    return value\n"
                'one = Pass("one", first, Contract(object, object))\n'
                'two = Pass("two", second, Contract(object, object))\n'
            )
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "a leaf Pass must not"):
                self.check(self.project(**{"interaction/write/routes/passes/rules.py": source}))

    def test_cross_file_leaf_import_and_hidden_runner_are_rejected(self):
        for body in (
            "from .other import execute\n",
            (
                "from ..pipeline import HiddenPipeline\ndef execute(value, "
                "control):\n    "
                "return HiddenPipeline().run(value)\n"
            ),
            (
                "from computer_use.pipeline import Sequence as Flow\ndef execute(value, "
                'control):\n    return Flow("hidden").run(value)\n'
            ),
            (
                "from computer_use.pipeline import Sequence\ndef create():\n    flow = "
                'Sequence("hidden")\n    def execute(value, control):\n        return '
                "flow.run(value, control)\n    return "
                "execute\n"
            ),
        ):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "a leaf Pass must not"):
                self.check(
                    self.project(
                        **{
                            "interaction/write/routes/passes/one.py": body,
                            "interaction/write/routes/passes/other.py": "def execute(value, control): return value\n",
                            "interaction/write/routes/pipeline.py": "class HiddenPipeline: pass\n",
                        }
                    )
                )

    def test_two_registered_leaves_cannot_share_a_pass_file(self):
        self.assertRaisesRegex(
            ValueError,
            "one registered Pass",
            self.check,
            self.project(
                **{
                    "interaction/write/routes/passes/pair.py": (
                        "def first(value, control): return value\ndef second(value, control): return value\n"
                    ),
                    "interaction/write/routes/pipeline.py": "from computer_use.pipeline import Pass, Contract\nfrom .passes.pair import first, second\n"
                    'one = Pass("one", first, Contract(object, object))\ntwo = Pass("two", second, Contract(object, object))\n',
                }
            ),
        )

    def test_one_leaf_can_have_a_private_helper_and_a_factory_closure(self):
        self.check(
            self.project(
                **{
                    "interaction/read/passes/one.py": (
                        "def _narrow(value): return value + 1\ndef create():\n    def "
                        "execute(value, "
                        "control): return _narrow(value)\n    return "
                        "execute\n"
                    ),
                    "interaction/read/pipeline.py": "from computer_use.pipeline import Pass, Contract\nfrom .passes.one import create\n"
                    'one = Pass("one", create(), Contract(object, object))\n',
                }
            )
        )

    def test_explicit_definitions_bind_inside_build_without_counting_a_callback_twice(self):
        self.check(
            self.project(
                **{
                    "interaction/read/passes/one.py": "from computer_use.pipeline import Pass as Leaf, Contract\n"
                    "def _narrow(value): return value + 1\n"
                    "def create(*, source):\n"
                    "    def execute(value, control): return _narrow(value) + source\n"
                    "    return execute\n"
                    'stage = Leaf.factory(name="one", factory=create, contract=Contract(int, int))\n',
                    "interaction/read/passes/two.py": "from computer_use.pipeline import Pass, Contract\n"
                    "def execute(value, control): return value\n"
                    'stage = Pass.define(name="two", function=execute, contract=Contract(int, int))\n',
                    "interaction/read/pipeline.py": "from computer_use.pipeline import Pass, Contract, Pipeline, Sequence\n"
                    "from .passes.one import create, stage as collect\n"
                    "from .passes.two import execute, stage as check\n"
                    "class ExamplePipeline(Pipeline):\n"
                    "    def __init__(self, source):\n"
                    "        self.source = source\n"
                    "        super().__init__(root=self.build())\n"
                    "    def build(self):\n"
                    '        return Sequence(name="example", steps=[collect(source=self.source), check()])\n'
                    'legacy_one = Pass("one", create(source=1), Contract(int, int))\n'
                    'legacy_two = Pass(name="two", function=execute, contract=Contract(int, int))\n',
                }
            )
        )

    def test_definitions_register_callbacks_and_factory_closures_for_leaf_call_checks(self):
        for first in (
            "def first(value, control): return second(value, control)\n"
            'one = Pass.define(name="one", function=first, contract=Contract(object, object))\n',
            "def create():\n"
            "    def execute(value, control): return second(value, control)\n"
            "    return execute\n"
            'one = Pass.factory(name="one", factory=create, contract=Contract(object, object))\n',
        ):
            with (
                self.subTest(source=first),
                self.assertRaisesRegex(ValueError, "a leaf Pass must not call another registered Pass"),
            ):
                self.check(
                    self.project(
                        **{
                            "interaction/read/passes/pair.py": "from computer_use.pipeline import Pass, Contract\n"
                            "def second(value, control): return value\n"
                            'two = Pass.define(name="two", function=second, contract=Contract(object, object))\n'
                            + first
                        }
                    )
                )

    def test_two_explicit_definitions_still_require_two_pass_files(self):
        for kind, callback in (("define", "function"), ("factory", "factory")):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "one registered Pass"):
                self.check(
                    self.project(
                        **{
                            "interaction/read/passes/pair.py": "from computer_use.pipeline import Pass as Leaf, Contract\n"
                            "def first(value, control): return value\n"
                            "def second(value, control): return value\n"
                            f'one = Leaf.{kind}(name="one", {callback}=first, contract=Contract(object, object))\n'
                            f'two = Leaf.{kind}(name="two", {callback}=second, contract=Contract(object, object))\n'
                        }
                    )
                )

    def test_definitions_cannot_hide_implementations_in_build_or_flow_scope(self):
        for body in (
            "def execute(value, control): return value\n"
            'one = Pass.define(name="one", function=execute, contract=Contract(object, object))\n',
            (
                'one = Pass.define(name="one", function=lambda value, control: '
                "value + 1, "
                "contract=Contract(object, "
                "object))\n"
            ),
            "class ExamplePipeline(Pipeline):\n"
            "    def build(self):\n"
            "        def create():\n"
            "            def execute(value, control): return value\n"
            "            return execute\n"
            '        one = Pass.factory(name="one", factory=create, contract=Contract(object, object))\n'
            "        return one()\n",
        ):
            with (
                self.subTest(source=body),
                self.assertRaisesRegex(ValueError, "(implementation must live|must live in its own)"),
            ):
                self.check(
                    self.project(
                        **{
                            "interaction/read/pipeline.py": "from computer_use.pipeline import Pass, Contract, Pipeline\n"
                            + body
                        }
                    )
                )

    def test_new_facade_and_bound_definitions_cannot_start_runners_inside_leaves(self):
        for body in (
            'return Pipeline(root=Sequence(name="hidden", steps=[])).run(value)',
            ('flow = Pipeline(root=Sequence(name="hidden", steps=[]))\n    return flow.arun(value)'),
            "return hidden().run(value)",
            "flow = hidden()\n    return flow.run(value)",
        ):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "a leaf Pass must not start"):
                self.check(
                    self.project(
                        **{
                            "interaction/read/passes/one.py": "from computer_use.pipeline import Pass, Contract, Pipeline, Sequence\n"
                            "def execute(value, control):\n    " + body + "\n"
                            'hidden = Pass.define(name="one", function=execute, contract=Contract(object, object))\n'
                        }
                    )
                )

    def test_namespace_and_direct_definition_forms_cannot_bypass_leaf_registration(self):
        declarations = (
            (
                "from computer_use import pipeline as flows\n",
                ('flows.Pass.define(name="one", function=first, contract=flows.Contract(object, object))'),
                ('flows.Pass.define(name="two", function=second, contract=flows.Contract(object, object))'),
            ),
            (
                "from computer_use import pipeline as flows\n",
                ('flows.Pass.factory(name="one", factory=first, contract=flows.Contract(object, object))'),
                ('flows.Pass.factory(name="two", factory=second, contract=flows.Contract(object, object))'),
            ),
            (
                "from computer_use.pipeline import PassDefinition as Definition, Contract\n",
                'Definition(name="one", factory=first, contract=Contract(object, object))',
                'Definition("two", second, Contract(object, object))',
            ),
        )
        for imports, one, two in declarations:
            with (
                self.subTest(one=one),
                self.assertRaisesRegex(ValueError, "a leaf Pass must not call another registered Pass"),
            ):
                self.check(
                    self.project(
                        **{
                            "interaction/read/passes/pair.py": imports
                            + "def first(value, control): return second(value, control)\n"
                            "def second(value, control): return value\n"
                            f"one = {one}\ntwo = {two}\n"
                        }
                    )
                )

    def test_new_definition_forms_cannot_hide_inline_implementations(self):
        for declaration in (
            (
                'flows.Pass.define(name="inline", function=lambda value, control: '
                "value, "
                "contract=flows.Contract(object, "
                "object))"
            ),
            ('flows.PassDefinition(name="inline", factory=lambda: None, contract=flows.Contract(object, object))'),
        ):
            with (
                self.subTest(declaration=declaration),
                self.assertRaisesRegex(ValueError, "inline Pass implementation must live"),
            ):
                self.check(
                    self.project(
                        **{
                            "interaction/read/pipeline.py": "from computer_use import pipeline as flows\n"
                            f"stage = {declaration}\n"
                        }
                    )
                )

    def test_qualified_callbacks_still_count_their_actual_implementation_file(self):
        self.assertRaisesRegex(
            ValueError,
            "one registered Pass",
            self.check,
            self.project(
                **{
                    "interaction/read/passes/pair.py": "def first(value, control): return value\n"
                    "def second(value, control): return value\n",
                    "interaction/read/pipeline.py": "from computer_use import pipeline as flows\n"
                    "from .passes import pair\n"
                    'one = flows.Pass.define(name="one", function=pair.first, contract=flows.Contract(object, object))\n'
                    'two = flows.Pass.define(name="two", function=pair.second, contract=flows.Contract(object, object))\n',
                }
            ),
        )
        for imports, reference in (
            ("from . import support\n", "support.execute"),
            ("import computer_use.interaction.read.support as helpers\n", "helpers.execute"),
            ("import computer_use.interaction.read.support\n", "computer_use.interaction.read.support.execute"),
        ):
            with self.subTest(imports=imports), self.assertRaisesRegex(ValueError, "implementation must live"):
                self.check(
                    self.project(
                        **{
                            "interaction/read/support.py": "def execute(value, control): return value\n",
                            "interaction/read/pipeline.py": "from computer_use.pipeline import Pass, Contract\n"
                            + imports
                            + (
                                f'one = Pass.define(name="one", function='
                                f"{reference}"
                                f", contract=Contract(object, object))\n"
                            ),
                        }
                    )
                )

    def test_new_leaf_implementation_cannot_hide_in_flow_file(self):
        for body in (
            ('def execute(value, control): return value\none = Pass("one", execute, Contract(object, object))\n'),
            'one = Pass("one", lambda value, control: value + 1, Contract(object, object))\n',
            (
                "class Flow:\n    def __init__(self):\n        def execute(value, "
                "control): "
                'return value\n        self.one = Pass("one", execute, '
                "Contract(object, "
                "object))\n"
            ),
        ):
            with (
                self.subTest(body=body),
                self.assertRaisesRegex(ValueError, "(implementation must live|must live in its own)"),
            ):
                self.check(
                    self.project(
                        **{"interaction/read/pipeline.py": "from computer_use.pipeline import Pass, Contract\n" + body}
                    )
                )

    def test_an_ir_editor_method_named_run_is_not_mistaken_for_a_pipeline(self):
        self.check(
            self.project(
                **{
                    "interaction/read/presentation/compression/passes/rule.py": "from computer_use.pipeline import Pass, Contract\n"
                    "def apply_rule(value, control):\n    return value.edit.run(value.rule)\n"
                    'node = Pass("rule", apply_rule, Contract(object, object))\n'
                }
            )
        )


class LocalExtensionTests(unittest.IsolatedAsyncioTestCase):
    def test_resolver_predicates_share_candidate_phases_without_a_nested_runner(self):
        with patch.object(CandidateGenerationPipeline, "run", side_effect=AssertionError("leaf started a runner")):
            self.assertTrue(has_independent_text_route(facts(OBJECT), Intent("replace")))
            self.assertTrue(has_ready_route(facts(KEY, context=READY), Intent("insert")))
            self.assertTrue(needs_pointer_member(facts(POINTER), Intent("click"), Attempts()))

    def test_new_pure_branch_is_local_and_unselected_siblings_remain_unexecuted(self):
        history = []
        original = Select(
            "RouteChoice",
            Pass("select", lambda value, _: "native", Contract(int, str)),
            {
                "native": Pass("native_rule", lambda value, _: value + 1, Contract(int, int)),
                "sibling": Pass("unselected", lambda *_: self.fail("unselected sibling ran"), Contract(int, int)),
            },
        )
        # A new rule and one registration compose a new graph. Neither the old
        # graph, an adjacent branch nor persistent state gains another owner.
        extended = Select(
            "RouteChoice",
            Pass("select", lambda value, _: "extended", Contract(int, str)),
            {
                **original.branches,
                "extended": Sequence(
                    "ExtendedRoute",
                    Pass("new_rule", lambda value, _: value * 2, Contract(int, int)),
                    original.branches["native"],
                ),
            },
        )
        self.assertEqual(extended.run(3, Control(diagnostic=history.append)), 7)
        self.assertEqual(original.run(3), 4)
        self.assertEqual(set(original.branches), {"native", "sibling"})
        self.assertEqual(set(extended.branches) - set(original.branches), {"extended"})
        self.assertTrue(all(event.effects == frozenset({Effect.PURE}) for event in history))
        self.assertFalse(any(event.path[-1] == "unselected" for event in history))

    async def test_additional_write_branch_reuses_native_unit_without_changing_siblings(self):
        from types import SimpleNamespace

        from sdk.interaction.write.test_execution_engine import result
        from sdk.interaction.write.test_write_pipeline_contracts import resolved

        from computer_use.interaction.write.model import RoutePlan
        from computer_use.interaction.write.progress import WriteRunState
        from computer_use.interaction.write.routes.pipeline import RouteExecutionPipeline

        owner, sibling, commands = WriteRunState(), WriteRunState(), []

        async def execute(operation, step, command, budget):
            commands.append(command)
            return result()

        async def never(*_):
            self.fail("the sibling native branch ran")

        primary = RouteExecutionPipeline(SimpleNamespace(execute=execute, calls=SimpleNamespace(retired=False)), owner)
        unused = RouteExecutionPipeline(SimpleNamespace(execute=never, calls=SimpleNamespace(retired=False)), sibling)

        def require_collected_basis(plan, _):
            if not plan.resolved.target.basis:
                raise ValueError("new route needs explicit collected basis")
            return plan

        # This extension adds one guard and one branch registration. Its native
        # unit, dispatch barrier, resource scope and sibling state are reused.
        extended = Select(
            "ExtendedWriteRoute",
            Pass("choose_extension", lambda *_: "checked", Contract(RoutePlan, str)),
            {
                "checked": Sequence(
                    "CheckedObjectRoute",
                    Pass("require_basis", require_collected_basis, Contract(RoutePlan, RoutePlan)),
                    primary,
                ),
                "unchanged": unused,
            },
        )
        source = resolved()
        plan = RoutePlan(source, source.target.facts.primitives[0], False, False)
        outcome = await extended.arun(plan)
        self.assertEqual(len(commands), 1)
        self.assertIs(outcome.command.target, source.bound.spec.target)
        self.assertEqual(sibling.view(), WriteRunState().view())
        self.assertFalse(owner.in_flight or sibling.in_flight)
        bad = replace(plan, resolved=replace(source, target=replace(source.target, basis=())))
        with self.assertRaisesRegex(ValueError, "explicit collected basis"):
            await extended.arun(bad)
        self.assertEqual(len(commands), 1, "a refused local branch must not enter native input")


if __name__ == "__main__":
    unittest.main()
