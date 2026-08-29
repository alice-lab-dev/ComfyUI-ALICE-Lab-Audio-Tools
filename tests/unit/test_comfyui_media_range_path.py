from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[2]


def _path_class() -> ast.ClassDef:
    module = ast.parse((ROOT / "src" / "nodes.py").read_text(encoding="utf-8"))
    return next(
        statement
        for statement in module.body
        if isinstance(statement, ast.ClassDef)
        and statement.name == "AliceLabMediaRangePath"
    )


def _prompt_link_helper():
    module = ast.parse((ROOT / "src" / "nodes.py").read_text(encoding="utf-8"))
    helper = next(
        statement
        for statement in module.body
        if isinstance(statement, ast.FunctionDef)
        and statement.name == "_prompt_input_is_linked"
    )
    namespace: dict[str, object] = {}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), "<helper>", "exec"), namespace)
    return namespace["_prompt_input_is_linked"]


def test_prompt_link_detection_distinguishes_widgets_and_connections() -> None:
    is_linked = _prompt_link_helper()
    prompt = {
        "12": {
            "inputs": {
                "media_path": ["7", 0],
                "start_seconds": 1.25,
            }
        }
    }

    assert is_linked(prompt, "12", "media_path") is True
    assert is_linked(prompt, "12", "start_seconds") is False
    assert is_linked(prompt, "12", "end_seconds") is False
    assert is_linked({}, "12", "media_path") is False


def test_path_node_keeps_media_path_as_a_connectable_string() -> None:
    path_class = _path_class()
    input_types = next(
        statement
        for statement in path_class.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and statement.name == "INPUT_TYPES"
    )
    source = ast.unparse(input_types)

    assert "'media_path'" in source
    assert "'STRING'" in source
    assert "connect a STRING output" in source
    assert "vhs_path_extensions" in source


def test_linked_path_defers_file_validation_until_execution() -> None:
    path_class = _path_class()
    validate = next(
        statement
        for statement in path_class.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and statement.name == "VALIDATE_INPUTS"
    )
    source = ast.unparse(validate)

    assert "media_path is None" in source
    assert "validate_static_range(start_seconds, end_seconds)" in source


def test_linked_path_becomes_the_effective_browser_preview_source() -> None:
    source = (ROOT / "web" / "media_range.js").read_text(encoding="utf-8")

    assert "let executedPathSource = null" in source
    assert "const activeSource = () => isPathNode && executedPathSource" in source
    assert "executedPathSource = executedRange.source" in source
    assert "const filename = usesGeneratedPreview" in source
    assert ": activeSource();" in source


def test_changed_linked_path_resets_unlinked_ab_range() -> None:
    source = (ROOT / "web" / "media_range.js").read_text(encoding="utf-8")

    assert "const pathChanged = executedRange.source !== previousSource" in source
    assert 'const rangeIsLinked = ["start_seconds", "end_seconds"].some' in source
    assert "if (pathChanged && !rangeIsLinked) executedRange = null" in source


def test_changed_linked_path_resets_backend_seconds_in_the_same_run() -> None:
    path_class = _path_class()
    extract = next(
        statement
        for statement in path_class.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and statement.name == "extract"
    )
    source = ast.unparse(extract)

    assert "path_is_linked" in source
    assert "previous_path != path" in source
    assert "not range_is_linked" in source
    assert "reset_range=reset_range" in source


def test_reset_range_uses_complete_media_duration_for_all_outputs() -> None:
    path_class = _path_class()
    extract_path = next(
        statement
        for statement in path_class.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and statement.name == "_extract_path"
    )
    source = ast.unparse(extract_path)

    assert "start = 0.0 if reset_range" in source
    assert "end = total if reset_range" in source
    assert "end - start" in source
