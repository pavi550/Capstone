"""Independent calculator acceptance suite, held outside the application."""

from calculator import subtract


def test_difference():
    assert subtract(9, 4) == 5


def test_negative_result():
    assert subtract(4, 9) == -5
