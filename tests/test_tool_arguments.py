import json

import pytest

from advisor_team.tool_arguments import call_arguments

WHOLE = '{"skill_name":"meeting-recall"}'


@pytest.mark.parametrize("text", [WHOLE, "{}", "[]", '{"entityUrls":["/me/events?$top=5"]}', ' {"a": 1} '])
def test_valid_arguments_are_unchanged(text: str) -> None:
    assert call_arguments(text) == text


def test_streamed_copy_then_whole_arguments_keeps_one_copy() -> None:
    assert call_arguments(WHOLE + WHOLE) == WHOLE
    nested = '{"query":"Michael Scott","capabilities":[{"name":"Meetings"}]}'
    assert json.loads(call_arguments(nested + nested)) == json.loads(nested)


def test_partial_stream_then_whole_arguments_keeps_the_whole() -> None:
    assert call_arguments('{"skill_na' + WHOLE) == WHOLE


def test_same_arguments_twice_with_different_whitespace() -> None:
    assert json.loads(call_arguments('{"a": 1}\n{"a":1}')) == {"a": 1}


@pytest.mark.parametrize("text", ['{"a":1}{"a":2}', '{"a":', "not json", ""])
def test_other_invalid_arguments_are_left_for_the_caller_to_reject(text: str) -> None:
    assert call_arguments(text) == text
