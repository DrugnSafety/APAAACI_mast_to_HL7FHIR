"""Translation Service — 서버가 만든 한국어 콘텐츠(문진·지식베이스·리포트·카드뉴스)를 번역한다.

왜 사전이 아니라 번역기인가
  화면 크롬(버튼·헤더)은 문구가 고정이라 `web/i18n.js` 사전으로 충분하다. 반면 서버 콘텐츠는
  항원 148종 × 지식베이스 필드 + 환자별로 조합되는 문진·리포트 문장이라 사전으로 감당되지 않는다.
  그래서 LLM 번역 + **영구 캐시**를 쓴다. 같은 한국어 문장은 평생 한 번만 번역된다.

안전장치
  - 한국어(ko) 요청은 번역을 아예 거치지 않는다(기존 동작·테스트 불변).
  - API 키가 없으면 원문(한국어)을 그대로 돌려준다. 조용히 비우지 않는다.
  - 숫자·단위·°C·kU/L·괄호 안 학명은 그대로 두도록 프롬프트로 고정한다.
  - 번역문은 기계 번역이므로 UI 가 그 사실을 표시한다(i18n `notice.machine`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from config.settings import BASE_DIR, settings

logger = logging.getLogger(__name__)

CACHE_PATH = BASE_DIR / "data" / "i18n_cache.json"
SUPPORTED = ("ko", "en", "zh")
LANG_NAME = {"en": "English", "zh": "Simplified Chinese (简体中文)"}
BATCH_CHARS = 3500          # 한 번에 보낼 최대 문자 수
BATCH_ITEMS = 40
MAX_SPLIT_DEPTH = 4         # 길이 불일치 시 묶음을 반으로 나눠 재시도하는 최대 깊이

_SYSTEM = (
    "You translate patient-facing allergy test content from Korean.\n"
    "Rules:\n"
    "1. Translate meaning faithfully. This is medical education text: never add, remove or soften "
    "a clinical instruction, and never invent findings.\n"
    "2. Keep EXACTLY as-is: numbers, units (mm, kU/L, ℃, %), class values, dates, Latin species names, "
    "text inside parentheses that is already English or Latin, and emoji.\n"
    "3. Preserve inline markup exactly: **bold**, `code`, <b>, <br/>, markdown headings/lists, and any "
    "leading list markers or numbering.\n"
    "3b. Placeholders like ⟦0⟧ ⟦12⟧ are markup. Keep every one of them, unchanged, and keep them in "
    "positions that make sense for the translated sentence. Never drop, merge or renumber them.\n"
    "4. Do not translate proper allergen brand/test names (MAST, UniCAP, ImmunoCAP, SPT, FHIR, SNOMED).\n"
    "3c. Keep personal names exactly as written in the source script. Do not transliterate or "
    "localize a patient's name.\n"
    "4b. Keep markdown tables (| … |) with the same number of columns, and keep raw HTML lines "
    "(e.g. <div class=\"detail-more\" markdown=\"1\">, </div>) byte-identical on their own lines.\n"
    "5. Return ONLY {\"translations\": [ ... ]} — a JSON object whose single key is \"translations\" and "
    "whose value is an array of translated strings with EXACTLY the same length and order as the input "
    "array. One input string maps to one output string, even if it contains newlines: never split a "
    "multi-line string into several array items, and never merge two inputs into one."
)


class TranslationService:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")
                        if api_key is None else api_key)
        self.client = None
        if self.api_key and self.api_key != "your_openai_api_key_here":
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=self.api_key)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"번역용 OpenAI 클라이언트 초기화 실패: {e}")
        self._lock = threading.Lock()
        self._cache: Dict[str, str] = {}
        self._dirty = False
        self._load()

    # ---------------- 캐시 ----------------
    def _load(self):
        try:
            if CACHE_PATH.exists():
                self._cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")).get("map", {})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"번역 캐시 로드 실패: {e}")
            self._cache = {}

    def save(self):
        if not self._dirty:
            return
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(
                {"note_ko": "서버 생성 콘텐츠 기계번역 캐시. key = '<lang>:<sha1(원문)>'",
                 "count": len(self._cache), "map": self._cache},
                ensure_ascii=False, indent=0), encoding="utf-8")
            self._dirty = False
        except Exception as e:  # noqa: BLE001
            logger.warning(f"번역 캐시 저장 실패: {e}")

    @staticmethod
    def _key(text: str, lang: str) -> str:
        return f"{lang}:{hashlib.sha1(text.encode('utf-8')).hexdigest()}"

    # ---------------- 번역 ----------------
    @staticmethod
    def _needs_translation(text: Any) -> bool:
        return isinstance(text, str) and bool(re.search(r"[가-힣]", text))

    def translate_batch(self, texts: List[str], lang: str) -> List[str]:
        """한국어 문자열 목록을 번역. 캐시 우선, 없는 것만 LLM 으로 채운다."""
        if lang not in ("en", "zh") or not texts:
            return list(texts)
        out = list(texts)
        misses: List[int] = []
        for i, t in enumerate(texts):
            if not self._needs_translation(t):
                continue
            hit = self._cache.get(self._key(t, lang))
            if hit is not None:
                out[i] = hit
            else:
                misses.append(i)
        if not misses:
            return out
        if not self.client:
            return out          # 키 없음 → 원문 유지

        for chunk in self._chunks(misses, texts):
            src = [texts[i] for i in chunk]
            got = self._call_exact(src, lang)
            if not got:
                continue
            with self._lock:
                for idx, translated in zip(chunk, got):
                    if isinstance(translated, str) and translated.strip():
                        out[idx] = translated
                        self._cache[self._key(texts[idx], lang)] = translated
                        self._dirty = True
        self.save()
        return out

    def _call_exact(self, src: List[str], lang: str, depth: int = 0) -> Optional[List[Optional[str]]]:
        """src 와 길이가 같은 목록을 돌려준다. 번역하지 못한 자리는 None 이다.

        모델이 배열 길이를 어기는 일이 드물게 있다(여러 줄 문자열을 쪼개거나 항목을 합침).
        예전에는 그 묶음 전체를 버려서 사용자에게 한국어가 그대로 보였다. 이제는 묶음을
        반씩 나눠 다시 시도하고, 한 항목까지 내려가면 길이 검증이 자명해진다.
        실패한 자리를 None 으로 남기는 이유는, 원문(한국어)을 번역 결과로 캐시에 굳히면
        이후 요청에서 영영 한국어가 나오기 때문이다."""
        got = self._call(src, lang)
        if got is not None and len(got) == len(src):
            return list(got)

        n_got = len(got) if got is not None else 0
        if len(src) == 1:
            # 한 항목을 여러 줄로 쪼갠 경우만 되붙여 살린다.
            if got is not None and n_got > 1 and all(isinstance(x, str) for x in got):
                return ["\n".join(got)]
            logger.warning(f"번역 실패({lang}): 단일 항목 응답 {n_got}건 — 원문 유지")
            return [None]
        if depth >= MAX_SPLIT_DEPTH:
            logger.warning(f"번역 분할 한도 초과({lang}): {n_got}/{len(src)} — 원문 유지")
            return [None] * len(src)

        logger.info(f"번역 응답 길이 불일치({lang}): {n_got}/{len(src)} — 절반으로 나눠 재시도")
        mid = len(src) // 2
        left = self._call_exact(src[:mid], lang, depth + 1) or [None] * mid
        right = self._call_exact(src[mid:], lang, depth + 1) or [None] * (len(src) - mid)
        return left + right

    @staticmethod
    def _chunks(indices: List[int], texts: List[str]):
        cur, size = [], 0
        for i in indices:
            n = len(texts[i])
            if cur and (size + n > BATCH_CHARS or len(cur) >= BATCH_ITEMS):
                yield cur
                cur, size = [], 0
            cur.append(i)
            size += n
        if cur:
            yield cur

    def _call(self, src: List[str], lang: str) -> Optional[List[str]]:
        try:
            resp = self.client.chat.completions.create(
                model=getattr(settings, "openai_chat_model", None) or "gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user",
                     "content": f"Target language: {LANG_NAME[lang]}\n"
                                f"Translate these {len(src)} strings. Return exactly {len(src)} "
                                f"translations in the same order.\n"
                                f"{json.dumps(src, ensure_ascii=False)}"},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                max_tokens=4000,
            )
            raw = (resp.choices[0].message.content or "").strip()
            data = json.loads(raw)
            if isinstance(data, list):
                return data
            for name in ("translations", "result", "results", "items", "output"):
                v = data.get(name)
                if isinstance(v, list):
                    return v
            # 키 이름을 못 맞춘 응답: 길이가 맞는 리스트를 우선 고른다
            lists = [v for v in data.values() if isinstance(v, list)]
            for v in lists:
                if len(v) == len(src):
                    return v
            return lists[0] if lists else None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"번역 호출 실패({lang}): {e}")
            return None

    # ---------------- 구조 번역 헬퍼 ----------------
    def translate_obj(self, obj: Any, lang: str, keys: Iterable[str]) -> Any:
        """중첩 dict/list 에서 지정한 키의 문자열 값만 번역한다(구조·식별자는 건드리지 않음)."""
        if lang not in ("en", "zh"):
            return obj
        keys = set(keys)
        targets: List[Any] = []          # (container, key) 쌍
        texts: List[str] = []

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in keys and self._needs_translation(v):
                        targets.append((node, k))
                        texts.append(v)
                    else:
                        walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(obj)
        if not texts:
            return obj
        for (container, k), tr in zip(targets, self.translate_batch(texts, lang)):
            container[k] = tr
        return obj

    def translate_text(self, text: str, lang: str) -> str:
        if not self._needs_translation(text) or lang not in ("en", "zh"):
            return text
        return self.translate_batch([text], lang)[0]

    _TEXT_NODE = re.compile(r">([^<>]+)<")
    _BOLD_OPEN = re.compile(r"<(?:b|strong)\b[^>]*>", re.I)
    _BOLD_CLOSE = re.compile(r"</(?:b|strong)\s*>", re.I)
    _VOID = re.compile(r"<(?:br|hr)\b[^>]*/?>", re.I)
    _PH = re.compile(r"⟦(\d+)⟧")
    _MD_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)

    def translate_html(self, html: str, lang: str) -> str:
        """자체 생성 HTML(카드뉴스)의 텍스트만 번역. 태그·속성·스타일은 손대지 않는다.

        문장이 조각나면 번역 품질이 떨어지므로, 문장 안에 끼는 마크업을 먼저 정리한다.
          - <b>/<strong> → **굵게** (LLM 이 잘 보존하는 표기) → 번역 후 태그로 복원
          - <br>/<hr>    → 자리표시자 ⟦n⟧ → 번역 후 원래 태그로 복원
        번역문에서 자리표시자가 유실되면 그 문장만 원문을 유지한다(마크업 파손 방지)."""
        if lang not in ("en", "zh") or not html:
            return html

        # <style>/<script> 안은 번역 대상이 아니다(주석의 한국어까지 건드리면 CSS 가 깨진다)
        blocks: List[str] = []

        def keep(m):
            blocks.append(m.group(0))
            return f"<!--K{len(blocks) - 1}-->"

        html = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", keep, html, flags=re.S | re.I)

        void: List[str] = []

        def stash(m):
            void.append(m.group(0))
            return f"⟦{len(void) - 1}⟧"

        masked = self._VOID.sub(stash, html)
        masked = self._BOLD_OPEN.sub("**", masked)
        masked = self._BOLD_CLOSE.sub("**", masked)

        uniq: List[str] = []
        seen = set()
        for t in self._TEXT_NODE.findall(masked):
            if self._needs_translation(t) and t not in seen:
                seen.add(t)
                uniq.append(t)
        table = {}
        if uniq:
            for src, tr in zip(uniq, self.translate_batch(uniq, lang)):
                if sorted(self._PH.findall(src)) != sorted(self._PH.findall(tr)):
                    logger.warning("번역문에서 줄바꿈 자리표시자가 유실되어 원문을 유지합니다.")
                    continue
                if tr.count("**") % 2:      # 굵게 표기가 깨졌으면 강조만 버린다
                    tr = tr.replace("**", "")
                table[src] = tr

        out = self._TEXT_NODE.sub(lambda m: ">" + table.get(m.group(1), m.group(1)) + "<", masked)
        out = self._MD_BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", out)
        out = out.replace("**", "")         # 짝이 남지 않은 표기 정리
        out = self._PH.sub(lambda m: void[int(m.group(1))], out)
        return re.sub(r"<!--K(\d+)-->", lambda m: blocks[int(m.group(1))], out)

    def translate_markdown(self, md: str, lang: str) -> str:
        """마크다운을 빈 줄 기준 블록으로 나눠 번역. 블록 단위라 캐시 재사용이 잘 된다."""
        if lang not in ("en", "zh") or not md:
            return md
        blocks = md.split("\n\n")
        idx = [i for i, b in enumerate(blocks) if self._needs_translation(b)]
        if not idx:
            return md
        translated = self.translate_batch([blocks[i] for i in idx], lang)
        for i, t in zip(idx, translated):
            blocks[i] = t
        return "\n\n".join(blocks)


_svc: Optional[TranslationService] = None
_last_key: Optional[str] = None


def get_translation_service(api_key: Optional[str] = None) -> TranslationService:
    global _svc, _last_key
    key = (settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")) if api_key is None else api_key
    if _svc is None or _last_key != key:
        _svc = TranslationService(api_key=key)
        _last_key = key
    return _svc
