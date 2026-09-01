from __future__ import annotations

import json

from .cache_store import CACHE_CATEGORIES, cache_root, cache_usage, clear_cache


def _cache_locations(category: str) -> tuple[str, dict[str, str]]:
    root = cache_root()
    categories = CACHE_CATEGORIES if category == "all" else (category,)
    return str(root), {item: str(root / item) for item in categories}


class AliceLabCacheManager:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "action": (["inspect", "clear"],),
                "category": (["all", *CACHE_CATEGORIES],),
                "confirm_clear": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Must be enabled before the clear action deletes cache files.",
                    },
                ),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("cache_report",)
    FUNCTION = "manage"
    CATEGORY = "ALICE_Lab/Utils"
    DESCRIPTION = "Inspect or safely clear ALICE Lab cache categories."
    OUTPUT_NODE = True

    def manage(self, action: str, category: str, confirm_clear: bool = False):
        root, category_paths = _cache_locations(category)
        if action == "inspect":
            result = {
                "action": action,
                "category": category,
                "cache_root": root,
                "category_paths": category_paths,
                "usage": cache_usage(category),
            }
        elif action == "clear":
            if not confirm_clear:
                raise ValueError("Enable confirm_clear before clearing the ALICE Lab cache")
            result = {
                "action": action,
                "category": category,
                "cache_root": root,
                "category_paths": category_paths,
                "removed": clear_cache(category),
            }
        else:
            raise ValueError(f"Unknown cache action: {action}")
        report = json.dumps(result, ensure_ascii=False, indent=2)
        return {"ui": {"text": [report]}, "result": (report,)}

    @classmethod
    def IS_CHANGED(cls, action: str, category: str, confirm_clear: bool):
        return float("nan")
