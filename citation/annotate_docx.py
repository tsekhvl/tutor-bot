"""Добавление Word-комментариев (примечаний) к маркерам проблемных сносок."""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timezone
from typing import Protocol

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_COMMENTS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
)
CT_COMMENTS = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
)

COMMENT_AUTHOR = "Проверка сносок"
COMMENT_INITIALS = "ПС"

_EXISTENCE_RU = {
    "likely_exists": "существует",
    "doubtful": "нуждается в перепроверке",
    "not_found": "не существует",
    "unknown": "нуждается в перепроверке",
}

# run, целиком содержащий footnoteReference с данным id
_FN_RUN_RE = re.compile(
    r"<w:r\b[^>]*>"
    r"(?:(?!</w:r>).)*?"
    r"<w:footnoteReference\b[^>]*\bw:id=\"(?P<fid>\d+)\"[^/]*/>"
    r"(?:(?!</w:r>).)*?"
    r"</w:r>",
    re.DOTALL,
)


class _FlagLike(Protocol):
    footnote_id: str
    marker: str
    existence: str
    needs_review: bool
    reasons: list[str]


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _max_existing_comment_id(doc_xml: str, comments_xml: str | None) -> int:
    ids: list[int] = [
        int(x)
        for x in re.findall(
            r"<w:comment(?:RangeStart|RangeEnd|Reference)\b[^>]*\bw:id=\"(\d+)\"",
            doc_xml,
        )
    ]
    if comments_xml:
        ids.extend(
            int(x)
            for x in re.findall(
                r"<w:comment\b[^>]*\bw:id=\"(\d+)\"",
                comments_xml,
            )
        )
    return max(ids) if ids else -1


def _ensure_content_types(parts: dict[str, bytes]) -> None:
    path = "[Content_Types].xml"
    text = parts[path].decode("utf-8")
    if "/word/comments.xml" in text:
        return
    override = (
        f'<Override PartName="/word/comments.xml" ContentType="{CT_COMMENTS}"/>'
    )
    if "</Types>" in text:
        text = text.replace("</Types>", override + "</Types>", 1)
    else:
        text += override
    parts[path] = text.encode("utf-8")


def _ensure_document_rels(parts: dict[str, bytes], names: set[str]) -> None:
    path = "word/_rels/document.xml.rels"
    if path not in names or path not in parts:
        text = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{R_NS}">'
            f'<Relationship Id="rId1" Type="{REL_COMMENTS}" Target="comments.xml"/>'
            "</Relationships>"
        )
        parts[path] = text.encode("utf-8")
        names.add(path)
        return

    text = parts[path].decode("utf-8")
    if "comments.xml" in text and "relationships/comments" in text:
        return

    used = set(re.findall(r'Id="(rId\d+)"', text))
    n = 1
    while f"rId{n}" in used:
        n += 1
    rel = (
        f'<Relationship Id="rId{n}" Type="{REL_COMMENTS}" Target="comments.xml"/>'
    )
    if "</Relationships>" in text:
        text = text.replace("</Relationships>", rel + "</Relationships>", 1)
    else:
        text += rel
    parts[path] = text.encode("utf-8")


def _comment_body_text(flag: _FlagLike) -> str:
    exists = _EXISTENCE_RU.get(flag.existence, flag.existence)
    parts = [r for r in (flag.reasons or []) if r]
    comment = " ".join(parts[:2]) if parts else "Требует внимания."
    marker = flag.marker or flag.footnote_id
    return f"Сноска {marker}. Источник: {exists}. {comment}".strip()


def _comment_xml_chunk(comment_id: int, text: str, when: str) -> str:
    body = _xml_escape(text)
    return (
        f'<w:comment w:id="{comment_id}" w:author="{_xml_escape(COMMENT_AUTHOR)}" '
        f'w:date="{when}" w:initials="{_xml_escape(COMMENT_INITIALS)}">'
        f'<w:p><w:r><w:t xml:space="preserve">{body}</w:t></w:r></w:p>'
        f"</w:comment>"
    )


def _build_comments_xml(
    existing: bytes | None,
    new_comments: list[tuple[int, str]],
    when: str,
) -> bytes:
    chunks = "".join(
        _comment_xml_chunk(cid, body, when) for cid, body in new_comments
    )
    if existing:
        text = existing.decode("utf-8")
        if "</w:comments>" not in text:
            raise ValueError("Повреждён word/comments.xml")
        text = text.replace("</w:comments>", chunks + "</w:comments>", 1)
        return text.encode("utf-8")

    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:comments xmlns:w="{W_NS}">'
        f"{chunks}"
        "</w:comments>"
    )
    return xml.encode("utf-8")


def _wrap_run(run_xml: str, comment_id: int) -> str:
    return (
        f'<w:commentRangeStart w:id="{comment_id}"/>'
        f"{run_xml}"
        f'<w:commentRangeEnd w:id="{comment_id}"/>'
        f'<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr>'
        f'<w:commentReference w:id="{comment_id}"/></w:r>'
    )


def annotate_docx_with_comments(
    file_bytes: bytes,
    flags: list[_FlagLike],
) -> bytes:
    """
    Копия .docx с Word-комментариями на маркерах сносок из flags.
    Текст сносок не меняется. Если flags пуст — возвращает исходные bytes.
    """
    flagged = [f for f in flags if f.needs_review]
    if not flagged:
        return file_bytes

    try:
        zin = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile as e:
        raise ValueError("Файл не похож на .docx.") from e

    names = set(zin.namelist())
    if "word/document.xml" not in names:
        raise ValueError("В архиве нет word/document.xml.")

    parts: dict[str, bytes] = {n: zin.read(n) for n in zin.namelist()}
    zin.close()

    doc_xml = parts["word/document.xml"].decode("utf-8")
    existing_comments = parts.get("word/comments.xml")
    comments_text = (
        existing_comments.decode("utf-8") if existing_comments else None
    )
    next_id = _max_existing_comment_id(doc_xml, comments_text) + 1
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    flag_by_fid = {str(f.footnote_id): f for f in flagged}

    wanted = set(flag_by_fid)
    placed: dict[str, int] = {}
    new_comments: list[tuple[int, str]] = []

    def _repl(m: re.Match[str]) -> str:
        fid = m.group("fid")
        if fid not in wanted or fid in placed:
            return m.group(0)
        cid = next_id + len(placed)
        placed[fid] = cid
        new_comments.append((cid, _comment_body_text(flag_by_fid[fid])))
        return _wrap_run(m.group(0), cid)

    new_doc = _FN_RUN_RE.sub(_repl, doc_xml)
    if not placed:
        return file_bytes

    parts["word/document.xml"] = new_doc.encode("utf-8")
    parts["word/comments.xml"] = _build_comments_xml(
        existing_comments, new_comments, when
    )
    names.add("word/comments.xml")
    _ensure_content_types(parts)
    _ensure_document_rels(parts, names)

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for name in _ordered_part_names(parts):
            zout.writestr(name, parts[name])
    return out.getvalue()


def _ordered_part_names(parts: dict[str, bytes]) -> list[str]:
    preferred = [
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
        "word/_rels/document.xml.rels",
        "word/comments.xml",
        "word/footnotes.xml",
    ]
    ordered: list[str] = []
    seen: set[str] = set()
    for p in preferred:
        if p in parts:
            ordered.append(p)
            seen.add(p)
    for n in sorted(parts.keys()):
        if n not in seen:
            ordered.append(n)
    return ordered
