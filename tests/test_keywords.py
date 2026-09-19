from app.transcriber.keywords import find_flags


def test_find_flags_basic():
    text = "so anyway flag this pick up milk on the way home"
    assert find_flags(text, ["flag this"]) == [("flag this", "pick up milk on the way home")]


def test_find_flags_case_insensitive():
    text = "Remember This call the dentist tomorrow"
    assert find_flags(text, ["remember this"]) == [("remember this", "call the dentist tomorrow")]


def test_find_flags_no_match():
    text = "just a normal sentence with nothing special"
    assert find_flags(text, ["flag this"]) == []


def test_find_flags_trigger_with_nothing_after_is_dropped():
    text = "flag this"
    assert find_flags(text, ["flag this"]) == []


def test_find_flags_multiple_triggers():
    text = "flag this buy eggs, also remember this call mom"
    result = find_flags(text, ["flag this", "remember this"])
    assert ("flag this", "buy eggs, also remember this call mom") in result
    assert ("remember this", "call mom") in result
