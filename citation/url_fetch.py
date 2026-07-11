"""Извлечение URL из сносок и HTTP-prefetch (title/snippet/status)."""
from __future__ import annotations

import html as html_lib
import logging
import re
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .docx_footnotes import FootnoteCitation

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_TITLE_RE = re.compile(
    r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

MAX_URLS_PER_FOOTNOTE = 2
FETCH_TIMEOUT_SEC = 5.0
# Жёсткий потолок ожидания одного future (если socket timeout «проскочил»).
FETCH_RESULT_TIMEOUT_SEC = 7.0
# Общий лимит prefetch на весь документ.
PREFETCH_BUDGET_SEC = 45.0
MAX_BODY_BYTES = 80_000
FETCH_WORKERS = 6
USER_AGENT = (
    "Mozilla/5.0 (compatible; TutorBotCitationCheck/1.1; +academic)"
)


@dataclass
class UrlFetchResult:
    url: str
    ok: bool
    status: int | None = None
    final_url: str = ""
    content_type: str = ""
    title: str = ""
    snippet: str = ""
    error: str = ""


def extract_urls(text: str, *, max_urls: int = MAX_URLS_PER_FOOTNOTE) -> list[str]:
    """Достаёт http(s) URL из текста сноски, дедуп, обрезка хвостов пунктуации."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_RE.finditer(text):
        raw = m.group(0).rstrip(".,;:!?»\"'")
        while raw and raw[-1] in ")]}>":
            raw = raw[:-1]
        if not raw:
            continue
        try:
            p = urlparse(raw)
            if p.scheme not in {"http", "https"} or not p.netloc:
                continue
        except Exception:
            continue
        if raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
        if len(out) >= max_urls:
            break
    return out


def _is_html(content_type: str, body: bytes) -> bool:
    ct = (content_type or "").lower()
    if "html" in ct or "xml" in ct:
        return True
    if any(x in ct for x in ("pdf", "octet-stream", "msword", "zip", "image/")):
        return False
    head = body[:200].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


def _parse_html(body: bytes) -> tuple[str, str]:
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        text = body.decode("latin-1", errors="replace")
    title = ""
    tm = _TITLE_RE.search(text)
    if tm:
        title = _WS_RE.sub(" ", html_lib.unescape(tm.group(1))).strip()[:300]
    stripped = _TAG_RE.sub(" ", text)
    stripped = html_lib.unescape(stripped)
    stripped = _WS_RE.sub(" ", stripped).strip()
    if title and stripped.lower().startswith(title.lower()):
        stripped = stripped[len(title) :].strip()
    return title, stripped[:500]


def _read_limited(resp, limit: int = MAX_BODY_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        chunk = resp.read(min(16384, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def fetch_url(url: str) -> UrlFetchResult:
    """Один HTTP GET с коротким socket-timeout; тело обрезается."""
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.5",
            "Accept-Language": "ru,en;q=0.8",
            # просим только начало — многие серверы уважают Range
            "Range": f"bytes=0-{MAX_BODY_BYTES - 1}",
        },
        method="GET",
    )
    ctx = ssl.create_default_context()
    try:
        with urlopen(req, timeout=FETCH_TIMEOUT_SEC, context=ctx) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            final = resp.geturl() or url
            ctype = resp.headers.get("Content-Type", "") or ""
            # PDF/бинарник — не читаем тело
            ct_l = ctype.lower()
            if any(x in ct_l for x in ("pdf", "octet-stream", "msword", "zip", "image/")):
                return UrlFetchResult(
                    url=url,
                    ok=200 <= int(status or 0) < 400 or int(status or 0) == 206,
                    status=int(status) if status is not None else None,
                    final_url=final,
                    content_type=ctype.split(";")[0].strip(),
                    error="",
                )
            body = _read_limited(resp)

        st = int(status) if status is not None else 0
        ok = 200 <= st < 400 or st == 206
        if not _is_html(ctype, body):
            return UrlFetchResult(
                url=url,
                ok=ok,
                status=st or None,
                final_url=final,
                content_type=ctype.split(";")[0].strip(),
                error="" if ok else f"HTTP {st}",
            )
        title, snippet = _parse_html(body)
        return UrlFetchResult(
            url=url,
            ok=ok,
            status=st or None,
            final_url=final,
            content_type=ctype.split(";")[0].strip() or "text/html",
            title=title,
            snippet=snippet,
            error="",
        )
    except HTTPError as e:
        body = b""
        try:
            body = e.read(MAX_BODY_BYTES) or b""
        except Exception:
            pass
        title, snippet = ("", "")
        ctype = e.headers.get("Content-Type", "") if e.headers else ""
        if body and _is_html(ctype, body):
            title, snippet = _parse_html(body)
        return UrlFetchResult(
            url=url,
            ok=False,
            status=int(e.code) if e.code else None,
            final_url=url,
            content_type=(ctype or "").split(";")[0].strip(),
            title=title,
            snippet=snippet,
            error=f"HTTP {e.code}",
        )
    except URLError as e:
        reason = getattr(e, "reason", e)
        return UrlFetchResult(url=url, ok=False, error=f"URLError: {reason}")
    except (TimeoutError, socket.timeout):
        return UrlFetchResult(url=url, ok=False, error="timeout")
    except Exception as e:
        logger.warning("fetch_url failed %s: %s", url, e)
        return UrlFetchResult(
            url=url, ok=False, error=f"{type(e).__name__}: {e}"
        )


def fetch_urls_for_citations(
    citations: Iterable[FootnoteCitation],
    *,
    workers: int = FETCH_WORKERS,
    progress=None,
) -> dict[str, list[UrlFetchResult]]:
    """
    Prefetch уникальных URL по всем сноскам.
    Жёсткий бюджет времени: неответившие → error=timeout/budget.
    """
    import time

    cites = list(citations)
    urls_by_fid: dict[str, list[str]] = {}
    unique_urls: list[str] = []
    seen_u: set[str] = set()
    for c in cites:
        urls = extract_urls(c.footnote_text)
        urls_by_fid[c.footnote_id] = urls
        for u in urls:
            if u not in seen_u:
                seen_u.add(u)
                unique_urls.append(u)

    fetched: dict[str, UrlFetchResult] = {}
    if unique_urls:
        n_workers = max(1, min(int(workers), 8, len(unique_urls)))
        if progress:
            try:
                progress(f"prefetch: {len(unique_urls)} ссылок (до {int(PREFETCH_BUDGET_SEC)} с)…")
            except Exception:
                pass

        t0 = time.monotonic()
        pool = ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="urlf")
        try:
            futs = {pool.submit(fetch_url, u): u for u in unique_urls}
            pending = set(futs)
            done_n = 0
            while pending and (time.monotonic() - t0) < PREFETCH_BUDGET_SEC:
                # короткий poll: забрать всё, что уже готово
                newly_done = [f for f in pending if f.done()]
                if not newly_done:
                    time.sleep(0.2)
                    continue
                for fut in newly_done:
                    u = futs[fut]
                    try:
                        fetched[u] = fut.result(timeout=0.05)
                    except Exception as e:
                        fetched[u] = UrlFetchResult(
                            url=u, ok=False, error=f"{type(e).__name__}: {e}"
                        )
                    pending.discard(fut)
                    done_n += 1
                if progress and done_n:
                    try:
                        progress(f"prefetch: {done_n}/{len(unique_urls)}…")
                    except Exception:
                        pass

            for fut in pending:
                u = futs[fut]
                if u not in fetched:
                    fetched[u] = UrlFetchResult(
                        url=u, ok=False, error="timeout (prefetch budget)"
                    )
                fut.cancel()
        finally:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)

        if progress:
            try:
                ok_n = sum(1 for r in fetched.values() if r.ok)
                progress(
                    f"prefetch готов: {ok_n}/{len(unique_urls)} ок "
                    f"за {time.monotonic() - t0:.0f} с"
                )
            except Exception:
                pass

    out: dict[str, list[UrlFetchResult]] = {}
    for c in cites:
        out[c.footnote_id] = [
            fetched.get(u)
            or UrlFetchResult(url=u, ok=False, error="not fetched")
            for u in urls_by_fid.get(c.footnote_id, [])
        ]
    return out


def format_fetched_block(results: list[UrlFetchResult] | None) -> str:
    """Текст блока FETCHED_URLS для промпта."""
    if not results:
        return "FETCHED_URLS: нет URL в тексте сноски"
    lines = ["FETCHED_URLS:"]
    for r in results:
        lines.append(f"- url: {r.url}")
        if r.final_url and r.final_url != r.url:
            lines.append(f"  final_url: {r.final_url}")
        if r.status is not None:
            lines.append(f"  status: {r.status}")
        if r.content_type:
            lines.append(f"  content_type: {r.content_type}")
        if r.title:
            lines.append(f"  title: {r.title}")
        if r.snippet:
            lines.append(f"  snippet: {r.snippet}")
        if r.error:
            lines.append(f"  error: {r.error}")
        if not r.ok and not r.error:
            lines.append("  error: fetch failed")
    return "\n".join(lines)
