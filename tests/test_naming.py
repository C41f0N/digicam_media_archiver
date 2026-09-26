from __future__ import annotations

from digicam_archiver.naming import MAX_NAME_LEN, sanitize, unique_name


def test_sanitize_keeps_a_normal_name() -> None:
    assert sanitize("Uni Friends Hangout") == "Uni Friends Hangout"


def test_sanitize_strips_path_separators() -> None:
    assert sanitize("../../etc/passwd") == "etc passwd"
    assert sanitize("a/b\\c") == "a b c"


def test_sanitize_strips_reserved_and_control_characters() -> None:
    assert sanitize('bad:name*?"<>|') == "bad name"
    assert sanitize("two\x00lines\x1f") == "two lines"


def test_sanitize_collapses_whitespace() -> None:
    assert sanitize("  lots   of \t space  ") == "lots of space"


def test_sanitize_trims_dots_so_no_dotfold_names() -> None:
    assert sanitize("  ..hidden.. ") == "hidden"
    assert sanitize("trailing...") == "trailing"


def test_sanitize_falls_back_when_nothing_is_left() -> None:
    assert sanitize("   ") == "Event"
    assert sanitize("///") == "Event"
    assert sanitize("", fallback="Untitled") == "Untitled"


def test_sanitize_caps_the_length() -> None:
    result = sanitize("x" * 200)
    assert len(result) == MAX_NAME_LEN


def test_sanitize_does_not_end_with_a_dot_after_truncating() -> None:
    result = sanitize("y" * (MAX_NAME_LEN - 2) + "...")
    assert not result.endswith(".")


def test_unique_name_passes_a_free_name_through() -> None:
    assert unique_name("Hangout", set()) == "Hangout"


def test_unique_name_counts_up() -> None:
    taken = {"Hangout", "Hangout (2)"}
    assert unique_name("Hangout", taken) == "Hangout (3)"
