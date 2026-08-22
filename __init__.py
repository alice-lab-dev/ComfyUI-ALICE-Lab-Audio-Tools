# ComfyUI loads this entry point as a package. Some source-tree tools import the
# file directly as ``__init__`` without a package context; in that case there is
# no valid relative import to perform. Import errors during an actual ComfyUI
# package load are deliberately allowed to propagate.
if __package__:
    from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
