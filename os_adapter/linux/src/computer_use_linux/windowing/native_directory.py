# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native application/window facts for v5. Window selection stays in the SDK."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, NamedTuple

from computer_use_linux.contracts.observation import stamp
from computer_use_linux.contracts.protocol import Failure, gap, now, token
from computer_use_linux.windowing.desktop import process_start
from computer_use_linux.windowing.native_installation import NativeInstallation, window_class_keys
from computer_use_linux.windowing.process_identity import process_birth


class DirectoryRead(NamedTuple):
    """Application catalogue, window membership, and discovery limitations."""

    applications: dict[str, Any]
    members: Any
    windows: list[Any]
    notes: list[str]


class NativeDirectory:
    def __init__(self, backend, store, session):
        self.b, self.store, self.session = backend, store, session
        self.ids = OrderedDict()
        self.surfaces = OrderedDict()
        self.installations = OrderedDict()
        self.launched = OrderedDict()

    @staticmethod
    def text(value, notes, kind, *, placeholder=""):
        if not isinstance(value, str):
            gap(notes, kind + " did not provide a string.")
            return placeholder
        if len(value) > 4096:
            gap(notes, kind + " exceeds 4096 characters; the directory contains a preview.")
        return value[:4096] if value.strip() else placeholder

    def identity(self, key):
        if key not in self.ids:
            self.ids[key] = token("application")
        self.ids.move_to_end(key)
        while len(self.ids) > 4096:
            self.ids.popitem(last=False)
        return self.ids[key]

    def windows(self, ctx, notes):
        native = self.b.desktop.windows(ctx=ctx, notes=notes)
        if len(native) > 512:
            gap(notes, "Window directory limited to 512 entries.")
        return [self.b.registry.window(w) for w in native[:512]]

    def window_application(self, item):
        """Same observed application ID in the AX-free desktop catalogue."""
        w = item.window
        key = ("process", w.pid, w.process_start) if w.pid and w.process_start else ("window", item.selector)
        return self.identity(key)

    def register_launch(self, installation, pid, birth, ctx):
        ctx.check()
        if not birth or process_birth(pid) != birth:
            raise Failure("The launched process is no longer alive.", "execution_unknown")
        application_id = self.identity(("process", pid, birth))
        application = dict(
            id=application_id,
            displayName=installation.label,
            isRunning=True,
            path=installation.path,
            identifier=installation.identifier,
        )
        self.launched[pid, birth] = (installation, application)
        self.launched.move_to_end((pid, birth))
        while len(self.launched) > 4096:
            self.launched.popitem(last=False)
        return dict(application)

    def live_launches(self, ctx):
        for (pid, birth), (installation, app) in list(self.launched.items()):
            ctx.check()
            if process_birth(pid) != birth:
                del self.launched[pid, birth]
                continue
            # Directory identities can expire. Never resurrect an old token by
            # transferring it to a different process generation.
            app["id"] = self.identity(("process", pid, birth))
            yield pid, birth, installation, dict(app)

    def running_installation(self, installation, ctx):
        return [app for _, _, source, app in self.live_launches(ctx) if source.key == installation.key]

    @staticmethod
    def accessibility_gap(error, ctx, notes, detail, *, allow_stale=False):
        ctx.check()
        if (
            isinstance(error, Failure)
            and error.code not in ("observation_unavailable", "observation_incomplete")
            and not (allow_stale and error.code == "stale_target")
        ):
            raise error
        gap(notes, detail + str(error))

    def accessibility_entries(self, fn, ctx, notes, detail):
        ctx.check()
        try:
            result = fn(notes=notes)
            ctx.check()
            return result, True
        except (Failure, self.b.ax.glib.Error) as error:
            self.accessibility_gap(error, ctx, notes, detail)
            return [], False

    def discover(self, ctx, *, include_installations=True) -> DirectoryRead:
        ax, notes = self.b.ax, []
        windows = self.windows(ctx, notes)
        applications, process_ids = {}, {}
        entries, _ = self.accessibility_entries(ax.applications, ctx, notes, "Application accessibility unavailable: ")
        for obj in entries:
            ctx.check()
            try:
                ax.live(obj)
                pid = ax.call(ax.atspi.Accessible.get_process_id, obj)
                name = ax.call(ax.atspi.Accessible.get_name, obj)
                # Exact types preserve scalar validation and identity contracts.
                # pylint: disable-next=unidiomatic-typecheck
                if type(pid) is not int or not 0 <= pid <= 2147483647:
                    raise Failure("Application process id is unavailable.", "observation_incomplete")
                name = self.text(name, notes, "Application name", placeholder="Unnamed application")
                birth = process_start(pid) if pid else ""
                key = ("process", pid, birth) if pid and birth else ("accessible", ax.native_key(obj))
                identifier_local = self.identity(key)
                applications[identifier_local] = dict(
                    id=identifier_local,
                    displayName=name or "Unnamed application",
                    isRunning=True,
                    path=None,
                    identifier=None,
                )
                if pid and birth:
                    process_ids[pid, birth] = identifier_local
            except (Failure, ax.glib.Error) as error:
                self.accessibility_gap(error, ctx, notes, "Application metadata unavailable: ", allow_stale=True)
        for pid, birth, _, app in self.live_launches(ctx):
            applications[app["id"]] = app
            process_ids[pid, birth] = app["id"]
        members, match_keys = {}, {}
        for item in windows:
            ctx.check()
            w = item.window
            key = (w.pid, w.process_start)
            identifier_local = process_ids.get(key) if all(key) else None
            if identifier_local is None:
                identifier_local = self.window_application(item)
                if all(key):
                    process_ids[key] = identifier_local
                applications.setdefault(
                    identifier_local,
                    dict(
                        id=identifier_local,
                        displayName=self.text(
                            w.app, notes, "Window application name", placeholder="Unnamed application"
                        ),
                        isRunning=True,
                        path=None,
                        identifier=None,
                    ),
                )
            members.setdefault(identifier_local, []).append(item)
            match_keys.setdefault(identifier_local, set()).update(window_class_keys(w.wm_class))
        for identifier_local, keys in match_keys.items():
            if len(keys) > 64:
                gap(notes, "Application window-class identities exceed 64 entries.")
            if keys:
                applications[identifier_local]["matchKeys"] = sorted(keys)[:64]
        # Installed entries are separate identities. Matching a name is not
        # evidence that a process came from that installation. The SDK may
        # resolve exact declared window-class hints without merging identities.
        try:
            installed = self.b.installed() if include_installations else []
        except self.b.ax.glib.Error as error:
            gap(notes, "Installed application directory unavailable: " + str(error))
            installed = []
        for app in installed:
            ctx.check()
            if len(applications) >= 4096:
                gap(notes, "Application directory limited to 4096 entries.")
                break
            try:
                record = NativeInstallation.capture(app, self.b.ax.gio, ctx, glib=self.b.ax.glib)
                identifier_local = self.identity(("installation", record.key))
                # A retained target remains immutable across catalogue refreshes.
                self.installations.setdefault(identifier_local, record)
                self.installations.move_to_end(identifier_local)
                while len(self.installations) > 4096:
                    self.installations.popitem(last=False)
                applications[identifier_local] = dict(
                    id=identifier_local,
                    displayName=self.text(
                        record.label, notes, "Installed application name", placeholder=record.identifier[:4096]
                    ),
                    isRunning=False,
                    path=record.path,
                    identifier=record.identifier,
                )
                keys = window_class_keys((record.startup_wm_class,))
                if keys:
                    applications[identifier_local]["matchKeys"] = list(keys)
            except (Failure, self.b.ax.glib.Error) as error:
                ctx.check()
                gap(notes, "Installed application metadata unavailable: " + str(error))
        return DirectoryRead(applications, members, windows, list(dict.fromkeys(notes))[:128])

    def applications(self, ctx):
        start = int(now())
        apps, _, _, notes = self.discover(ctx)
        return dict(
            kind="applications",
            session=self.session,
            platform="linux",
            observed=stamp(start),
            stopReasons=notes,
            applications=list(apps.values()),
        )

    def read(self, application, ctx):
        start = int(now())
        apps, members, windows, notes = self.discover(ctx, include_installations=False)
        if application not in apps or not apps[application]["isRunning"]:
            raise Failure(
                "The observed running application is no longer in the directory.", "stale_target", application
            )
        selected = {item.window.key: item for item in members.get(application, ())}
        # WM_TRANSIENT_FOR or AT-SPI subwindow relations may cross processes.
        # Include only explicit child links; never match another app by title.
        for _ in range(len(windows)):
            children = [
                item for item in windows if item.window.key not in selected and item.window.transient in selected
            ]
            if not children:
                break
            selected.update((item.window.key, item) for item in children)
        all_windows = {item.window.key: item for item in windows}
        candidates, provider_available = self.accessibility_entries(
            self.b.ax.windows, ctx, notes, "Window accessibility directory unavailable: "
        )
        result = []
        for item in selected.values():
            ctx.check()
            w, root = item.window, None
            # Enumeration is not an addressed input. A popup can disappear
            # while native/AX directory calls run. Preserve other live windows
            # and report a partial directory; never resurrect the old binding.
            try:
                native = self.b.desktop.validate(item)
            except Failure as error:
                ctx.check()
                if error.code != "stale_target":
                    raise
                gap(notes, "Window " + item.ref + " changed during discovery: " + str(error))
                continue
            ctx.check()
            try:
                root = native.accessible
                if root is None:
                    root = self.b.ax.matching_window(native, candidates)
                if root is not None:
                    self.b.ax.live(root)
            except (Failure, self.b.ax.glib.Error) as error:
                self.accessibility_gap(
                    error, ctx, notes, "Window accessibility unavailable for " + item.ref + ": ", allow_stale=True
                )
                root = None
            parent = all_windows.get(w.transient) if w.parent_state != "unknown" else None
            parent_state = (
                "known"
                if parent
                else "unknown"
                if w.parent_state == "unknown" or w.transient is not None or w.modal
                else "none"
            )
            row = dict(
                window=item.ref,
                instance=item.selector,
                surface=item.scope,
                node=self.store.keep(item.scope, root) if root is not None else None,
                title=self.text(w.title, notes, "Window title"),
                kind="window",
                parent=parent.ref if parent else None,
                parentState=parent_state,
                modal=bool(w.modal and w.mapped),
                nativeWindow=isinstance(w.key, int),
                accessibility=root is not None,
            )
            self.surfaces[item.scope] = item
            self.surfaces.move_to_end(item.scope)
            while len(self.surfaces) > 4096:
                self.surfaces.popitem(last=False)
            result.append(row)
        return dict(
            kind="surfaces",
            session=self.session,
            platform="linux",
            observed=stamp(start),
            stopReasons=list(dict.fromkeys(notes))[:128],
            application=application,
            provider=dict(status="atspi" if provider_available else "unavailable", attribute=None, writeStatus=None),
            surfaces=result,
        )

    def validate(self, target, ctx):
        root = self.b.registry.instance(target["instance"])
        selected = self.surfaces.get(target["surface"])
        if selected is None or target["rootWindow"] != root.ref:
            raise Failure("The original window scope is unavailable.", "stale_target")
        self.b.desktop.validate(root)
        self.b.desktop.validate(selected)
        if selected is not root:
            items = {i.window.key: i for i in self.windows(ctx, [])}
            seen, current = set(), selected.window.key
            while current != root.window.key:
                if current in seen or current not in items:
                    raise Failure("Observed surface no longer belongs to the working window.", "stale_target")
                seen.add(current)
                current = items[current].window.transient
        if target["node"] is not None:
            node = self.store.get(target["node"], target["surface"])
            self.b.ax.live(node.obj)

    def clear(self):
        self.ids.clear()
        self.surfaces.clear()
        self.installations.clear()
        self.launched.clear()
