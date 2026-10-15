import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "snapgen_modules"))
import snapgen_bridge_codex_patch as patch  # noqa: E402


def _bridge(tmp_path: Path, provider_block: str) -> Path:
    compat = tmp_path / "chatgpt_api" / "api" / "openai_compat.py"
    compat.parent.mkdir(parents=True)
    (tmp_path / "chatgpt_api" / "providers" / "chatgpt").mkdir(parents=True)
    compat.write_text("def _provider_for_account(config, account=None):\n" + provider_block + "\n        pass\n",
                      encoding="utf-8")
    return compat


def test_plain_bridge_gets_chrome_first(tmp_path):
    compat = _bridge(tmp_path, patch.PROVIDER_OLD)
    assert patch.install(tmp_path, log=lambda _m: None)
    text = compat.read_text(encoding="utf-8")
    assert patch.PROVIDER_NEW in text
    assert text.index("snapgen_chrome") < text.index("snapgen_codex")
    assert (tmp_path / "chatgpt_api" / "providers" / "chatgpt" / "snapgen_chrome.py").is_file()
    assert not patch.install(tmp_path, log=lambda _m: None)  # second run changes nothing


def test_codex_only_bridge_is_upgraded(tmp_path):
    compat = _bridge(tmp_path, patch.PROVIDER_V1)
    assert patch.install(tmp_path, log=lambda _m: None)
    text = compat.read_text(encoding="utf-8")
    assert patch.PROVIDER_NEW in text and patch.PROVIDER_V1 not in text
