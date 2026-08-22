from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "alice_lab_audio_tools_test_float_output",
    Path(__file__).parents[2] / "float_output.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_float_out_displays_and_passes_exact_value() -> None:
    result = MODULE.AliceLabOutputFloat().display(0.987654321, "similarity", 9)

    assert result["result"] == (0.987654321,)
    payload = json.loads(result["ui"]["alice_lab_audio_tools_float_out"][0])
    assert payload == {
        "value": 0.987654321,
        "label": "similarity",
        "precision": 9,
    }


def test_float_out_value_is_a_named_connection_input() -> None:
    value_type, value_options = MODULE.AliceLabOutputFloat.INPUT_TYPES()["required"]["value"]

    assert value_type == "FLOAT"
    assert value_options == {"forceInput": True}


def test_float_out_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        MODULE.AliceLabOutputFloat().display(float("nan"))
