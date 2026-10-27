"""Shared immutable projection fixtures; no discoverable TestCase objects."""

from computer_use.interaction.read.presentation.ir import (
    PresentationIR,
    ProjectionEditor,
    ProjectionInput,
    ProjectionView,
    validate_projection,
)


def n(ref, role, *children, **fields):
    return dict(ref=str(ref), role=role, children=list(children), **fields)


class FixtureProjection(ProjectionView):
    def __init__(self, root, context):
        self.ir = PresentationIR.build(ProjectionInput.build(root, context))
        super().__init__(self.ir)
        self.edit = ProjectionEditor(self.ir)

    def run(self, step, *args):
        return self.edit.run(step, self, *args)

    def assert_integrity(self):
        validate_projection(self.ir)


def state(root, **context):
    return FixtureProjection(root, dict(includeBackground=True, recordStages=True, **context))
