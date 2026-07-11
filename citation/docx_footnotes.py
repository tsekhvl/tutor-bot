"""Извлечение классических footnotes из .docx (OOXML)."""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


@dataclass
class FootnoteCitation:
    """Одна сноска + абзац, к которому она привязана."""

    footnote_id: str
    marker: str  # как в тексте, обычно номер
    footnote_text: str
    paragraph_text: str
    paragraph_index: int  # 0-based среди body paragraphs


@dataclass
class DocxFootnoteParseResult:
    citations: list[FootnoteCitation]
    paragraph_count: int
    footnote_count: int
    warnings: list[str]


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _text_of(el: ET.Element) -> str:
    parts: list[str] = []
    for node in el.iter():
        if _local(node.tag) == "t" and node.text:
            parts.append(node.text)
        # мягкий перенос / tab
        if _local(node.tag) in {"tab", "br", "cr"}:
            parts.append(" ")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _footnote_map(footnotes_xml: bytes) -> dict[str, str]:
    """
    id → текст сноски.

    Служебные separator / continuationSeparator пропускаем по w:type,
    а не по id: в части документов реальная первая сноска имеет w:id="0"
    (или иной id ≠ отображаемому номеру «1»).
    """
    root = ET.fromstring(footnotes_xml)
    out: dict[str, str] = {}
    for fn in root.findall("w:footnote", NS):
        fid = fn.get(f"{{{W_NS}}}id")
        if fid is None:
            continue
        ftype = (fn.get(f"{{{W_NS}}}type") or "").strip().lower()
        if ftype in {"separator", "continuationseparator"}:
            continue
        text = _text_of(fn)
        if text:
            out[fid] = text
        else:
            # пустая реальная сноска — тоже запомним, чтобы не путать с «не найдена»
            out[fid] = ""
    return out


def _iter_body_paragraphs(document_xml: bytes):
    root = ET.fromstring(document_xml)
    body = root.find("w:body", NS)
    if body is None:
        return
    for p in body.findall("w:p", NS):
        yield p


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _paragraph_footnote_segments(p: ET.Element) -> list[tuple[str, str]]:
    """
    Сегменты абзаца по маркерам сносок.

    Для каждой сноски контекст = текст от предыдущего маркера (или начала
    абзаца) до этого маркера. Текст после маркера к этой сноске не относится
    (там может быть уже другая сноска).
    """
    segments: list[tuple[str, str]] = []
    buf: list[str] = []
    for node in p.iter():
        tag = _local(node.tag)
        if tag == "footnoteReference":
            fid = node.get(f"{{{W_NS}}}id")
            if fid is not None:
                ctx = _normalize_space("".join(buf))
                segments.append((fid, ctx))
                buf = []
            continue
        if tag == "t" and node.text:
            buf.append(node.text)
        elif tag in {"tab", "br", "cr"}:
            buf.append(" ")
    # хвост после последней сноски намеренно отбрасываем
    return segments


def parse_docx_footnotes(file_bytes: bytes) -> DocxFootnoteParseResult:
    """
    Парсит .docx: классические footnotes + контекст до маркера сноски.
    Не обрабатывает endnotes и списки литературы.
    """
    warnings: list[str] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile as e:
        raise ValueError("Файл не похож на .docx (повреждённый ZIP).") from e

    names = set(zf.namelist())
    if "word/document.xml" not in names:
        raise ValueError("В архиве нет word/document.xml — это не Word-документ.")

    if "word/footnotes.xml" not in names:
        return DocxFootnoteParseResult(
            citations=[],
            paragraph_count=0,
            footnote_count=0,
            warnings=["В документе нет классических footnotes (word/footnotes.xml отсутствует)."],
        )

    footnotes = _footnote_map(zf.read("word/footnotes.xml"))
    document_xml = zf.read("word/document.xml")

    citations: list[FootnoteCitation] = []
    para_idx = 0
    used_ids: set[str] = set()
    # Отображаемый номер как в Word (порядок появления), не внутренний w:id.
    display_n = 0

    for p in _iter_body_paragraphs(document_xml):
        segments = _paragraph_footnote_segments(p)
        if not segments:
            # абзац без сносок — считаем только если есть текст
            plain = _normalize_space(_text_of(p))
            if plain:
                para_idx += 1
            continue

        empty_ctx = [fid for fid, ctx in segments if not ctx]
        if empty_ctx:
            warnings.append(
                f"Сноски {', '.join(empty_ctx)}: мало текста перед маркером "
                f"(возможно, стоят подряд) — контекст может быть слабым."
            )

        for fid, ctx in segments:
            used_ids.add(fid)
            display_n += 1
            marker = str(display_n)
            if fid in footnotes:
                fn_text = footnotes[fid]
                if not fn_text:
                    warnings.append(
                        f"Сноска {marker} (w:id={fid}): текст в footnotes.xml пуст."
                    )
            else:
                fn_text = ""
                warnings.append(
                    f"Сноска {marker} (w:id={fid}): не найдена в footnotes.xml "
                    f"(возможно, служебный id или битая ссылка)."
                )
            citations.append(
                FootnoteCitation(
                    footnote_id=fid,
                    marker=marker,
                    footnote_text=fn_text or "(пусто)",
                    paragraph_text=ctx or "(нет текста перед маркером сноски)",
                    paragraph_index=para_idx,
                )
            )
        para_idx += 1

    orphan = sorted(
        (fid for fid in footnotes if fid not in used_ids and footnotes.get(fid)),
        key=lambda x: int(x) if x.isdigit() else x,
    )
    for fid in orphan:
        warnings.append(
            f"Сноска w:id={fid} есть в footnotes.xml, но в тексте нет ссылки — пропущена."
        )

    if not citations and footnotes:
        warnings.append(
            "Footnotes найдены, но в теле документа нет w:footnoteReference "
            "(возможно, только endnotes или другой формат)."
        )

    return DocxFootnoteParseResult(
        citations=citations,
        paragraph_count=para_idx,
        footnote_count=len(footnotes),
        warnings=warnings,
    )
