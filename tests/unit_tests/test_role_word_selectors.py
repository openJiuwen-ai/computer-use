"""Role presentation must remain usable as a public selector."""

import pytest
from computer_use.protocol.element_path import element_path


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
