"""ComfyUI custom-node entry point and Registry metadata surface."""

from pathlib import Path
import sys


# ComfyUI imports this file as a package, while Registry/Manager inspection can
# load it directly from its file path without setting ``__package__``.  Both
# paths must expose the same mappings.  Only the import context is handled here:
# import failures from the node implementation intentionally remain visible.
if __package__:
    from .src.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
else:
    _ROOT = str(Path(__file__).resolve().parent)
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from src.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
