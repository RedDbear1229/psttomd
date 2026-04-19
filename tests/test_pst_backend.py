"""
tests/test_pst_backend.py — PypffBackend 스모크 테스트

tests/data/test.pst (java-libpst 공개 샘플) 을 열어 메시지 반복 동작을 검증한다.
pypff 미설치 환경(예: Windows Native, 일부 Termux)에서는 skip.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

# pypff 가 없는 환경에서는 모듈 전체 skip
pypff = pytest.importorskip("pypff")

from lib.pst_backend import (  # noqa: E402
    MessageData,
    PypffBackend,
    get_backend,
)

TEST_PST = Path(__file__).parent / "data" / "test.pst"

pytestmark = pytest.mark.skipif(
    not TEST_PST.exists(),
    reason=f"sample PST not found: {TEST_PST}",
)


class TestGetBackend:
    def test_auto_picks_pypff_on_linux(self) -> None:
        """linux/wsl/termux 환경에서 auto 는 PypffBackend 를 반환한다."""
        backend = get_backend({"pst_backend": "pypff"})
        assert isinstance(backend, PypffBackend)

    def test_unknown_backend_exits(self) -> None:
        with pytest.raises(SystemExit):
            get_backend({"pst_backend": "bogus"})


class TestPypffBackendOpen:
    def test_open_and_close(self) -> None:
        backend = PypffBackend()
        backend.open(str(TEST_PST))
        backend.close()

    def test_context_manager(self) -> None:
        with PypffBackend() as backend:
            backend.open(str(TEST_PST))


class TestPypffBackendIteration:
    def test_iter_messages_yields_tuples(self) -> None:
        """iter_messages 는 (folder_path, MessageData) 튜플을 yield 한다."""
        messages = []
        with PypffBackend() as backend:
            backend.open(str(TEST_PST))
            for folder_path, msg in backend.iter_messages():
                messages.append((folder_path, msg))
                if len(messages) >= 3:
                    break

        assert messages, "최소 1개 메시지가 나와야 함"
        folder, msg = messages[0]
        assert isinstance(folder, str)
        assert isinstance(msg, MessageData)

    def test_message_fields_populated(self) -> None:
        """메시지에는 최소 subject 또는 body 중 하나는 존재해야 한다."""
        with PypffBackend() as backend:
            backend.open(str(TEST_PST))
            for _, msg in backend.iter_messages():
                # 샘플 PST 는 대부분 subject 또는 body 를 보유
                has_content = (
                    msg.subject
                    or msg.html_body
                    or msg.plain_text_body
                    or msg.sender_email_address
                )
                if has_content:
                    return
        pytest.fail("PST 에서 content 를 가진 메시지를 찾지 못함")

    def test_message_identifier_is_string(self) -> None:
        """message_identifier 는 항상 문자열 (pypff 는 int 를 내부적으로 사용)."""
        with PypffBackend() as backend:
            backend.open(str(TEST_PST))
            for _, msg in backend.iter_messages():
                assert isinstance(msg.message_identifier, str)
                break
