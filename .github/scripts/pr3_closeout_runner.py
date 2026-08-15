from __future__ import annotations

from pathlib import Path

source_path = Path(".github/scripts/pr3_closeout_patch.py")
source = source_path.read_text(encoding="utf-8")

old = """replace_once(
    \"app/services/youtube_delivery.py\",
    '''        *,
        session_uri: str,
        media_path: Path,
''',
    '''        *,
        access_token: str,
        session_uri: str,
        media_path: Path,
''',
)
"""
new = """replace_once(
    \"app/services/youtube_delivery.py\",
    '''    def upload_media(
        self,
        *,
        session_uri: str,
        media_path: Path,
        start_offset: int,
        total_bytes: int,
        mime_type: str,
    ) -> ResumableUploadStatus: ...
''',
    '''    def upload_media(
        self,
        *,
        access_token: str,
        session_uri: str,
        media_path: Path,
        start_offset: int,
        total_bytes: int,
        mime_type: str,
    ) -> ResumableUploadStatus: ...
''',
)
"""
if source.count(old) != 1:
    raise RuntimeError(
        f"expected exactly one protocol upload replacement, found {source.count(old)}"
    )
fixed = source.replace(old, new, 1)

old_eof = 'write(test_path, test_content.rstrip() + append_tests + "\\n")'
new_eof = 'write(test_path, test_content.rstrip() + append_tests.rstrip() + "\\n")'
if fixed.count(old_eof) != 1:
    raise RuntimeError(
        f"expected exactly one test EOF writer, found {fixed.count(old_eof)}"
    )
fixed = fixed.replace(old_eof, new_eof, 1)

exec(compile(fixed, str(source_path), "exec"), {"__name__": "__main__"})
