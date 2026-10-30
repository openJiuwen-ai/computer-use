# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Discover public app instances using the existing native directory protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.identity import FrozenRecord
from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.native.query.directory import SurfaceReadProfile, surfaces_query
from computer_use.session.interfaces import ApplicationEntry
from computer_use.protocol.session import failure_error

from .membership import roots

if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.directory.service import NativeDirectory
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
    from computer_use.session.interfaces import ApplicationReferences
    from computer_use.types import AppInfo


class ApplicationDiscoveryPipeline:
    """Publish selectable App instances from application and window catalogues.

    Running applications with discovered root windows expand into instance
    choices; other applications retain an application-level entry. Each choice
    receives a public reference used to construct its App handle.

    Attributes:
        directory: Native application/window directory service used for discovery.
        references: Registry publishing each choice under a public application number.
    """

    def __init__(self, directory: NativeDirectory, references: ApplicationReferences) -> None:
        self.directory, self.references = directory, references

    async def run(self) -> list[AppInfo]:
        """Discover applications and allocate public integer references for their choices."""
        directory = await self.directory.applications()
        result = []
        running = [app["id"] for app in directory["applications"] if app["isRunning"]]
        capabilities = self.directory.port.connection.capabilities
        batch_enabled = "nativeSurfaceBatch" in capabilities and "nativeReadIsolation" not in capabilities
        # A visual single-query capability does not promise a visual batch.
        # Keep its established route when only legacy batching is available.
        batch_enabled = batch_enabled and (
            "nativeVisualSurfaceQuery" not in capabilities or "nativeVisualSurfaceBatch" in capabilities
        )
        batch_entries = {}
        for application in directory["applications"]:
            if application["isRunning"]:
                try:
                    if batch_enabled:
                        if application["id"] not in batch_entries:
                            start = running.index(application["id"])
                            try:
                                batch = await self.directory.surface_batch(
                                    running[start : start + 64], profile=SurfaceReadProfile.VISUAL
                                )
                            except ComputerUseError as error:
                                if error.effect != "none":
                                    raise
                                # A failed read-only batch returns to the original
                                # per-application discovery, without changing targets.
                                batch_enabled = False
                            else:
                                batch_entries = {entry["application"]: entry for entry in batch["entries"]}
                        if batch_enabled:
                            entry = batch_entries[application["id"]]
                            if "error" in entry:
                                raise failure_error("inspect", entry["error"])
                            surfaces = entry["result"]
                    if not batch_enabled:
                        # pylint: disable-next=protected-access
                        surfaces = await self.directory._query(
                            surfaces_query(
                                application["id"],
                                profile=SurfaceReadProfile.VISUAL,
                                capabilities=self.directory.port.connection.capabilities,
                            ),
                            30000,
                        )
                except ComputerUseError as error:
                    if error.effect != "none":
                        raise
                    surfaces = None
                if "nativeReadIsolation" in self.directory.port.connection.capabilities and (
                    surfaces is None or not surfaces["surfaces"]
                ):
                    # Some providers enumerate window identity through AX
                    # when no native window catalogue is available. This is
                    # still a directory query, never recursive tree capture.
                    try:
                        surfaces = await self.directory.surfaces(application["id"])
                    except ComputerUseError as error:
                        if error.effect != "none":
                            raise
                        surfaces = None
                if surfaces is not None and roots(surfaces):
                    result.extend(self.publish_choices(application["displayName"], surfaces))
                    continue
            entry = ApplicationEntry(
                directory["session"], application["id"], application["displayName"], application["isRunning"]
            )
            result.append(
                FrozenRecord(
                    dict(id=self.references.publish(entry), display_name=entry.display_name, is_running=entry.running)
                )
            )
        if not result and directory["stopReasons"]:
            raise local_failure(
                "application discovery is incomplete: " + "; ".join(directory["stopReasons"]),
                "retry discovery when the desktop is available",
                "observation_incomplete",
            )
        return result

    def publish_choices(self, name: str, directory: DirectoryFacts) -> list[AppInfo]:
        """Publish one App choice per root window in an application's directory.

        Args:
            name: Application display name used as the choice's title prefix.
            directory: Native window catalogue, including parent-relationship evidence.

        Returns:
            Public entries with instance references and window-specific display names.
        """
        choices = []
        for item in roots(directory):
            title = name + (" — " + item["title"] if item["title"] else "")
            if item["parentState"] == "unknown":
                title += " [window ownership unknown]"
            entry = ApplicationEntry(
                directory["session"], directory["application"], title, True, directory, item["window"], name
            )
            choices.append(FrozenRecord(dict(id=self.references.publish(entry), display_name=title, is_running=True)))
        return choices
