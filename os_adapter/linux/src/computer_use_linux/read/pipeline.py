"""Native query composition. The Runtime injects only query capabilities."""

from computer_use_linux.contracts.protocol import Failure

from .passes.begin_query_provider import begin_query_provider
from .passes.check_query_boundary import check_query_boundary
from .passes.collect_applications import collect_applications
from .passes.collect_captured_text import collect_captured_text
from .passes.collect_launch_facts import collect_launch_facts
from .passes.collect_nodes import collect_nodes
from .passes.collect_relation import collect_relation
from .passes.collect_surfaces import collect_surfaces
from .passes.encode_native_facts import encode_native_facts
from .passes.register_observed_objects import register_observed_objects
from .passes.resolve_query_scope import resolve_query_scope
from .passes.validate_element_scope import validate_element_scope


class NativeQueryPipeline:
    def __init__(
        self,
        *,
        session,
        current,
        ax,
        directory,
        facts,
        text_page,
        launch_inspect,
        context,
        finish,
        decode_binding,
        decode_query,
        binding_keys,
    ):
        self.session, self.current, self.ax = session, current, ax
        self.directory, self.facts, self.text_page = directory, facts, text_page
        self.launch_inspect, self.context, self.finish = launch_inspect, context, finish
        self.decode_binding, self.decode_query, self.binding_keys = decode_binding, decode_query, binding_keys

    def run(self, params, ctx):
        scope = resolve_query_scope(params, self.session, self.current(), self.decode_binding, self.decode_query)
        check_query_boundary(scope, self.binding_keys)
        ctx.deadline = min(ctx.deadline, scope.operation.deadline)
        try:
            begin_query_provider(scope, ctx, self.ax)
            if scope.launch:
                result = collect_launch_facts(scope, ctx, self.launch_inspect)
            elif scope.kind == "inputContext":
                result = self.context.run(scope, ctx)
            elif scope.directory:
                if scope.kind == "applications":
                    result = collect_applications(ctx, self.directory)
                elif scope.kind == "surfaces":
                    result = collect_surfaces(scope, ctx, self.directory)
                else:
                    result = collect_captured_text(scope, self.session, self.text_page)
            else:
                validate_element_scope(scope, ctx, self.directory)
                result = (
                    collect_nodes(scope, ctx, self.facts)
                    if scope.kind == "nodes"
                    else collect_relation(scope, ctx, self.facts)
                )
            register_observed_objects(scope, result)
            return encode_native_facts(result, ctx)
        except Failure as error:
            if error.code in ("cancelled", "deadline_exceeded"):
                self.finish(scope.operation.token)
            raise
