"""
PST 파서 백엔드 추상화

세 가지 PST 파싱 방법을 단일 인터페이스(PSTBackend)로 통합한다:

  PypffBackend    — Linux/WSL, libpff-python (pypff) 직접 사용. 속도 최고.
  ReadpstBackend  — WSL/Linux, readpst CLI → EML → mail-parser. 빌드가 어려울 때 대안.
  Win32ComBackend — Windows Native, pywin32 + Outlook COM API. Outlook 설치 필수.

모든 백엔드는 동일한 MessageData 데이터클래스를 생성하므로,
pst2md.py 등 상위 코드는 백엔드 종류와 무관하게 동작한다.

사용 예:
    from lib.config import load_config
    from lib.pst_backend import get_backend

    cfg = load_config()
    with get_backend(cfg) as backend:
        backend.open("/path/to/archive.pst")
        for folder_path, msg in backend.iter_messages():
            print(msg.subject, msg.client_submit_time)
"""
from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 공통 데이터 클래스
# ---------------------------------------------------------------------------

@dataclass
class MessageData:
    """백엔드 독립적인 단일 메시지 표현.

    모든 백엔드(pypff / readpst / win32com)는 각자의 원시 메시지를
    이 공통 구조체로 변환해 반환한다.
    상위 코드는 이 클래스만 다루면 된다.

    Attributes:
        message_identifier:    Message-ID 또는 백엔드별 고유 식별자.
        subject:               메일 제목.
        sender_name:           발신자 이름.
        sender_email_address:  발신자 이메일 주소.
        display_to:            수신자 표시 문자열 (쉼표 구분).
        display_cc:            참조 수신자 표시 문자열.
        client_submit_time:    발송 시각 (timezone-aware 권장, 없으면 None).
        html_body:             HTML 본문 바이트 (없으면 None).
        plain_text_body:       평문 본문 바이트 (없으면 None).
        rtf_body:              RTF 본문 바이트 (없으면 None).
        in_reply_to_identifier: In-Reply-To 헤더 값.
        references:            References 헤더 값 (공백 구분 ID 목록).
        number_of_attachments: 첨부 파일 수.
        _attachments:          백엔드별 원시 첨부 객체 리스트 (내부용).
    """
    message_identifier:    str = ""
    subject:               str = ""
    sender_name:           str = ""
    sender_email_address:  str = ""
    display_to:            str = ""
    display_cc:            str = ""
    client_submit_time:    Optional[datetime] = None
    html_body:             Optional[bytes] = None
    plain_text_body:       Optional[bytes] = None
    rtf_body:              Optional[bytes] = None
    in_reply_to_identifier: str = ""
    references:            str = ""
    number_of_attachments: int = 0
    #: 백엔드별 원시 첨부 객체 — get_attachment_data() 내부에서만 참조
    _attachments: list = field(default_factory=list, repr=False)


@dataclass
class AttachmentData:
    """첨부 파일 메타데이터 (현재는 내부 참조용 보조 구조체).

    Attributes:
        name: 파일명.
        size: 바이트 단위 크기.
        _raw: 백엔드별 원시 첨부 객체 (내부용).
    """
    name: str = ""
    size: int = 0
    _raw: object = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# 추상 베이스 클래스
# ---------------------------------------------------------------------------

class PSTBackend(ABC):
    """PST 파서 백엔드 공통 인터페이스.

    컨텍스트 매니저(with 문)로 사용하면 close() 를 자동으로 호출한다.

    Example:
        with get_backend(cfg) as b:
            b.open(pst_path)
            for folder, msg in b.iter_messages():
                ...
    """

    @abstractmethod
    def open(self, path: str) -> None:
        """PST 파일을 열고 파싱을 준비한다.

        Args:
            path: PST 파일 경로 (문자열).

        Raises:
            SystemExit: 필수 라이브러리/도구가 없거나 파일 열기에 실패한 경우.
        """
        ...

    @abstractmethod
    def iter_messages(self) -> Iterator[tuple[str, MessageData]]:
        """PST 의 모든 메시지를 (폴더경로, MessageData) 튜플로 순회한다.

        폴더 경로는 '/' 구분자를 사용하는 논리적 경로
        (예: "Inbox/ProjectX")를 반환한다.

        Yields:
            (folder_path, message_data) 튜플.
        """
        ...

    @abstractmethod
    def get_attachment_data(self, msg: MessageData, index: int) -> tuple[str, bytes]:
        """지정 인덱스의 첨부 파일 데이터를 반환한다.

        Args:
            msg:   iter_messages() 에서 반환된 MessageData.
            index: 0-based 첨부 파일 인덱스.

        Returns:
            (파일명, 바이트) 튜플. 오류 시 ("attachment_N", b"") 반환.
        """
        ...

    @abstractmethod
    def close(self) -> None:
        """열린 PST 파일과 임시 리소스를 해제한다."""
        ...

    def __enter__(self) -> "PSTBackend":
        return self

    def __exit__(self, *args) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Backend 1: pypff (Linux/WSL 권장)
# ---------------------------------------------------------------------------

class PypffBackend(PSTBackend):
    """libpff-python(pypff) 기반 PST 파서.

    Linux/WSL 환경에서 가장 빠르고 안정적인 백엔드.
    pypff 는 PST 파일에 직접 접근하므로 Outlook 설치 불필요.

    설치:
        pip install libpff-python
        또는: sudo apt install python3-pff
    """

    def open(self, path: str) -> None:
        try:
            import pypff   # type: ignore[import]
        except ImportError:
            sys.exit(
                "오류: pypff 를 찾을 수 없습니다.\n"
                "설치: pip install libpff-python\n"
                "또는: sudo apt install python3-pff"
            )
        self._pff = pypff
        self._file = pypff.file()
        self._file.open(path)

    def iter_messages(self) -> Iterator[tuple[str, MessageData]]:
        root = self._file.get_root_folder()
        yield from self._iter_folder(root, "")

    def _iter_folder(self, folder, path: str) -> Iterator[tuple[str, MessageData]]:
        """폴더를 재귀적으로 순회하며 메시지를 yield 한다."""
        try:
            name = folder.name or "Root"
        except Exception:
            name = "Root"
        current = f"{path}/{name}" if path else name

        # 현재 폴더의 메시지 순회
        # number_of_sub_messages 자체가 libpff 오류를 던질 수 있으므로 try 로 감쌈
        try:
            n_msgs = folder.number_of_sub_messages
        except Exception as e:
            log.warning("폴더 메시지 수 읽기 실패 [%s]: %s", current, e)
            n_msgs = 0

        for i in range(n_msgs):
            try:
                raw = folder.get_sub_message(i)
                yield current, self._to_msgdata(raw)
            except Exception as e:
                log.warning("메시지 읽기 실패 [%s][%d]: %s", current, i, e)

        # 하위 폴더 재귀
        try:
            n_folders = folder.number_of_sub_folders
        except Exception as e:
            log.warning("하위 폴더 수 읽기 실패 [%s]: %s", current, e)
            n_folders = 0

        for i in range(n_folders):
            try:
                sub = folder.get_sub_folder(i)
                yield from self._iter_folder(sub, current)
            except Exception as e:
                log.warning("폴더 읽기 실패 [%s][%d]: %s", current, i, e)

    @staticmethod
    def _safe_get(raw, attr: str, default=None):
        """pypff 객체의 속성을 안전하게 읽는다.

        getattr 의 3인자 형식은 AttributeError 만 잡는다.
        pypff 는 내부 libpff 오류(손상된 PST, 누락된 descriptor node 등)를
        AttributeError 가 아닌 자체 예외로 전파하므로, 모든 예외를 잡아야 한다.

        Args:
            raw:     pypff 메시지/첨부 객체.
            attr:    읽을 속성 이름.
            default: 읽기 실패 시 반환할 기본값.

        Returns:
            속성 값 또는 default.
        """
        try:
            val = getattr(raw, attr)
            # None 과 falsy 바이트(b"")는 그대로 반환 — 빈 본문도 유효한 값
            return val if val is not None else default
        except Exception:
            return default

    def _to_msgdata(self, raw) -> MessageData:
        """pypff 메시지 객체를 MessageData 로 변환한다.

        pypff_message_get_number_of_attachments 등 libpff 저수준 오류가
        개별 필드 접근 시 발생할 수 있으므로 모든 필드를 독립적으로 try/except 로 감싼다.
        """
        sg = self._safe_get  # 타이핑 편의용 별칭

        # 첨부 수: libpff descriptor node 읽기 실패가 가장 빈번하게 발생
        try:
            n_att = int(sg(raw, "number_of_attachments", 0) or 0)
        except Exception:
            n_att = 0
            log.debug("첨부 수 읽기 실패 (손상된 노드): %r", sg(raw, "subject", ""))

        attachments: list = []
        for i in range(n_att):
            try:
                attachments.append(raw.get_attachment(i))
            except Exception as e:
                log.debug("첨부 객체 로드 실패 [%d]: %s", i, e)
                attachments.append(None)

        # client_submit_time 은 datetime 또는 None 이어야 함
        submit_time = sg(raw, "client_submit_time", None)

        return MessageData(
            message_identifier    = str(sg(raw, "message_identifier",    "") or ""),
            subject               = str(sg(raw, "subject",               "") or ""),
            sender_name           = str(sg(raw, "sender_name",           "") or ""),
            sender_email_address  = str(sg(raw, "sender_email_address",  "") or ""),
            display_to            = str(sg(raw, "display_to",            "") or ""),
            display_cc            = str(sg(raw, "display_cc",            "") or ""),
            client_submit_time    = submit_time if isinstance(submit_time, datetime) else None,
            html_body             = sg(raw, "html_body",       None),
            plain_text_body       = sg(raw, "plain_text_body", None),
            rtf_body              = sg(raw, "rtf_body",        None),
            in_reply_to_identifier= str(sg(raw, "in_reply_to_identifier", "") or ""),
            references            = str(sg(raw, "references",             "") or ""),
            number_of_attachments = n_att,
            _attachments          = attachments,
        )

    def get_attachment_data(self, msg: MessageData, index: int) -> tuple[str, bytes]:
        raw_att = msg._attachments[index]
        if raw_att is None:
            return f"attachment_{index}", b""

        # name, size, read_buffer 모두 libpff 오류 가능성 있음
        try:
            name = str(self._safe_get(raw_att, "name", "") or f"attachment_{index}")
        except Exception:
            name = f"attachment_{index}"

        try:
            size = int(self._safe_get(raw_att, "size", 0) or 0)
            data = raw_att.read_buffer(size) if size > 0 else b""
        except Exception as e:
            log.debug("첨부 데이터 읽기 실패 [%s]: %s", name, e)
            data = b""

        return name, data

    def close(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Backend 2: readpst CLI (WSL/Linux 대안)
# ---------------------------------------------------------------------------

class ReadpstBackend(PSTBackend):
    """readpst CLI 경유 PST 파서.

    readpst 로 PST → EML 일괄 변환 후 mail-parser 로 각 EML 을 파싱한다.
    pypff 빌드가 어렵거나 libpff 가 없는 환경에서 사용한다.

    특징:
      - 변환 시 임시 디렉터리에 EML 파일을 먼저 모두 추출 (디스크 공간 필요)
      - close() 호출 시 임시 디렉터리를 자동 삭제

    설치:
        sudo apt install pst-utils   # readpst
        pip install mail-parser
    """

    def open(self, path: str) -> None:
        if not shutil.which("readpst"):
            sys.exit(
                "오류: readpst 가 없습니다.\n"
                "설치: sudo apt install pst-utils"
            )
        self._pst_path = path
        self._tmpdir = tempfile.mkdtemp(prefix="pst2md_readpst_")
        log.info("readpst 로 EML 추출 중: %s → %s", path, self._tmpdir)
        result = subprocess.run(
            ["readpst", "-e", "-D", "-o", self._tmpdir, path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            sys.exit(f"readpst 실패:\n{result.stderr}")

    def iter_messages(self) -> Iterator[tuple[str, MessageData]]:
        try:
            import mailparser  # type: ignore[import]
        except ImportError:
            sys.exit("설치 필요: pip install mail-parser")

        for eml_path in sorted(Path(self._tmpdir).rglob("*.eml")):
            # 임시 디렉터리 기준 상대 경로를 폴더 경로로 사용
            rel = eml_path.parent.relative_to(self._tmpdir)
            folder_path = str(rel).replace(os.sep, "/") or "Root"
            try:
                mail = mailparser.parse_from_file(str(eml_path))
                yield folder_path, self._to_msgdata(mail, eml_path)
            except Exception as e:
                log.warning("EML 파싱 실패 [%s]: %s", eml_path, e)

    def _to_msgdata(self, mail, eml_path: Path) -> MessageData:
        """mail-parser 객체를 MessageData 로 변환한다."""
        subject = mail.subject or ""

        from_list = mail.from_ or []
        from_name = from_list[0][0] if from_list and from_list[0] else ""
        from_addr = from_list[0][1] if from_list and len(from_list[0]) > 1 else ""

        to_list = mail.to or []
        display_to = ", ".join(f"{n} <{a}>" if n else a for n, a in to_list)
        cc_list = mail.cc or []
        display_cc = ", ".join(f"{n} <{a}>" if n else a for n, a in cc_list)

        dt = mail.date
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        # mail-parser: text_html → HTML, text_plain → 평문으로 직접 분리
        # mail.body 는 HTML 없으면 평문을 반환하므로 html_body 에 넣으면 안 됨
        html_body: Optional[bytes] = None
        if getattr(mail, "text_html", None):
            html_body = "\n".join(mail.text_html).encode("utf-8")
        text_body: Optional[bytes] = None
        if getattr(mail, "text_plain", None):
            text_body = "\n".join(mail.text_plain).encode("utf-8")

        att_list = mail.attachments or []

        return MessageData(
            message_identifier    = mail.message_id or str(eml_path.stem),
            subject               = subject,
            sender_name           = from_name,
            sender_email_address  = from_addr,
            display_to            = display_to,
            display_cc            = display_cc,
            client_submit_time    = dt,
            html_body             = html_body,
            plain_text_body       = text_body,
            in_reply_to_identifier= mail.headers.get("In-Reply-To", ""),
            references            = mail.headers.get("References", ""),
            number_of_attachments = len(att_list),
            _attachments          = att_list,
        )

    def get_attachment_data(self, msg: MessageData, index: int) -> tuple[str, bytes]:
        att = msg._attachments[index]
        name = att.get("filename") or att.get("name") or f"attachment_{index}"
        payload = att.get("payload") or b""
        # mail-parser 는 첨부 내용을 Base64 문자열로 반환하는 경우가 있다
        if isinstance(payload, str):
            try:
                payload = base64.b64decode(payload)
            except Exception:
                payload = payload.encode("utf-8", errors="replace")
        return name, payload

    def close(self) -> None:
        if hasattr(self, "_tmpdir") and self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Backend 3: win32com / Outlook COM API (Windows Native)
# ---------------------------------------------------------------------------

class Win32ComBackend(PSTBackend):
    """pywin32 + Outlook COM API 기반 PST 파서.

    Windows에서 Outlook 이 설치된 경우 사용한다.
    PST 파일을 Outlook 스토어로 마운트해 COM 인터페이스를 통해 접근한다.

    주의사항:
      - Outlook 프로세스가 실행 중이어야 COM 서버로 동작한다.
      - pypff 와 달리 PST 에 Outlook 잠금(exclusive lock)이 걸릴 수 있다.
      - close() 에서 마운트된 스토어를 제거하므로 반드시 호출해야 한다.

    설치:
        pip install pywin32
        python -m pywin32_postinstall -install
    """

    # Outlook OlStoreType 상수: 유니코드 PST 포맷
    _OL_STORE_UNICODE = 3
    # Outlook OlItemType 상수: 메일 항목
    _OL_MAIL_ITEM = 43

    def open(self, path: str) -> None:
        try:
            import win32com.client  # type: ignore[import]
        except ImportError:
            sys.exit(
                "오류: pywin32 가 없습니다.\n"
                "설치: pip install pywin32\n"
                "설치 후: python -m pywin32_postinstall -install"
            )
        self._win32 = win32com.client
        self._outlook = win32com.client.Dispatch("Outlook.Application")
        self._ns = self._outlook.GetNamespace("MAPI")

        # PST 추가 전 스토어 수를 기억해 나중에 새 스토어를 식별
        self._store_count_before = self._ns.Stores.Count
        self._ns.AddStoreEx(path, self._OL_STORE_UNICODE)
        self._pst_path = path

        # 파일 경로로 새로 추가된 스토어 탐색
        self._store = None
        for i in range(1, self._ns.Stores.Count + 1):
            s = self._ns.Stores.Item(i)
            if s.FilePath and Path(s.FilePath).resolve() == Path(path).resolve():
                self._store = s
                break
        # 경로 매칭 실패 시 마지막 스토어를 fallback 으로 사용
        if self._store is None:
            self._store = self._ns.Stores.Item(self._ns.Stores.Count)

    def iter_messages(self) -> Iterator[tuple[str, MessageData]]:
        root_folder = self._store.GetRootFolder()
        yield from self._iter_folder(root_folder, "")

    def _iter_folder(self, folder, path: str) -> Iterator[tuple[str, MessageData]]:
        """Outlook 폴더를 재귀적으로 순회하며 메일 항목을 yield 한다."""
        name = folder.Name or "Root"
        current = f"{path}/{name}" if path else name

        items = folder.Items
        # COM 컬렉션은 1-based 인덱스
        for i in range(1, items.Count + 1):
            try:
                item = items.Item(i)
                if item.Class == self._OL_MAIL_ITEM:
                    yield current, self._to_msgdata(item)
            except Exception as e:
                log.warning("항목 읽기 실패 [%s][%d]: %s", current, i, e)

        subfolders = folder.Folders
        for i in range(1, subfolders.Count + 1):
            try:
                yield from self._iter_folder(subfolders.Item(i), current)
            except Exception as e:
                log.warning("폴더 읽기 실패 [%s][%d]: %s", current, i, e)

    def _to_msgdata(self, item) -> MessageData:
        """Outlook MailItem COM 객체를 MessageData 로 변환한다."""
        def _safe(attr: str, default=""):
            """COM 속성 접근 중 예외 발생 시 기본값을 반환하는 헬퍼."""
            try:
                v = getattr(item, attr, default)
                return v if v is not None else default
            except Exception:
                return default

        # SentOn 은 COM Date 타입 → Python datetime 으로 변환
        sent_on = _safe("SentOn", None)
        dt = None
        if sent_on and hasattr(sent_on, "year"):
            try:
                dt = datetime(
                    sent_on.year, sent_on.month, sent_on.day,
                    sent_on.hour, sent_on.minute, sent_on.second,
                    tzinfo=timezone.utc,
                )
            except (ValueError, OverflowError):
                dt = None

        # HTML 본문 우선 시도, 없으면 평문 본문
        body_html: Optional[bytes] = None
        body_plain: Optional[bytes] = None
        try:
            html = item.HTMLBody
            if html:
                body_html = html.encode("utf-8", errors="replace")
        except Exception:
            pass
        if body_html is None:
            try:
                plain = item.Body
                if plain:
                    body_plain = plain.encode("utf-8", errors="replace")
            except Exception:
                pass

        try:
            n_att = item.Attachments.Count
        except Exception:
            n_att = 0

        att_list = []
        for i in range(1, n_att + 1):
            try:
                att_list.append(item.Attachments.Item(i))
            except Exception:
                att_list.append(None)

        return MessageData(
            message_identifier    = str(_safe("InternetMessageId") or _safe("EntryID")),
            subject               = str(_safe("Subject")),
            sender_name           = str(_safe("SenderName")),
            sender_email_address  = str(_safe("SenderEmailAddress")),
            display_to            = str(_safe("To")),
            display_cc            = str(_safe("CC")),
            client_submit_time    = dt,
            html_body             = body_html,
            plain_text_body       = body_plain,
            # Outlook COM 에서 In-Reply-To / References 는 직접 접근 불가
            in_reply_to_identifier= "",
            references            = "",
            number_of_attachments = n_att,
            _attachments          = att_list,
        )

    def get_attachment_data(self, msg: MessageData, index: int) -> tuple[str, bytes]:
        raw_att = msg._attachments[index]
        if raw_att is None:
            return f"attachment_{index}", b""
        name = str(getattr(raw_att, "FileName", "") or f"attachment_{index}")
        # COM API 는 SaveAsFile 로만 내용을 얻을 수 있어 임시 파일 경유
        # mktemp() 는 TOCTOU 경쟁 조건 위험 → mkstemp() 사용
        fd, tmp = tempfile.mkstemp(suffix=Path(name).suffix or ".bin")
        os.close(fd)  # SaveAsFile 이 직접 파일을 열므로 fd 즉시 닫기
        try:
            raw_att.SaveAsFile(tmp)
            data = Path(tmp).read_bytes()
        except Exception:
            data = b""
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return name, data

    def close(self) -> None:
        try:
            # 마운트한 PST 스토어를 Outlook 에서 제거
            # open() 도중 실패했을 경우 _store / _ns 가 없을 수 있음
            if hasattr(self, "_store") and self._store and hasattr(self, "_ns"):
                self._ns.RemoveStore(self._store.GetRootFolder())
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 팩토리 함수
# ---------------------------------------------------------------------------

def get_backend(config: dict) -> PSTBackend:
    """설정에 따라 적합한 PSTBackend 인스턴스를 반환한다.

    pst_backend 설정값:
      auto     — 플랫폼을 자동 감지해 선택 (linux/wsl → pypff, windows → win32com)
      pypff    — PypffBackend
      readpst  — ReadpstBackend
      win32com — Win32ComBackend

    Args:
        config: load_config() 가 반환한 설정 dict.

    Returns:
        해당 백엔드의 PSTBackend 인스턴스 (아직 open() 호출 전).

    Raises:
        SystemExit: 알 수 없는 백엔드 이름이 지정된 경우.
    """
    from lib.config import detect_platform

    name = config.get("pst_backend", "auto")
    if name == "auto":
        plat = detect_platform()
        name = "win32com" if plat == "windows" else "pypff"
        log.debug("백엔드 자동 선택: %s (플랫폼: %s)", name, plat)

    if name == "pypff":
        return PypffBackend()
    if name == "readpst":
        return ReadpstBackend()
    if name == "win32com":
        return Win32ComBackend()

    sys.exit(
        f"알 수 없는 pst_backend: {name!r}\n"
        "허용값: pypff | readpst | win32com | auto"
    )
