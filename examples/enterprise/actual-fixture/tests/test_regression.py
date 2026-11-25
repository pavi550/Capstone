"""Existing regression test for the deterministic fixture."""

from calculator import add


def test_add():
    assert add(1, 2) == 3
