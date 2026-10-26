"""Select menu source kind."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.menus.model import PURE, MenuCollectionRequest
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, port: NativePort) -> Callable[[MenuCollectionRequest, Control], bool]:
    def execute(request: MenuCollectionRequest, control: Control) -> bool:
        """Select application-root menu discovery when the driver declares native application
        roots.
        """
        return "nativeApplicationRoots" in port.connection.capabilities

    return execute


stage = Pass.factory(
    name="select_menu_source_kind",
    factory=create,
    contract=Contract(MenuCollectionRequest, bool, PURE),
)
