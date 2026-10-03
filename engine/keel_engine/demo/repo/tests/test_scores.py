import pytest

from scores import average, top


def test_average():
    assert average([2, 4]) == 3


def test_average_of_nothing_is_refused():
    with pytest.raises(ValueError):
        average([])


def test_top():
    assert top({"ana": 3, "bo": 9, "cy": 5}, 2) == ["bo", "cy"]
