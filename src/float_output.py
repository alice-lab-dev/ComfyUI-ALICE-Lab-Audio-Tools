from __future__ import annotations

import json
import math


class AliceLabOutputFloat:
    """Display an executed FLOAT value and pass it through unchanged."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "value": ("FLOAT", {"forceInput": True}),
                "label": ("STRING", {"default": "Value"}),
                "precision": ("INT", {"default": 6, "min": 0, "max": 12}),
            }
        }

    RETURN_TYPES = ("FLOAT",)
    RETURN_NAMES = ("value",)
    FUNCTION = "display"
    CATEGORY = "ALICE_Lab/Utils"
    DESCRIPTION = "Display an executed FLOAT value in the node and pass it through."
    OUTPUT_NODE = True

    def display(self, value=0.0, label="Value", precision=6):
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ValueError("Output Float received a non-finite value")
        payload = {
            "value": numeric_value,
            "label": str(label).strip() or "Value",
            "precision": max(0, min(12, int(precision))),
        }
        return {
            "ui": {"alice_lab_audio_tools_float_out": [json.dumps(payload)]},
            "result": (numeric_value,),
        }
