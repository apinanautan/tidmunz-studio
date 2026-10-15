import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "snapgen_modules"))
from snapgen_bridge_web_error_patch import PREVIEW_OLD, patch_transport, patch_classifier


def test_streamed_model_error_is_read_and_connection_closed():
    source = 'def preview(response):\n' + PREVIEW_OLD + '    return text[:400]\n'
    patched = patch_transport(source)
    assert patch_transport(patched) == patched
    scope = {}
    exec(patched, scope)

    class Response:
        text = ""
        status_code = 404
        closed = False

        def iter_content(self):
            yield b'{"detail": "Model '
            yield b'not found"}'

        def close(self):
            self.closed = True

    response = Response()
    assert scope['preview'](response) == '{"detail": "Model not found"}'
    assert response.closed
    response.status_code = 200
    response.closed = False
    assert scope['preview'](response) == ''
    assert not response.closed


def test_model_404_is_not_reported_as_missing_conversation():
    source = '''def classify(message, provider_status):
    normalized = message.lower()
    if provider_status == 404 and "conversation failed" in normalized:
        return "chatgpt_conversation_not_found"
    if any(keyword in normalized for keyword in ("not valid", "invalid model", "unsupported model")):
        return "chatgpt_unsupported_model"
    return "provider_error"
'''
    patched = patch_classifier(source)
    assert patch_classifier(patched) == patched
    scope = {}
    exec(patched, scope)
    classify = scope['classify']
    assert classify('ChatGPT conversation failed: 404 {"detail":"Model not found"}', 404) == 'chatgpt_unsupported_model'
    assert classify('ChatGPT conversation failed: 404 conversation not found', 404) == 'chatgpt_conversation_not_found'


def test_desktop_formatter_does_not_turn_model_error_back_into_missing_story():
    path = Path(__file__).resolve().parents[1] / 'snapgen_gui_v2.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_snapgen_friendly_bridge_error')
    scope = {'_snapgen_rate_limit_text': lambda message: None}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), scope)
    message = scope['_snapgen_friendly_bridge_error'](
        'HTTP 400: chatgpt_unsupported_model: ChatGPT Web request failed. Provider status: 404. '
        'ChatGPT conversation failed: 404 {"detail":"Model not found"}'
    )
    assert 'ChatGPT ปฏิเสธโมเดล' in message
    assert 'ยังไม่ต้องกดเริ่มเรื่องใหม่' in message
    assert 'ไม่พบประวัติ ChatGPT เดิม' not in message
