"""Local-only OCR preview for broker transaction screenshots.

Images are decoded in memory, passed to RapidOCR, and discarded.  Only the
reviewed structured fills and irreversible hashes may later enter the journal.
The parser intentionally prefers a warning over inventing a missing fact.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import math
import re
import sqlite3
import threading
from pathlib import Path
from typing import Iterable


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}

_OCR = None
_OCR_LOCK = threading.Lock()


class JournalImageError(ValueError):
    """The uploaded bytes or recognizable transaction content are invalid."""


class JournalOCRUnavailable(RuntimeError):
    """The optional local OCR runtime is not installed or cannot start."""


def _decode_image(mime_type: str, content_b64: str):
    if mime_type not in ALLOWED_MIME:
        raise JournalImageError("仅支持 PNG、JPEG、WebP 图片")
    if not isinstance(content_b64, str) or not content_b64:
        raise JournalImageError("图片内容为空")
    raw_b64 = content_b64.split(",", 1)[1] if content_b64.startswith("data:") and "," in content_b64 else content_b64
    # Reject an oversized request before allocating the decoded byte buffer.
    if len(raw_b64) > (MAX_IMAGE_BYTES * 4 // 3 + 16):
        raise JournalImageError("单张图片不能超过 10 MB")
    try:
        raw = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise JournalImageError("图片 base64 内容无效") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise JournalImageError("单张图片不能超过 10 MB")

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # RapidOCR depends on Pillow, but keep the error actionable.
        raise JournalOCRUnavailable("当前服务缺少本地 OCR 运行组件，请执行 restart.sh 自动补齐依赖后重试") from exc
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = (probe.format or "").upper()
            if fmt not in {"PNG", "JPEG", "WEBP"}:
                raise JournalImageError("图片真实格式不是 PNG、JPEG 或 WebP")
            width, height = probe.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise JournalImageError("图片像素不能超过 2500 万")
            probe.verify()
        with Image.open(io.BytesIO(raw)) as loaded:
            image = loaded.convert("RGB")
            image.load()
    except JournalImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise JournalImageError("图片已损坏或无法解码") from exc
    return raw, image


def _ocr_engine():
    global _OCR
    if _OCR is not None:
        return _OCR
    try:
        from rapidocr import RapidOCR
    except (ImportError, OSError) as exc:
        raise JournalOCRUnavailable(
            "当前服务缺少本地 OCR 运行组件，请执行 restart.sh 自动补齐依赖后重试"
        ) from exc
    try:
        _OCR = RapidOCR()
    except Exception as exc:  # noqa: BLE001 - native runtime failures need a stable public error
        raise JournalOCRUnavailable(f"本地 OCR 初始化失败：{type(exc).__name__}") from exc
    return _OCR


def _normalize_ocr_result(result) -> list[dict]:
    """Accept both current RapidOCR output objects and the older tuple shape."""
    rows: list[dict] = []
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None) or getattr(result, "texts", None)
    scores = getattr(result, "scores", None)
    if boxes is not None and texts is not None:
        for box, text, score in zip(boxes, texts, scores or [1.0] * len(texts)):
            rows.append({"box": box, "text": str(text), "score": float(score)})
        return rows
    payload = result[0] if isinstance(result, tuple) and result else result
    for item in payload or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        box, text, score = item[0], item[1], item[2]
        rows.append({"box": box, "text": str(text), "score": float(score)})
    return rows


def _run_ocr(image) -> list[dict]:
    try:
        import numpy as np
    except ImportError as exc:
        raise JournalOCRUnavailable("本地 OCR 缺少 numpy 依赖") from exc
    with _OCR_LOCK:
        try:
            return _normalize_ocr_result(_ocr_engine()(np.asarray(image)))
        except JournalOCRUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise JournalImageError(f"图片识别失败：{type(exc).__name__}") from exc


def _bounds(box) -> tuple[float, float, float, float]:
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        return min(xs), min(ys), max(xs), max(ys)
    except (TypeError, ValueError, IndexError):
        return 0.0, 0.0, 0.0, 0.0


def _tokens_to_lines(tokens: Iterable[dict]) -> list[dict]:
    prepared = []
    for token in tokens:
        text = str(token.get("text") or "").strip()
        if not text:
            continue
        x1, y1, x2, y2 = _bounds(token.get("box"))
        prepared.append({"text": text, "score": float(token.get("score") or 0),
                         "x": x1, "y": (y1 + y2) / 2, "height": max(1.0, y2 - y1)})
    prepared.sort(key=lambda item: (item["y"], item["x"]))
    if not prepared:
        return []
    heights = sorted(item["height"] for item in prepared)
    tolerance = max(5.0, heights[len(heights) // 2] * 0.65)
    grouped: list[list[dict]] = []
    for token in prepared:
        if grouped and abs(token["y"] - sum(t["y"] for t in grouped[-1]) / len(grouped[-1])) <= tolerance:
            grouped[-1].append(token)
        else:
            grouped.append([token])
    lines = []
    for group in grouped:
        group.sort(key=lambda item: item["x"])
        lines.append({
            "text": " ".join(item["text"] for item in group),
            "score": round(sum(item["score"] for item in group) / len(group), 4),
            "tokens": group,
        })
    return lines


def _local_instruments() -> list[dict]:
    """Read the durable local market catalog without creating or updating it."""
    try:
        from .growth_ladders import _db_path

        path = Path(_db_path())
        if not path.is_file():
            return []
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            latest = db.execute("SELECT MAX(trade_date) AS d FROM session_instruments").fetchone()["d"]
            if not latest:
                return []
            return [dict(row) for row in db.execute(
                "SELECT code,name,market FROM session_instruments WHERE trade_date=?", (latest,)
            ).fetchall()]
        finally:
            db.close()
    except (OSError, sqlite3.Error, ValueError):
        return []


def _security_from_text(text: str, instruments: list[dict]) -> tuple[str, str, list[dict]]:
    codes = re.findall(r"(?<!\d)([03689]\d{5})(?!\d)", text)
    if codes:
        code = codes[0]
        matches = [row for row in instruments if str(row.get("code")) == code]
        return code, str(matches[0].get("name") or "") if matches else "", matches[:5]
    # Longest match wins so that a short alias cannot shadow the full security name.
    matches = [row for row in instruments if len(str(row.get("name") or "")) >= 2
               and str(row.get("name")) in text]
    matches.sort(key=lambda row: len(str(row.get("name") or "")), reverse=True)
    if not matches:
        return "", "", []
    longest = len(str(matches[0].get("name") or ""))
    best = [row for row in matches if len(str(row.get("name") or "")) == longest]
    unique = {str(row.get("code")): row for row in best}
    if len(unique) == 1:
        row = next(iter(unique.values()))
        return str(row.get("code") or ""), str(row.get("name") or ""), [row]
    return "", str(best[0].get("name") or ""), list(unique.values())[:5]


_SIDE_WORDS = (("清仓", "sell"), ("卖出", "sell"), ("减仓", "sell"),
               ("建仓", "buy"), ("买入", "buy"), ("加仓", "buy"))
_DATE_RE = re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?")
_NUMBER = r"[-+]?\d[\d,]*(?:\.\d+)?"


def _number_after(label_re: str, text: str) -> float | None:
    match = re.search(rf"(?:{label_re})\s*[:：]?\s*({_NUMBER})", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _date(text: str) -> str:
    match = _DATE_RE.search(text)
    if not match:
        return ""
    y, m, d = map(int, match.groups())
    try:
        import datetime

        return datetime.date(y, m, d).isoformat()
    except ValueError:
        return ""


def _reported_pnl_on_anchor(text: str) -> float | None:
    """Read a broker-reported per-exit P&L without mistaking fill fields for it."""
    tail = _DATE_RE.sub(" ", text)
    for word, _side in _SIDE_WORDS:
        tail = tail.replace(word, " ")
    if re.search(r"价格|成交价|均价|数量|成交量|股数|金额|成交额|税费|费用|手续费|佣金", tail):
        return None
    values = re.findall(_NUMBER, tail)
    if len(values) != 1:
        return None
    try:
        value = float(values[0].replace(",", ""))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _infer_unlabelled(block: str, day: str) -> tuple[float | None, float | None, float | None, float | None]:
    cleaned = _DATE_RE.sub(" ", block)
    values = []
    for raw in re.findall(_NUMBER, cleaned):
        try:
            values.append(float(raw.replace(",", "")))
        except ValueError:
            pass
    # Search a price × shares ≈ amount triple instead of trusting column order.
    for i, price in enumerate(values):
        if not 0 < price < 10000:
            continue
        for j, shares in enumerate(values):
            if i == j or not 0 < abs(shares) <= 1_000_000 or abs(shares - round(shares)) > 1e-6:
                continue
            for k, amount in enumerate(values):
                if k in (i, j) or amount <= 0:
                    continue
                expected = price * abs(shares)
                if abs(expected - amount) <= max(0.05, expected * 0.001):
                    remaining = [abs(v) for n, v in enumerate(values) if n not in (i, j, k) and 0 <= abs(v) < 1000]
                    return price, abs(shares), amount, remaining[-1] if remaining else None
    return None, None, None, None


def _warning(code: str, message: str, *, field: str = "", severity: str = "warning") -> dict:
    return {"code": code, "message": message, "field": field, "severity": severity}


def _parse_lines(lines: list[dict], instruments: list[dict] | None = None) -> dict:
    instruments = _local_instruments() if instruments is None else instruments
    full_text = "\n".join(str(line.get("text") or "") for line in lines)
    code, name, security_candidates = _security_from_text(full_text, instruments)
    anchors = []
    for index, line in enumerate(lines):
        text = str(line.get("text") or "")
        side = next((mapped for word, mapped in _SIDE_WORDS if word in text), None)
        if side and _DATE_RE.search(text):
            anchors.append((index, side))
    fills, warnings, reported_pnl_parts = [], [], []
    for pos, (start, side) in enumerate(anchors):
        end = anchors[pos + 1][0] if pos + 1 < len(anchors) else len(lines)
        block_lines = lines[start:end]
        block = " ".join(str(line.get("text") or "") for line in block_lines)
        day = _date(block)
        price = _number_after("价格|成交价|均价", block)
        shares = _number_after("数量|成交量|股数", block)
        amount = _number_after("金额|成交额", block)
        fee = _number_after("税费|费用|手续费|佣金", block)
        if price is None or shares is None:
            guessed_price, guessed_shares, guessed_amount, guessed_fee = _infer_unlabelled(block, day)
            price = price if price is not None else guessed_price
            shares = shares if shares is not None else guessed_shares
            amount = amount if amount is not None else guessed_amount
            fee = fee if fee is not None else guessed_fee
        if side == "sell":
            candidate = _reported_pnl_on_anchor(str(block_lines[0].get("text") or ""))
            if candidate is not None:
                reported_pnl_parts.append(candidate)
        fill = {"side": side, "date": day, "price": price or 0, "shares": abs(shares or 0)}
        if fee is not None and math.isfinite(fee) and fee >= 0:
            fill["fee"] = round(fee, 2)
        confidence = min((float(line.get("score") or 0) for line in block_lines), default=0)
        fill["confidence"] = round(confidence, 3)
        if amount is not None:
            fill["reported_amount"] = round(abs(amount), 2)
        fill_warnings = []
        if not day:
            fill_warnings.append(_warning("missing_date", "未识别到完整成交日期", field="date", severity="error"))
        if not price:
            fill_warnings.append(_warning("missing_price", "未识别到成交价格", field="price", severity="error"))
        if not shares:
            fill_warnings.append(_warning("missing_shares", "未识别到成交数量", field="shares", severity="error"))
        if price and shares and amount is not None:
            expected = price * abs(shares)
            if abs(expected - abs(amount)) > max(0.05, expected * 0.002):
                fill_warnings.append(_warning("amount_mismatch", "价格×数量与截图金额不一致，请核对", field="amount"))
        if confidence < 0.72:
            fill_warnings.append(_warning("low_confidence", "这一行 OCR 置信度较低，请逐项核对"))
        fill["warnings"] = fill_warnings
        warnings.extend(fill_warnings)
        fills.append(fill)
    if not anchors:
        warnings.append(_warning("no_transactions", "没有识别到带完整日期的买卖记录", severity="error"))
    if not code:
        warnings.append(_warning("missing_code", "截图未显示代码且本地股票目录无法唯一匹配，请填写代码", field="code", severity="error"))
    if fills and not any(fill["side"] == "buy" for fill in fills):
        warnings.append(_warning("sell_only", "截图只有卖出记录，只能追加到已有交易或补齐买入", severity="error"))
    reported_pnl = round(math.fsum(reported_pnl_parts), 2) if reported_pnl_parts else None
    return {"code": code, "name": name, "security_candidates": security_candidates,
            "fills": fills, "reported_pnl": reported_pnl, "warnings": warnings}


def preview_image(client_id: str, mime_type: str, content_b64: str) -> dict:
    raw, image = _decode_image(str(mime_type or "").lower(), content_b64)
    source_hash = hashlib.sha256(raw).hexdigest()
    tokens = _run_ocr(image)
    lines = _tokens_to_lines(tokens)
    parsed = _parse_lines(lines)
    from . import journal

    candidates = journal.image_import_candidates(parsed["code"], parsed["fills"], source_hash)
    confidence = round(sum(float(row.get("score") or 0) for row in tokens) / len(tokens), 3) if tokens else 0.0
    draft_id = hashlib.sha256((source_hash + "\0" + str(client_id)).encode()).hexdigest()[:16]
    return {
        "client_id": str(client_id)[:100], "source_hash": source_hash,
        "ocr": {"confidence": confidence, "line_count": len(lines)},
        "drafts": [{
            "draft_id": draft_id, "source_hash": source_hash,
            "code": parsed["code"], "name": parsed["name"], "playbook": "其它",
            "as_planned": None, "note": "", "fills": parsed["fills"],
            "reported_pnl": parsed["reported_pnl"], "confidence": confidence,
            "warnings": parsed["warnings"], "security_candidates": parsed["security_candidates"],
            "existing_candidates": candidates["trades"], "duplicate": candidates["duplicate"],
            "raw_lines": [str(line.get("text") or "")[:300] for line in lines[:120]],
        }],
    }
