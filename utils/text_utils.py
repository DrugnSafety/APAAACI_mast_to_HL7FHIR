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


# ---------------------------------------------------------------------------
# 조사 고르기 — 받침 유무에 맞는 조사 한 가지만 쓴다('고양이과(와)' 같은 병기를 내보내지 않는다)
# ---------------------------------------------------------------------------
# 숫자를 한국어로 읽었을 때 받침이 있는 것: 0(영) 1(일) 3(삼) 6(육) 7(칠) 8(팔). ㄹ 받침은 1·7·8.
_DIGIT_FINAL = {"0": "ㅇ", "1": "ㄹ", "3": "ㅁ", "6": "ㄱ", "7": "ㄹ", "8": "ㄹ"}
_TRAILING_NOISE = " \t\"'’”」』》〉]}.,!?…·*_`"
_VOWELS = "aeiouy"


def _last_sound(word: str) -> str:
    """낱말의 마지막 소리: ""(받침 없음) / "ㄹ" / "ㅇ"(그 밖의 받침).

    뒤에 붙은 괄호 설명은 읽지 않는 말로 보고 괄호 앞 낱말에 맞춘다('집먼지진드기(유럽·미국 두 종)' → '기').
    한글은 종성으로 정확히 가린다. 숫자는 한국어로 읽은 소리에, 로마자는 외래어 표기 관행에 맞춘다:
    l·le → ㄹ, m·n·ng → 받침, 짧은 모음 뒤의 p·t·k·b·ck → 받침(cat 캣, crab 크랩), 그 밖은 받침 없음(dog 도그, milk 밀크).
    """
    text = str(word or "")
    while True:
        text = text.rstrip(_TRAILING_NOISE)
        if text.endswith((")", "）")):
            cut = max(text.rfind("("), text.rfind("（"))
            if cut > 0:
                text = text[:cut]
                continue
            text = text[:-1]
            continue
        break
    if not text:
        return ""
    ch = text[-1]
    if "가" <= ch <= "힣":
        final = (ord(ch) - 0xAC00) % 28
        return "" if final == 0 else ("ㄹ" if final == 8 else "ㅇ")
    if ch.isdigit():
        f = _DIGIT_FINAL.get(ch, "")
        return "" if not f else ("ㄹ" if f == "ㄹ" else "ㅇ")
    low = text.lower()
    if ch.isascii() and ch.isalpha():
        if low.endswith(("l", "le")):      # apple 애플, maple 메이플
            return "ㄹ"
        if low.endswith(("m", "n", "ng")):
            return "ㅇ"
        if low.endswith("ck"):
            return "ㅇ"
        if low[-1] in "ptkb" and len(low) >= 2 and low[-2] in _VOWELS and (len(low) < 3 or low[-3] not in _VOWELS):
            return "ㅇ"
        return ""
    return ""


# 조사 짝: (받침 있을 때, 받침 없을 때). '으로/로' 는 ㄹ 받침 뒤에서도 '로' 다.
_JOSA = {
    "은는": ("은", "는"), "이가": ("이", "가"), "을를": ("을", "를"), "과와": ("과", "와"),
    "으로": ("으로", "로"), "이에요": ("이에요", "예요"), "이라": ("이라", "라"), "아야": ("아", "야"),
}
_JOSA_ALIASES = {"는은": "은는", "가이": "이가", "를을": "을를", "와과": "과와", "로": "으로", "예요": "이에요"}


def josa(word: str, kind: str) -> str:
    """word 뒤에 붙일 조사 한 가지. kind: '은는'·'이가'·'을를'·'과와'·'으로'·'이에요' (순서를 바꿔 써도 된다).

    조사만 돌려준다 — 낱말을 이스케이프(md_text·html.escape)한 뒤에 붙일 수 있게.
    """
    with_final, without_final = _JOSA[_JOSA_ALIASES.get(kind, kind)]
    sound = _last_sound(word)
    if not sound:
        return without_final
    if sound == "ㄹ" and with_final == "으로":
        return "로"
    return with_final


def with_josa(word: str, kind: str) -> str:
    """word + 알맞은 조사."""
    return f"{word}{josa(word, kind)}"
