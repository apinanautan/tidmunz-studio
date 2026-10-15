import ast
import os
from pathlib import Path
from unittest.mock import patch


def test_codex_requires_explicit_selection():
    source = Path(__file__).resolve().parents[1] / "snapgen_modules" / "snapgen_codex_engine.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "engine_wanted")
    namespace = {"os": os}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    with patch.dict(os.environ, {}, clear=True):
        assert not namespace["engine_wanted"]()
        for value in ("auto", "web", "", "unknown"):
            os.environ["SNAPGEN_ENGINE"] = value
            assert not namespace["engine_wanted"]()
        os.environ["SNAPGEN_ENGINE"] = "codex"
        assert namespace["engine_wanted"]()
