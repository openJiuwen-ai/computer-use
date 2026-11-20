"""Role presentation must remain usable as a public selector."""

import pytest
from computer_use.protocol.element_path import element_path
from computer_use.protocol.schema import SchemaError


@pytest.mark.parametrize("words,canonical", [
    ("menu item", "menuItem"), ("text field", "textField"),
    ("menu bar item", "menuBarItem"), ("button", "button"),
])
def test_rendered_role_words_and_canonical_role_resolve_to_one_query(words, canonical):
    source = [{"role": words, "name": "sort"}]
    assert element_path(source) == element_path([{"role": canonical, "name": "sort"}])
    assert source[0]["role"] == words


@pytest.mark.parametrize("role", ["menu  item", "menu\titem", "menu/item", "menu item!", "", 123])
def test_invalid_role_is_not_silently_broadened_to_a_name_query(role):
    with pytest.raises(ValueError):
        element_path([{"role": role, "name": "sort"}])


def test_role_error_identifies_the_argument():
    with pytest.raises(ValueError, match="menu/item"):
        element_path([{"role": "menu/item", "name": "sort"}])


@pytest.mark.parametrize(
    ("role", "cause_type"),
    [("menu  item", ValueError), ("menu/item", SchemaError)],
)
def test_role_parse_keeps_cause_and_suppression(role, cause_type):
    with pytest.raises(ValueError) as failure:
        element_path([{"role": role, "name": "sort"}])
    assert type(failure.value.__context__) is cause_type
    assert failure.value.__cause__ is None
    assert failure.value.__suppress_context__


def test_selector_length_and_whitespace_follow_the_wire_character_units():
    astral = "😀" * 128
    unicode_control = "\u0085"
    assert element_path([astral, {"identifier": unicode_control}])[0]["name"] == astral
    assert element_path([unicode_control])[0]["name"] == unicode_control
    with pytest.raises(ValueError, match="up to 256 characters"):
        element_path([astral + "x"])
    with pytest.raises(ValueError, match="nonempty name/identifier"):
        element_path(["\ufeff"])
