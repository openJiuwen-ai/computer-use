# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from types import SimpleNamespace

import pytest

from computer_use_native_tools.evaluation.ledger import Episode
from manual.save_shortcut_workflow import replace_and_save
from test_program_runtime import Desktop, invoke


@pytest.mark.parametrize("phase", ["menu_read", "element_lookup"])
def test_native_fixture_never_saves_after_query_log_failure(tmp_path, phase):
    episode = Episode(tmp_path, {"task": "evidence-boundary"})
    calls = []

    async def replace(text, **kwargs):
        calls.append("replace")

    async def text():
        return SimpleNamespace(text="Q4 replaced probe")

    async def click():
        calls.append("save")

    async def read(**kwargs):
        if phase == "menu_read":
            episode.journal._error = "injected full disk during query"

    async def element(name):
        if phase == "element_lookup":
            episode.journal._error = "injected full disk during query"
        return SimpleNamespace(click=click)

    with pytest.raises(RuntimeError, match="evidence could not be persisted"):
        asyncio.run(
            replace_and_save(
                episode,
                SimpleNamespace(read=read, element=element),
                SimpleNamespace(type_text=replace, read_text=text),
                tmp_path,
                "menu",
            )
        )
    assert calls == ["replace"]


def test_generic_runtime_stops_after_silently_reported_query_log_failure():
    desktop = Desktop()
    failed = []
    original = desktop.read

    async def read(**kwargs):
        result = await original(**kwargs)
        if desktop.content == "new":
            failed.append(True)
        return result

    def checkpoint():
        if failed:
            raise OSError("recording failed during query")

    desktop.read = read
    result = invoke(desktop, checkpoint=checkpoint)
    assert result.status == "stopped" and desktop.content == "new" and desktop.saves == 0
    assert "replacement_visible" in result.confirmed_progress
