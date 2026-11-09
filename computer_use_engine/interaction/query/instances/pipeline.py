# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Discover application scopes without expanding any window catalogue."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.diagnostics import local_failure
from computer_use.identity import FrozenRecord
from computer_use.session.interfaces import ApplicationEntry


if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.directory.service import NativeDirectory
    from computer_use.session.interfaces import ApplicationReferences
    from computer_use.session.windows import WindowReferences
    from computer_use.types import AppInfo


class ApplicationDiscoveryPipeline:
    """Publish application choices independently of AX/window discovery.

    The native application directory supplies installed/running identity. Window
    catalogue discovery occurs when the selected App or desktop is observed.
    """

    def __init__(
        self, directory: NativeDirectory, references: ApplicationReferences, windows: WindowReferences
    ) -> None:
        self.directory, self.references, self.windows = directory, references, windows

    async def run(self) -> list[AppInfo]:
        """Publish applications only; concrete windows have their own references."""
        directory = await self.directory.applications(include_ended=True)
        self.windows.retire_applications(directory["session"], directory.get("endedApplications", ()))
        result = []
        for application in directory["applications"]:
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
                "observation_incomplete",
            )
        return result
