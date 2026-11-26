# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A gate quote only locates a rule: the doer gets the whole paragraph it comes from, condition and heading included."""
from __future__ import annotations

import pytest

gate = pytest.importorskip("osworld.gate")

IMAGE_SKILL = """# Image editor

## Checks

### A task that names the editor

When the task names the editor, work only through its menus and dialogs. **Do not use its scripting console**
-- and do not edit the image files with other programs or scripts.

- Leave the editor open on the result."""

# The shape of multi2fix_1030 09a37c51's quote: the rule without the "When ..." that limits it.
GATE_NOTES = """### Skill basis
- [image] "**Do not use its scripting console** -- and do not edit the image files with other programs or scripts."
- [image] "Leave the ... editor open on the result."
- [image] "a rule that is in no skill at all, made up by the check"
### Probe evidence
- The editor is not running.
### Feasibility
Feasible."""


@pytest.fixture
def image_skill(monkeypatch):
    skills = [{"name": "image", "description": "", "directory": "/skills/image"}]
    monkeypatch.setattr(gate, "list_skills", lambda: skills)
    monkeypatch.setattr(gate, "skill_body", lambda path, version: IMAGE_SKILL)


def _basis(notes: str) -> str:
    return notes.split("### Skill basis", 1)[1].split("### Probe evidence", 1)[0]


def test_a_quote_that_drops_its_condition_comes_back_with_it(image_skill):
    basis = _basis(gate.expand_skill_basis(GATE_NOTES, "v1")[0])

    assert "When the task names the editor, work only through its menus and dialogs" in basis
    assert "(Image editor > Checks > A task that names the editor)" in basis


def test_an_elided_quote_finds_its_paragraph(image_skill):
    assert '"- Leave the editor open on the result."' in gate.expand_skill_basis(GATE_NOTES, "v1")[0]


def test_a_quote_found_in_no_skill_is_dropped_and_reported(image_skill):
    notes, unmatched = gate.expand_skill_basis(GATE_NOTES, "v1")

    assert "made up by the check" not in notes
    assert unmatched == ['- [image] "a rule that is in no skill at all, made up by the check"']


def test_the_other_sections_are_unchanged(image_skill):
    notes, _ = gate.expand_skill_basis(GATE_NOTES, "v1")

    assert notes.endswith("### Probe evidence\n- The editor is not running.\n### Feasibility\nFeasible.")


def test_no_matching_quote_leaves_none(image_skill):
    quote = '### Skill basis\n- [image] "nothing like this exists anywhere"\n### Probe evidence\n- x'
    notes, _ = gate.expand_skill_basis(quote, "v1")

    assert _basis(notes).strip() == "- none"


def test_a_naming_quote_keeps_its_default_name_condition():
    quote = '### Skill basis\n- [chrome] "keep the name Chrome proposes"\n### Probe evidence\n- x'
    notes, _ = gate.expand_skill_basis(quote, "v1")

    assert "When the task asks for Chrome's default name or says nothing about the name" in notes


def test_every_block_of_every_skill_sits_under_a_heading():
    for skill in gate.list_skills():
        body = gate.skill_body(f"{skill['directory']}/SKILL.md", "v1")
        orphans = [b for h, b in gate.skill_blocks(body) if not h]
        assert not orphans, (skill["name"], orphans[:1])
