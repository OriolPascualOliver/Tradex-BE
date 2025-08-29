import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.sorted_list import SortedList


def test_add_and_ordering():
    sl = SortedList()
    for value in [5, 1, 3]:
        sl.add(value)
    assert list(sl) == [1, 3, 5]


def test_remove_existing_and_missing_values():
    sl = SortedList([1, 2, 3])
    sl.remove(2)
    assert list(sl) == [1, 3]
    try:
        sl.remove(4)
    except ValueError:
        pass
    else:
        assert False, "Expected ValueError for missing element"
