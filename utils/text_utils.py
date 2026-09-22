"""문장 단위 줄이기.

카드뉴스·리포트의 설명문을 글자수로 자르면 '온도 20~2…' 처럼 문장 한가운데서 끊긴다.
숫자·단위가 끊기면 뜻이 바뀌므로, 항상 문장 경계에서만 자른다.
"""
from __future__ import annotations

import re

# 마침표 뒤 공백, 또는 한국어 종결어미('니다.' '요.') 뒤에서 문장을 나눈다.
# '0.2~0.3mm' 같은 소수점은 뒤에 공백이 없으므로 나뉘지 않는다.
_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split((text or "").strip()) if s.strip()]


def trim_sentences(text: str, max_len: int, max_sentences: int | None = None) -> str:
    """앞에서부터 온전한 문장만 max_len 안에서 모은다.

    첫 문장이 max_len 보다 길어도 자르지 않고 통째로 쓴다 — 잘린 문장보다 긴 문장이 낫다.
    """
    parts = split_sentences(text)
    if not parts:
        return ""
    out = [parts[0]]
    for s in parts[1:]:
        if max_sentences is not None and len(out) >= max_sentences:
            break
        if len(" ".join(out + [s])) > max_len:
            break
        out.append(s)
    return " ".join(out)
