"""Read-only AT-SPI focus discovery shared by observation and input guards.

Returns native objects with live parent-path evidence. Events and Collection
matches are candidates, not permissions to input. Unrelated subtrees are no
longer traversed after narrowing to a focused branch. This helper has no
preparation, dispatch, retry, or persistent recipient-state ownership.
"""

from computer_use_linux.contracts.protocol import Failure, require


class _InitialProbeBudget(Failure):
    """Discovery exhausted its own node budget, not the request."""


class NativeFocusConflict(Failure):
    """Known independent native objects both claim current focus."""


class NativeFocusDiscovery:
    SEARCH_NODES = 1024

    def __init__(self, ax, root, ctx):
        self.ax, self.root, self.ctx = ax, root, ctx
        self.objects, self.search_seen = {}, set()
        self.candidate_seen, self.source = False, "tree"

    def _key(self, obj):
        # GObject identity is native proxy identity. Keep every compared proxy
        # alive for this primitive: a released parent can otherwise be wrapped
        # again at a different address and look like an independent scope.
        key = self.ax.native_key(obj)
        self.objects[key] = obj
        return key

    def _hidden(self, obj):
        states = self._states(obj)
        return not (states.contains(self.ax.A.StateType.SHOWING) or states.contains(self.ax.A.StateType.VISIBLE))

    def focus_path(self, obj):
        path = self._path(obj)
        # Providers may leave FOCUSED on children of a closed popup or hidden
        # panel. SHOWING includes ancestor visibility in the AT-SPI contract;
        # VISIBLE keeps potentially visible/partly occluded scopes available.
        return None if any(self._hidden(self.objects[key]) for key in path) else path

    def _call(self, name, obj, *args):
        return self.ax.call(getattr(self.ax.A.Accessible, name), obj, *args)

    def _states(self, obj):
        self.ax.live(obj)
        states = self._call("get_state_set", obj)
        require(states is not None, "The native keyboard recipient state is unavailable.", "input_unavailable")
        return states

    def is_focused(self, obj):
        return self._states(obj).contains(self.ax.A.StateType.FOCUSED)

    def _path(self, obj):
        root_key = self._key(self.root)
        keys, current = [], obj
        for _ in range(128):
            if current is None:
                break
            self.ax.live(current)
            key = self._key(current)
            if key in keys:
                break
            keys.append(key)
            if key == root_key:
                return tuple(keys)
            current = self._call("get_parent", current)
        raise Failure("The keyboard recipient no longer belongs to the original working window.", "stale_target")

    def _collection(self):
        if "Collection" not in self._call("get_interfaces", self.root):
            return None
        a = self.ax.A
        states = a.StateSet()
        states.add(a.StateType.FOCUSED)
        match = a.CollectionMatchType.ALL
        rule = a.MatchRule.new(states, match, {}, match, [], match, [], match, False)
        # Request the first focused descendant. Looking for more
        # matches can exhaust huge virtual content when only one control is
        # focused. The result is a candidate; live state and scope still guard
        # delivery, and known independent focused objects remain conflicts.
        matches = self.ax.call(a.Collection.get_matches, self.root, rule, a.CollectionSortOrder.CANONICAL, 1, True)
        require(
            matches is not None and len(matches) <= 1,
            "The native focused-object query is incomplete.",
            "input_unavailable",
        )
        return [self.root, *matches]

    def _children(self, obj, count):
        for index in range(count):
            child = self._call("get_child_at_index", obj, index)
            require(child is not None, "The native focused-object search is incomplete.", "input_unavailable")
            yield child

    def _walk(self, root=None):
        pending, seen = [iter((self.root if root is None else root,))], set()
        while pending:
            try:
                obj = next(pending[-1])
            except StopIteration:
                pending.pop()
                continue
            key = self._key(obj)
            if key in seen:
                continue
            seen.add(key)
            self.search_seen.add(key)
            if len(self.search_seen) > self.SEARCH_NODES:
                raise _InitialProbeBudget(
                    "The native focused-object search exceeded its initial budget.", "input_unavailable"
                )
            self.ax.live(obj)
            if self._hidden(obj):
                continue
            yield obj
            count = self._call("get_child_count", obj)
            require(
                type(count) is int and 0 <= count <= 2147483647,
                "The native focused-object search is incomplete.",
                "input_unavailable",
            )
            # Fetch one sibling only when it is needed. Prefetching every
            # child can exhaust the probe before yielding even its first
            # focused editor, despite stopping unrelated subtrees later.
            pending.append(self._children(obj, count))

    def discover(self, *, probe=False, subtree=None, terminal=None):
        candidates = {}
        root_key = self._key(self.root)
        # Events are candidates, not state authority. A later notification
        # from an unfocused container must not erase the actual recipient.
        for event in reversed(tuple(getattr(self.ax, "focus_events", {}).values())):
            if self._key(event) == root_key:
                continue
            try:
                if self.is_focused(event):
                    path = self.focus_path(event)
                    if path is not None and self.is_focused(event):
                        candidates[self._key(event)] = event, path
                        self.candidate_seen = True
            except (Failure, self.ax.GLib.Error):
                self.ax.ctx.check()
        if candidates and not probe:
            self.source = "event"
            return self._unique(candidates)
        if candidates:
            self._unique(candidates)  # Known independent focus is already a conflict.
        try:
            sources = None if subtree is not None else self._collection()
        except self.ax.GLib.Error:
            self.ax.ctx.check()
            sources = None
        self.source = "tree" if sources is None else "collection"
        sources = self._walk(subtree) if sources is None else sources
        narrowed = set()
        try:
            while True:
                matched = False
                for obj in sources:
                    key = self._key(obj)
                    if key == root_key or not self.is_focused(obj):
                        continue
                    if key not in candidates:
                        try:
                            path = self.focus_path(obj)
                        except (Failure, self.ax.GLib.Error) as error:
                            raise Failure(
                                "The focused candidate has no confirmed working-window path: " + str(error),
                                "stale_target"
                                if isinstance(error, Failure) and error.code == "stale_target"
                                else "input_unavailable",
                            ) from error
                        if path is None:
                            continue
                        candidates[key] = obj, path
                        self.candidate_seen = True
                    matched = True
                    if terminal is not None and terminal(obj):
                        # A query may have enough evidence at this focused
                        # object. Its descendants are not completeness gates.
                        # Continuous-input callers retain the default search.
                        return self._unique(candidates)
                    if self.source == "tree" and key not in narrowed:
                        # Find the focused descendant as screen readers do,
                        # then narrow to that native subtree. Some toolkits
                        # mark both a focus scope and its actual editor; keep
                        # descending there, never resume the unrelated window
                        # census after a live recipient has been established.
                        narrowed.add(key)
                        sources = self._walk(obj)
                        break
                else:
                    if self.source == "collection" and not matched:
                        # A provider may return a retained focus in a closed
                        # popup as its first match. It is not the current
                        # recipient; use the same bounded, visibility-aware
                        # tree discovery rather than accepting that match.
                        self.source, sources = "tree", self._walk(subtree)
                        continue
                    break
        except NativeFocusConflict:
            raise
        except (Failure, self.ax.GLib.Error) as error:
            if candidates:
                raise Failure(
                    "The initial focused-object evidence became incomplete: " + str(error), "input_unavailable"
                ) from error
            raise
        # Some toolkits mark both a focus container and its descendant. Keep
        # the unique deepest live object only if every other candidate is on
        # its actual native parent chain; independent candidates are ambiguous.
        if not candidates:
            self.source = "x11"
        return self._unique(candidates)

    @staticmethod
    def _unique(candidates):
        deepest = [value for value in candidates.values() if set(candidates) <= set(value[1])]
        if candidates and len(deepest) != 1:
            raise NativeFocusConflict(
                "The window reports multiple independent keyboard recipients.", "input_unavailable"
            )
        return deepest[0] if deepest else (None, ())
