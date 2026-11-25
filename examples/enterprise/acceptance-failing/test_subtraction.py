"""Intentionally failing acceptance fixture to exercise the delivery gate."""

from calculator import subtract


def test_intentional_failure():
    expected_fault = 100
    assert subtract(9, 4) == expected_fault
