# -*- coding: utf-8 -*-
"""Install Prompt-Ref conversation cursor and DOCX upload support into Bridge."""
from __future__ import annotations

from pathlib import Path
import base64
import json
import py_compile
import subprocess

MARKER = 'conversation_state: dict[str, str | None] = {'
FILE_MARKER = 'elif item_type in {"input_file", "file"}:'
CAPABILITY_VERSION = 10
CAPABILITY_FILE = ".snapgen_bridge_capabilities.json"
CONVERSATION_NOT_FOUND_MARKER = "chatgpt_conversation_not_found"


def _write_capability_version(bridge_dir) -> None:
    path = Path(bridge_dir) / CAPABILITY_FILE
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({
        "version": CAPABILITY_VERSION,
        "docx_input_file": True,
        "prompt_ref_cursor": True,
        "robust_file_upload": True,
        "cursor_message_id_helper": True,
        "image_story_cursor": True,
        "vision_story_cursor": True,
        "conversation_not_found_error": True,
        "temporary_image_chat": True,
        "image_rate_limit_notice": True,
    }, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def runtime_probe(bridge_dir, bridge_python) -> tuple[bool, str]:
    """Prove installed Bridge Python converts a real input_file payload."""
    root = Path(bridge_dir).resolve()
    python = Path(bridge_python).resolve()
    if not python.is_file():
        return False, f"ไม่พบ Python ของ Bridge: {python}"
    sample = base64.b64encode(b"PK-snapgen-docx-probe").decode("ascii")
    code = (
        "import base64; "
        "from chatgpt_api.api.openai_compat import _openai_content_to_provider_parts as c, _latest_message_id_from_value as mid; "
        f"raw=base64.b64decode('{sample}'); "
        f"p=c([{{'type':'input_file','file_data':'{sample}',"
        "'mime_type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',"
        "'filename':'snapgen_probe.docx'}]); "
        "assert len(p)==1 and p[0].kind=='file_bytes' and p[0].data==raw and p[0].name=='snapgen_probe.docx'; "
        "assert mid([{'message':{'id':'cursor-probe'}}])=='cursor-probe'; "
        "import inspect; from chatgpt_api.api.openai_compat import _vision_request as vr; "
        "src=inspect.getsource(vr); assert 'vision metadata requires both conversation_id and parent_message_id' in src; "
        "assert 'conversation_state=conversation_state' in src; "
        "from chatgpt_api.providers.chatgpt.transport import ChatGPTWebTransport as wt; "
        "img=inspect.getsource(wt._generate_image_sync); "
        "assert 'self._repair_invalid_conversation_parent(payload, headers)' in img; "
        "assert 'ChatGPT image response switched conversation' in img; "
        "builder=inspect.getsource(wt._build_image_chat_payload); "
        "assert '# SnapGen temporary image chat' in builder; "
        "from chatgpt_api.api.openai_compat import _classify_provider_error as classify; "
        "assert classify('ChatGPT conversation failed: 404', 404)[0]=='chatgpt_conversation_not_found'; "
        "print('DOCX_BRIDGE_OK CURSOR_HELPER_OK VISION_CURSOR_OK IMAGE_CURSOR_STRICT_OK')"
    )
    try:
        result = subprocess.run(
            [str(python), "-B", "-c", code], cwd=str(root),
            capture_output=True, text=True, timeout=45,
            encoding="utf-8", errors="replace",
        )
    except Exception as exc:
        return False, str(exc)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0 and "DOCX_BRIDGE_OK" in output and "CURSOR_HELPER_OK" in output and "VISION_CURSOR_OK" in output, output.strip()[-1200:]


def file_upload_supported(bridge_dir) -> bool:
    root = Path(bridge_dir) / "chatgpt_api"
    try:
        compat = (root / "api" / "openai_compat.py").read_text(encoding="utf-8")
        types = (root / "core" / "types.py").read_text(encoding="utf-8")
        transport = (root / "providers" / "chatgpt" / "transport.py").read_text(encoding="utf-8")
        return (
            FILE_MARKER in compat
            and 'def file_bytes(' in types
            and 'part.kind not in {"image_bytes", "file_bytes"}' in transport
            and 'part.kind in {"image_bytes", "file_bytes"}' in transport
        )
    except OSError:
        return False


def _install_file_upload(bridge_dir, log) -> bool:
    """Add generic file upload without changing existing image behavior."""
    root = Path(bridge_dir) / "chatgpt_api"
    paths = {
        "compat": root / "api" / "openai_compat.py",
        "types": root / "core" / "types.py",
        "transport": root / "providers" / "chatgpt" / "transport.py",
    }
    if file_upload_supported(bridge_dir):
        return False
    for path in paths.values():
        if not path.is_file():
            raise RuntimeError(f"ไม่พบ Bridge source: {path}")

    texts = {key: path.read_text(encoding="utf-8") for key, path in paths.items()}
    old = 'ContentKind = Literal["text", "image_url", "image_bytes"]'
    new = 'ContentKind = Literal["text", "image_url", "image_bytes", "file_bytes"]'
    if new not in texts["types"]:
        if old not in texts["types"]:
            raise RuntimeError("โครงสร้างชนิดไฟล์ Bridge ไม่ตรงกับ patch")
        texts["types"] = texts["types"].replace(old, new, 1)

    method = (
        '    @classmethod\n'
        '    def file_bytes(\n'
        '        cls,\n'
        '        data: bytes,\n'
        '        mime_type: str = "application/octet-stream",\n'
        '        name: str | None = None,\n'
        '    ) -> "ContentPart":\n'
        '        return cls(kind="file_bytes", data=data, mime_type=mime_type, name=name)\n\n'
    )
    anchor = '\n\n@dataclass(slots=True)\nclass Message:'
    if 'def file_bytes(' not in texts["types"]:
        if anchor not in texts["types"]:
            raise RuntimeError("ไม่พบตำแหน่งเพิ่ม file_bytes ใน Bridge")
        texts["types"] = texts["types"].replace(anchor, '\n\n' + method + '@dataclass(slots=True)\nclass Message:', 1)

    image_branch = (
        '                if url:\n'
        '                    parts.append(_content_part_from_image_reference(url, item))\n'
    )
    file_branch = (
        image_branch +
        '            elif item_type in {"input_file", "file"}:\n'
        '                encoded = _str_or_none(item.get("file_data")) or _str_or_none(item.get("data"))\n'
        '                if encoded:\n'
        '                    if encoded.startswith("data:") and "," in encoded:\n'
        '                        encoded = encoded.split(",", 1)[1]\n'
        '                    try:\n'
        '                        file_bytes = base64.b64decode(encoded, validate=True)\n'
        '                    except Exception as exc:\n'
        '                        raise ValueError("input_file.file_data must be valid base64") from exc\n'
        '                    parts.append(ContentPart.file_bytes(\n'
        '                        file_bytes,\n'
        '                        _str_or_none(item.get("mime_type")) or "application/octet-stream",\n'
        '                        _str_or_none(item.get("filename")) or _str_or_none(item.get("name")) or "attachment",\n'
        '                    ))\n'
    )
    if FILE_MARKER not in texts["compat"]:
        if image_branch not in texts["compat"]:
            raise RuntimeError("ไม่พบตำแหน่งเพิ่ม input_file ใน Bridge")
        texts["compat"] = texts["compat"].replace(image_branch, file_branch, 1)

    transport_replacements = (
        ('if part.kind != "image_bytes" or not part.data:',
         'if part.kind not in {"image_bytes", "file_bytes"} or not part.data:'),
        ('elif part.kind == "image_bytes":',
         'elif part.kind in {"image_bytes", "file_bytes"}:'),
    )
    for old, new in transport_replacements:
        if new not in texts["transport"]:
            if old not in texts["transport"]:
                raise RuntimeError("โครงสร้างอัปโหลดไฟล์ Bridge ไม่ตรงกับ patch")
            texts["transport"] = texts["transport"].replace(old, new, 1)

    temp_paths = []
    try:
        for key, path in paths.items():
            temp = path.with_suffix(path.suffix + ".snapgen.tmp")
            temp.write_text(texts[key], encoding="utf-8")
            py_compile.compile(str(temp), doraise=True)
            temp_paths.append((temp, path))
        for temp, path in temp_paths:
            temp.replace(path)
    finally:
        for temp, _path in temp_paths:
            temp.unlink(missing_ok=True)
    if not file_upload_supported(bridge_dir):
        raise RuntimeError("ติดตั้งตัวแนบ DOCX ของ Bridge ไม่ครบ")
    log("✓ Bridge รองรับการแนบ DOCX จริงแล้ว")
    return True


def _install_robust_upload(bridge_dir, log) -> bool:
    """Retry web/storage upload and return JSON errors instead of closing socket."""
    root = Path(bridge_dir) / "chatgpt_api"
    transport_path = root / "providers" / "chatgpt" / "transport.py"
    compat_path = root / "api" / "openai_compat.py"
    if not transport_path.is_file() or not compat_path.is_file():
        raise RuntimeError("ไม่พบไฟล์ Bridge สำหรับติดตั้ง robust upload")
    transport = transport_path.read_text(encoding="utf-8")
    compat = compat_path.read_text(encoding="utf-8")
    changed = False

    old_create = '''        create_response = requests.post(
            f"{self.endpoints.base_url}/backend-api/files",
            headers=_json_headers_for_token_refresh(headers),
            data=json.dumps(
                {
                    "file_name": file_name,
                    "file_size": len(data),
                    "use_case": "multimodal" if mime_type.startswith("image/") else "my_files",
                },
                separators=(",", ":"),
            ).encode("utf-8"),
            impersonate=self.impersonate,
            timeout=self.timeout,
        )
'''
    new_create = '''        create_response = None
        create_error = None
        for attempt in range(3):
            try:
                create_response = requests.post(
                    f"{self.endpoints.base_url}/backend-api/files",
                    headers=_json_headers_for_token_refresh(headers),
                    data=json.dumps(
                        {
                            "file_name": file_name,
                            "file_size": len(data),
                            "use_case": "multimodal" if mime_type.startswith("image/") else "my_files",
                        },
                        separators=(",", ":"),
                    ).encode("utf-8"),
                    impersonate=self.impersonate,
                    timeout=self.timeout,
                )
                if create_response.status_code not in {429, 500, 502, 503, 504}:
                    break
            except Exception as exc:
                create_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        if create_response is None:
            raise ProviderError(f"ChatGPT file create network failed: {create_error}")
'''
    if new_create not in transport:
        if old_create not in transport:
            raise RuntimeError("ไม่พบขั้น create file ของ Bridge")
        transport = transport.replace(old_create, new_create, 1)
        changed = True

    old_put = '''        upload_response = requests.put(
            upload_url,
            headers={
                "content-type": mime_type,
                "origin": self.endpoints.base_url,
                "x-ms-blob-type": "BlockBlob",
                "x-ms-version": "2020-04-08",
            },
            data=data,
            impersonate=self.impersonate,
            timeout=self.timeout,
        )
'''
    new_put = '''        upload_response = None
        upload_error = None
        upload_headers = {
            "content-type": mime_type,
            "origin": self.endpoints.base_url,
            "x-ms-blob-type": "BlockBlob",
            "x-ms-version": "2020-04-08",
        }
        for attempt in range(3):
            try:
                upload_kwargs = {
                    "headers": upload_headers,
                    "data": data,
                    "timeout": self.timeout,
                }
                # Signed storage URLs do not need browser impersonation. Some
                # team networks reset impersonated PUT requests.
                if attempt == 0:
                    upload_kwargs["impersonate"] = self.impersonate
                upload_response = requests.put(upload_url, **upload_kwargs)
                if upload_response.status_code not in {429, 500, 502, 503, 504}:
                    break
            except Exception as exc:
                upload_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        if upload_response is None:
            raise ProviderError(f"ChatGPT storage upload network failed: {upload_error}")
'''
    if new_put not in transport:
        if old_put not in transport:
            raise RuntimeError("ไม่พบขั้น storage upload ของ Bridge")
        transport = transport.replace(old_put, new_put, 1)
        changed = True

    old_marker = '''        uploaded_response = requests.post(
            f"{self.endpoints.base_url}/backend-api/files/{file_id}/uploaded",
            headers=_json_headers_for_token_refresh(headers),
            data=b"{}",
            impersonate=self.impersonate,
            timeout=self.timeout,
        )
'''
    new_marker = '''        uploaded_response = None
        marker_error = None
        for attempt in range(3):
            try:
                uploaded_response = requests.post(
                    f"{self.endpoints.base_url}/backend-api/files/{file_id}/uploaded",
                    headers=_json_headers_for_token_refresh(headers),
                    data=b"{}",
                    impersonate=self.impersonate,
                    timeout=self.timeout,
                )
                if uploaded_response.status_code not in {429, 500, 502, 503, 504}:
                    break
            except Exception as exc:
                marker_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        if uploaded_response is None:
            raise ProviderError(f"ChatGPT uploaded marker network failed: {marker_error}")
'''
    if new_marker not in transport:
        if old_marker not in transport:
            raise RuntimeError("ไม่พบขั้น uploaded marker ของ Bridge")
        transport = transport.replace(old_marker, new_marker, 1)
        changed = True

    old_handler = '''            except ValueError as exc:
                _send_json(self, 400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
                return
            _send_json(self, 200, response)
'''
    new_handler = '''            except ValueError as exc:
                _send_json(self, 400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
                return
            except Exception as exc:
                _send_json(self, 502, {"error": {"message": f"Bridge request failed: {type(exc).__name__}: {exc}", "type": "bridge_internal_error"}})
                return
            _send_json(self, 200, response)
'''
    if new_handler not in compat:
        if old_handler not in compat:
            raise RuntimeError("ไม่พบ HTTP error handler ของ Bridge")
        compat = compat.replace(old_handler, new_handler, 1)
        changed = True

    if not changed:
        return False
    for path, text in ((transport_path, transport), (compat_path, compat)):
        temp = path.with_suffix(path.suffix + ".robust.tmp")
        try:
            temp.write_text(text, encoding="utf-8")
            py_compile.compile(str(temp), doraise=True)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    log("✓ Bridge อัปโหลด DOCX แบบ retry และส่ง Error รายขั้นแล้ว")
    return True



def _install_cursor_message_id_helper(bridge_dir, log) -> bool:
    """Install helper in openai_compat.py, where cursor callbacks call it."""
    path = Path(bridge_dir) / "chatgpt_api" / "api" / "openai_compat.py"
    if not path.is_file():
        raise RuntimeError(f"ไม่พบ Bridge source: {path}")
    source = path.read_text(encoding="utf-8")
    if "def _latest_message_id_from_value(" in source:
        return False
    anchor = "\n\nasync def _collect_text(provider: ChatGPTProvider, request: ChatRequest, operation_id: str | None = None) -> str:\n"
    helper = (
        "\n\ndef _latest_message_id_from_value(value: Any) -> str | None:\n"
        "    found: list[str] = []\n\n"
        "    def walk(item: Any) -> None:\n"
        "        if isinstance(item, dict):\n"
        "            message = item.get(\"message\")\n"
        "            if isinstance(message, dict) and isinstance(message.get(\"id\"), str):\n"
        "                found.append(message[\"id\"])\n"
        "            direct_id = item.get(\"message_id\")\n"
        "            if isinstance(direct_id, str):\n"
        "                found.append(direct_id)\n"
        "            for nested in item.values():\n"
        "                walk(nested)\n"
        "        elif isinstance(item, list):\n"
        "            for nested in item:\n"
        "                walk(nested)\n\n"
        "    walk(value)\n"
        "    return found[-1] if found else None\n"
    )
    if anchor not in source:
        raise RuntimeError("ไม่พบตำแหน่งเพิ่ม cursor helper ใน Bridge")
    source = source.replace(anchor, helper + anchor, 1)
    temp = path.with_suffix(path.suffix + ".cursor-helper.tmp")
    try:
        temp.write_text(source, encoding="utf-8")
        py_compile.compile(str(temp), doraise=True)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    log("✓ Bridge รองรับ parent message cursor ครบแล้ว")
    return True


def _install_conversation_not_found_error(bridge_dir, log) -> bool:
    """Classify a missing continuation chat instead of reporting a generic 502."""
    path = Path(bridge_dir) / "chatgpt_api" / "api" / "openai_compat.py"
    if not path.is_file():
        raise RuntimeError(f"ไม่พบ Bridge source: {path}")
    source = path.read_text(encoding="utf-8")
    if CONVERSATION_NOT_FOUND_MARKER in source:
        return False

    anchor = '    if provider_status in {401, 403}:\n'
    replacement = (
        '    if provider_status == 404 and "conversation failed" in normalized:\n'
        '        return (\n'
        '            "chatgpt_conversation_not_found",\n'
        '            "invalid_request_error",\n'
        '            409,\n'
        '            "The requested ChatGPT conversation was not found for the selected account. "\n'
        '            "Verify the account capture; if the conversation was deleted or belongs to another account, "\n'
        '            "start a new story explicitly. The bridge will not create a new conversation automatically.",\n'
        '        )\n'
        + anchor
    )
    if anchor not in source:
        raise RuntimeError("ไม่พบตำแหน่งจัดประเภท conversation 404 ใน Bridge")
    source = source.replace(anchor, replacement, 1)
    temp = path.with_suffix(path.suffix + ".conversation-404.tmp")
    try:
        temp.write_text(source, encoding="utf-8")
        py_compile.compile(str(temp), doraise=True)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    log("✓ Bridge แยกข้อผิดพลาดประวัติ ChatGPT ที่หายออกจาก 502 แล้ว")
    return True

def cursor_supported(bridge_dir) -> bool:
    path = Path(bridge_dir) / "chatgpt_api" / "api" / "openai_compat.py"
    try:
        text = path.read_text(encoding="utf-8")
        return MARKER in text and 'metadata["on_parent_message_id"]' in text
    except OSError:
        return False


def install(bridge_dir, log=print) -> bool:
    """Patch old upstream Bridge source. Return True only when files changed."""
    file_changed = _install_file_upload(bridge_dir, log)
    robust_changed = _install_robust_upload(bridge_dir, log)
    helper_changed = _install_cursor_message_id_helper(bridge_dir, log)
    conversation_error_changed = _install_conversation_not_found_error(bridge_dir, log)
    from snapgen_bridge_image_cursor_patch import install as _install_image_cursor
    image_cursor_changed = _install_image_cursor(bridge_dir, log)
    from snapgen_bridge_vision_cursor_patch import install as _install_vision_cursor
    vision_cursor_changed = _install_vision_cursor(bridge_dir, log)
    from snapgen_bridge_rate_limit_patch import install as _install_rate_limit
    try:
        vision_cursor_changed = _install_rate_limit(bridge_dir, log) or vision_cursor_changed
    except Exception as rate_limit_error:
        # Optional nicer rate-limit message: a Bridge with a different layout
        # must still start, or every update leaves the team with no Bridge.
        log(f"[Bridge] ข้ามการแจ้งลิมิตรูปแบบละเอียด: {rate_limit_error}")
    path = Path(bridge_dir) / "chatgpt_api" / "api" / "openai_compat.py"
    if not path.is_file():
        raise RuntimeError(f"ไม่พบ Bridge source: {path}")
    text = path.read_text(encoding="utf-8")
    if cursor_supported(bridge_dir):
        _write_capability_version(bridge_dir)
        return file_changed or robust_changed or helper_changed or conversation_error_changed or image_cursor_changed or vision_cursor_changed

    replacements = [
        (
            '    request_metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}\n'
            '    requested_operation_id = _str_or_none(body.get("chatgpt_operation_id")) or _str_or_none(',
            '    request_metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}\n'
            '    conversation_id = _str_or_none(request_metadata.get("conversation_id"))\n'
            '    parent_message_id = _str_or_none(request_metadata.get("parent_message_id"))\n'
            '    conversation_state: dict[str, str | None] = {\n'
            '        "conversation_id": conversation_id,\n'
            '        "parent_message_id": parent_message_id,\n'
            '    }\n'
            '    requested_operation_id = _str_or_none(body.get("chatgpt_operation_id")) or _str_or_none(',
        ),
        (
            '                temporary_chat,\n'
            '                operation_id=operation_id,\n'
            '            )\n'
            '            if not text:',
            '                temporary_chat,\n'
            '                operation_id=operation_id,\n'
            '                conversation_id=conversation_id,\n'
            '                parent_message_id=parent_message_id,\n'
            '                conversation_state=conversation_state,\n'
            '            )\n'
            '            if not text:',
        ),
        (
            '            return _completion_response(requested_model, text, [], account=account, extra=operation_extra)\n'
            '        finally:',
            '            response = _completion_response(requested_model, text, [], account=account, extra=operation_extra)\n'
            '            for key in ("conversation_id", "parent_message_id"):\n'
            '                value = conversation_state.get(key)\n'
            '                if isinstance(value, str) and value:\n'
            '                    response[key] = value\n'
            '            return response\n'
            '        finally:',
        ),
        (
            '    operation_id: str | None = None,\n'
            ') -> tuple[str, ChatGPTProvider, str]:\n'
            '    attempts: list[dict[str, Any]] = []',
            '    operation_id: str | None = None,\n'
            '    conversation_id: str | None = None,\n'
            '    parent_message_id: str | None = None,\n'
            '    conversation_state: dict[str, str | None] | None = None,\n'
            ') -> tuple[str, ChatGPTProvider, str]:\n'
            '    attempts: list[dict[str, Any]] = []',
        ),
        (
            '                    temporary_chat,\n'
            '                    operation_id=operation_id,\n'
            '                ),\n'
            '            )\n'
            '        except ProviderError as exc:',
            '                    temporary_chat,\n'
            '                    operation_id=operation_id,\n'
            '                    conversation_id=conversation_id,\n'
            '                    parent_message_id=parent_message_id,\n'
            '                    conversation_state=conversation_state,\n'
            '                ),\n'
            '            )\n'
            '        except ProviderError as exc:',
        ),
        (
            '        async for delta in provider.stream_chat(request):\n'
            '            if delta.conversation_id:\n'
            '                _update_chatgpt_operation(operation_id, conversation_id=delta.conversation_id)\n'
            '                if _chatgpt_operation_cancel_requested(operation_id):',
            '        async for delta in provider.stream_chat(request):\n'
            '            if delta.conversation_id:\n'
            '                _update_chatgpt_operation(operation_id, conversation_id=delta.conversation_id)\n'
            '                callback = request.metadata.get("on_conversation_id")\n'
            '                if callable(callback):\n'
            '                    callback(delta.conversation_id)\n'
            '                if _chatgpt_operation_cancel_requested(operation_id):',
        ),
        (
            '                    raise ProviderError("ChatGPT operation cancelled")\n'
            '            if delta.text:\n'
            '                chunks.append(delta.text)',
            '                    raise ProviderError("ChatGPT operation cancelled")\n'
            '            parent_message_id = _latest_message_id_from_value(delta.raw)\n'
            '            if parent_message_id:\n'
            '                callback = request.metadata.get("on_parent_message_id")\n'
            '                if callable(callback):\n'
            '                    callback(parent_message_id)\n'
            '            if delta.text:\n'
            '                chunks.append(delta.text)',
        ),
        (
            '    temporary_chat: bool,\n'
            '    operation_id: str | None = None,\n'
            ') -> str:\n'
            '    return await _collect_text(\n'
            '        provider,\n'
            '        ChatRequest(\n'
            '            messages=messages,\n'
            '            model=model_slug,\n'
            '            thinking_effort=thinking_effort,\n'
            '            stream=True,\n'
            '            metadata={"history_and_training_disabled": temporary_chat},',
            '    temporary_chat: bool,\n'
            '    operation_id: str | None = None,\n'
            '    conversation_id: str | None = None,\n'
            '    parent_message_id: str | None = None,\n'
            '    conversation_state: dict[str, str | None] | None = None,\n'
            ') -> str:\n'
            '    metadata: dict[str, Any] = {"history_and_training_disabled": temporary_chat}\n'
            '    if conversation_state is not None:\n'
            '        def on_conversation_id(value: str) -> None:\n'
            '            conversation_state["conversation_id"] = value\n\n'
            '        def on_parent_message_id(value: str) -> None:\n'
            '            conversation_state["parent_message_id"] = value\n\n'
            '        metadata["on_conversation_id"] = on_conversation_id\n'
            '        metadata["on_parent_message_id"] = on_parent_message_id\n'
            '    return await _collect_text(\n'
            '        provider,\n'
            '        ChatRequest(\n'
            '            messages=messages,\n'
            '            model=model_slug,\n'
            '            conversation_id=conversation_id,\n'
            '            parent_message_id=parent_message_id,\n'
            '            thinking_effort=thinking_effort,\n'
            '            stream=True,\n'
            '            metadata=metadata,',
        ),
    ]
    changed = 0
    for old, new in replacements:
        if new in text:
            continue
        if old not in text:
            raise RuntimeError("โครงสร้าง Bridge ไม่ตรงกับ patch Prompt-Ref ที่รองรับ")
        text = text.replace(old, new, 1)
        changed += 1
    if not changed or MARKER not in text or 'metadata["on_parent_message_id"]' not in text:
        raise RuntimeError("ติดตั้ง cursor Prompt-Ref ไม่ครบ")
    temp = path.with_suffix(".cursor.tmp")
    temp.write_text(text, encoding="utf-8")
    py_compile.compile(str(temp), doraise=True)
    temp.replace(path)
    _write_capability_version(bridge_dir)
    log("✓ Bridge รองรับประวัติ Prompt-Ref แล้ว")
    return True


def extract_cursor(data):
    """Read cursor from current and legacy Bridge response layouts."""
    if not isinstance(data, dict):
        return None, None
    candidates = [data]
    for key in ("metadata", "meta", "raw", "response", "result", "data"):
        value = data.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for choice in data.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        candidates.append(choice)
        message = choice.get("message")
        if isinstance(message, dict):
            candidates.append(message)
            candidates.extend(
                message[key] for key in ("metadata", "raw")
                if isinstance(message.get(key), dict)
            )
    conversation_id = None
    parent_message_id = None
    for value in candidates:
        conversation_id = conversation_id or value.get("conversation_id") or value.get("chatgpt_conversation_id")
        parent_message_id = parent_message_id or value.get("parent_message_id") or value.get("message_id") or value.get("chatgpt_message_id")
    return conversation_id, parent_message_id
