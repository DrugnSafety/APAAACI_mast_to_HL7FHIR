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

import copy
import hashlib
import html as html_lib
import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from config.settings import BASE_DIR, settings

logger = logging.getLogger(__name__)

# I18N_CACHE_PATH 로 캐시 파일을 바꿀 수 있다. 테스트(conftest.py)가 실제 캐시를 읽거나 쓰지 않게 하는 데 쓴다.
CACHE_PATH = Path(os.getenv("I18N_CACHE_PATH") or BASE_DIR / "data" / "i18n_cache.json")
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
    "3b. Placeholders like ⟦0⟧ ⟦12⟧ ⟦P0⟧ ⟦P3⟧ are markup or values filled in later (a name, a date, "
    "a number). Keep every one of them, unchanged, and keep them in positions that make sense for the "
    "translated sentence. Never drop, merge, renumber or translate them.\n"
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


# ============================================================
# 사용자 값 보호 + 미번역 집계
# ============================================================
# 환자가 입력했거나 결과지에서 읽은 값(이름·기관·자유 기재·레지스트리에 없는 항원명)은 일반 문장과 같은
# 번역 요청·같은 영구 캐시에 넣지 않는다. 번역 전에 ⟦P0⟧ 같은 자리표시자로 바꾸고 번역 뒤에 되돌린다.
#   - 캐시 키와 캐시 값에는 자리표시자만 남는다(환자 식별 정보가 추적 중인 캐시 파일로 가지 않는다).
#   - 일반 문장을 번역하는 LLM 요청에 공격자가 고른 문자열이 섞이지 않는다(캐시 오염 방지).
#   - 자유 기재 값은 따로 묶어 번역하고 캐시하지 않는다. 이름·기관명은 번역하지 않고 그대로 둔다.
# 날짜(YYYY-MM-DD)는 범위 밖에서도 항상 자리표시자로 바꾼다 — 검사일이 캐시에 남지 않고, 작성일이
# 바뀔 때마다 같은 문장을 다시 번역하지도 않는다.
_SLOT = re.compile(r"⟦P(\d+)⟧")
_DATE = r"(?<!\d)\d{4}[-./]\d{1,2}[-./]\d{1,2}(?!\d)"
_SEX_LABEL = {"남성": {"en": "Male", "zh": "男性"}, "여성": {"en": "Female", "zh": "女性"}}
_HANGUL = re.compile(r"[가-힣]")
MIN_PROTECTED_LEN = 2

# 자리(slot): (종류, 원문에 있던 표기, 값, HTML 이스케이프된 표기였는가)
#   keep = 그대로 되돌림(이름·날짜·나이) / sex = 언어별 표기로 되돌림 / free = 따로 번역한 값으로 되돌림
Slot = Tuple[str, str, str, bool]


class TranslationScope:
    """한 요청(한 환자)의 번역 범위. 보호할 값과, 번역하지 못한 문장 집계를 담는다."""

    def __init__(self, identity: Iterable[Any] = (), free_text: Iterable[Any] = (), age: Any = None):
        self.total = 0                       # 번역 대상이었던 문장 수
        self.untranslated: List[str] = []    # 번역하지 못한 문장(자리표시자로 가린 형태)
        self.errors = 0                      # 번역기 밖에서 난 실패(호출부가 올린다)
        self._free_tr: Dict[str, Dict[str, str]] = {}
        self._lits: Dict[str, Tuple[str, str, bool]] = {}
        for kind, values in (("free", free_text), ("keep", identity)):     # 겹치면 identity 가 이긴다
            for v in values:
                v = str(v).strip() if v is not None else ""
                if len(v) < MIN_PROTECTED_LEN:
                    continue
                for variant in (v, html_lib.escape(v, quote=False), html_lib.escape(v, quote=True)):
                    self._lits[variant] = (kind, v, variant != v)
        parts = []
        if self._lits:
            parts.append("(?P<lit>" + "|".join(
                re.escape(k) for k in sorted(self._lits, key=len, reverse=True)) + ")")
        parts.append(f"(?P<date>{_DATE})")
        if isinstance(age, int) and not isinstance(age, bool) and 0 <= age < 150:
            parts.append(rf"(?P<age>(?<!\d){age}(?=\s?세))")
        self._rx = re.compile("|".join(parts))

    def mask(self, text: str) -> Tuple[str, List[Slot]]:
        slots: List[Slot] = []
        index: Dict[str, int] = {}

        def put(kind: str, token: str, value: str, escaped: bool) -> str:
            if token not in index:
                index[token] = len(slots)
                slots.append((kind, token, value, escaped))
            return f"⟦P{index[token]}⟧"

        def sub(m):
            token = m.group(0)
            if m.lastgroup == "lit":
                kind, value, escaped = self._lits[token]
                return put(kind, token, value, escaped)
            return put("keep", token, token, False)

        masked = self._rx.sub(sub, text)
        # 성별은 이름·나이와 같은 줄(인적사항 줄)에서만 가린다. 다른 문장의 '남성/여성' 은 일반 어휘다.
        if any(kind == "keep" and not re.fullmatch(_DATE, token) for kind, token, _v, _e in slots):
            for label in _SEX_LABEL:
                if label in masked:
                    masked = masked.replace(label, put("sex", label, label, False))
        return masked, slots

    def resolve_free(self, values: List[str], lang: str, svc: "TranslationService") -> Dict[str, str]:
        """자유 기재 값의 번역. 이 범위 안에서 값마다 한 번만 부르고, 캐시에는 넣지 않는다."""
        done = self._free_tr.setdefault(lang, {})
        todo = [v for v in values if v not in done]
        if todo:
            done.update({v: "" for v in todo})
            done.update(svc._translate_uncached(todo, lang))
        return {v: t for v, t in done.items() if t}


_PLAIN = TranslationScope()          # 범위 밖 호출용(날짜만 가린다). 집계에는 쓰지 않는다.
_scope: ContextVar[Optional[TranslationScope]] = ContextVar("translation_scope", default=None)


@contextmanager
def translation_scope(identity: Iterable[Any] = (), free_text: Iterable[Any] = (),
                      age: Any = None) -> Iterator[TranslationScope]:
    """이 블록 안의 번역은 identity/free_text 값을 가리고, 번역하지 못한 문장을 scope 에 모은다."""
    scope = TranslationScope(identity, free_text, age)
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


def current_scope() -> Optional[TranslationScope]:
    return _scope.get()


def _slots_ok(text: str, slots: List[Slot]) -> bool:
    """번역문에 자리표시자가 빠짐없이, 없는 번호 없이 들어 있는가."""
    return {int(n) for n in _SLOT.findall(text)} == set(range(len(slots)))


def _restore(text: str, slots: List[Slot], lang: str, free_tr: Dict[str, str]) -> str:
    def sub(m):
        i = int(m.group(1))
        if i >= len(slots):
            return m.group(0)
        kind, token, value, escaped = slots[i]
        if kind == "sex":
            return _SEX_LABEL[token].get(lang, token)
        if kind == "free" and free_tr.get(value):
            return html_lib.escape(free_tr[value]) if escaped else free_tr[value]
        return token
    return _SLOT.sub(sub, text)


_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_TAG_START = re.compile(r"<(?=[A-Za-z/!?])")
_BARE_AMP = re.compile(r"&(?!#?\w+;)")
# 링크 목적지: 인라인 `](목적지` 와 참조 정의 `[이름]: 목적지`(인용문·목록 안에서도 정의로 읽힌다).
_LINK_DEST = re.compile(r"\]\(\s*<?([^\s<>)]*)|\[[^\]\n]+\]:[ \t]*\n?[ \t]*<?([^\s<>]*)")
_WEB_LINK = re.compile(r"https?://", re.I)
_ATTR_BRACE = str.maketrans({"{": "&#123;", "}": "&#125;"})


def _link_dests(md: str) -> set:
    return {a or b for a, b in _LINK_DEST.findall(md)}


def _adds_unsafe_link(src: str, translated: str) -> bool:
    """번역문에 원문에 없던 링크 목적지가 생겼고, 그것이 평범한 웹 주소가 아닌가.

    위험한 스킴을 골라내는 방식은 엔티티(`&#106;avascript:`, `java&Tab;script:`, `&colon;`)나
    `<javascript:…>` 표기로 빠져나간다. 그래서 새 목적지는 http(s) 로 시작할 때만 받아들인다."""
    return any(not _WEB_LINK.match(d) for d in _link_dests(translated) - _link_dests(src))


def _escape_text_node(text: str) -> str:
    """번역문을 HTML 텍스트 노드에 넣기 전 이스케이프한다. 원문에 있던 엔티티(&amp; 등)는 그대로 둔다."""
    return _BARE_AMP.sub("&amp;", text).replace("<", "&lt;").replace(">", "&gt;")


def _neutralize_new_markup(src: str, translated: str) -> str:
    """마크다운 블록 번역문에서 원문에 없던 HTML 태그를 글자로 바꾼다(원문의 태그는 그대로 둔다)."""
    allowed = set(_TAG.findall(src))
    out, pos = [], 0
    for m in _TAG.finditer(translated):
        out.append(_TAG_START.sub("&lt;", translated[pos:m.start()]))
        out.append(m.group(0) if m.group(0) in allowed else html_lib.escape(m.group(0), quote=False))
        pos = m.end()
    out.append(_TAG_START.sub("&lt;", translated[pos:]))
    return "".join(out)


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
        """캐시를 원자적으로 저장한다.

        FastAPI 는 sync 엔드포인트를 스레드풀에서 돌리므로 요청 여러 개가 동시에 캐시를
        고칠 수 있다. 잠금 없이 json.dumps 로 넘기면 순회 도중 dict 가 바뀌어 터지고,
        같은 경로에 곧바로 쓰면 쓰다가 죽었을 때 캐시 파일이 잘린 채 남는다.
        스냅샷을 잠금 안에서 뜨고, 임시 파일에 쓴 뒤 교체한다."""
        if not self._dirty:
            return
        with self._lock:
            if not self._dirty:
                return
            snapshot = dict(self._cache)
            self._dirty = False
        tmp = CACHE_PATH.with_suffix(CACHE_PATH.suffix + f".tmp{os.getpid()}")
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(
                {"note_ko": "서버 생성 콘텐츠 기계번역 캐시. key = '<lang>:<sha1(원문)>'",
                 "count": len(snapshot), "map": snapshot},
                ensure_ascii=False, indent=0), encoding="utf-8")
            os.replace(tmp, CACHE_PATH)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"번역 캐시 저장 실패: {e}")
            self._dirty = True          # 다음 기회에 다시 저장한다
            try:
                tmp.unlink(missing_ok=True)
            except Exception:           # noqa: BLE001
                pass

    @staticmethod
    def _key(text: str, lang: str) -> str:
        return f"{lang}:{hashlib.sha1(text.encode('utf-8')).hexdigest()}"

    # ---------------- 번역 ----------------
    @staticmethod
    def _needs_translation(text: Any) -> bool:
        return isinstance(text, str) and bool(re.search(r"[가-힣]", text))

    def _llm_ready(self) -> bool:
        from services import llm_backend
        return bool(self.client) or (llm_backend.active() == "ollama" and llm_backend.is_available("ollama"))

    def translate_batch(self, texts: List[str], lang: str) -> List[str]:
        """한국어 문자열 목록을 번역. 캐시 우선, 없는 것만 LLM 으로 채운다.

        사용자 값은 자리표시자로 가린 뒤에 캐시를 찾고 번역한다(위 '사용자 값 보호' 참고).
        번역하지 못한 문장은 원문 그대로 돌려주고, 번역 범위(translation_scope)가 있으면 거기에 적는다."""
        if lang not in ("en", "zh") or not texts:
            return list(texts)
        scope = _scope.get()
        masker = scope or _PLAIN
        out = list(texts)
        work: Dict[int, Tuple[str, List[Slot]]] = {}
        free_vals = set()
        for i, t in enumerate(texts):
            if not isinstance(t, str) or not t:
                continue
            masked, slots = masker.mask(t)
            free = {v for kind, _tok, v, _e in slots if kind == "free" and self._needs_translation(v)}
            if self._needs_translation(masked) or free:
                work[i] = (masked, slots)
                free_vals |= free
        if not work:
            return out
        free_tr = masker.resolve_free(sorted(free_vals), lang, self) if free_vals else {}

        misses: List[int] = []
        for i, (masked, slots) in work.items():
            if not self._needs_translation(masked):      # 사용자 값만 한국어인 문장
                out[i] = _restore(masked, slots, lang, free_tr)
                continue
            if scope is not None:
                scope.total += 1
            hit = self._cache.get(self._key(masked, lang))
            if hit is not None and _slots_ok(hit, slots):
                out[i] = _restore(hit, slots, lang, free_tr)
            else:
                misses.append(i)

        done = set()
        if misses and self._llm_ready():
            sources = {i: work[i][0] for i in misses}
            for chunk in self._chunks(misses, sources):
                got = self._call_exact([sources[i] for i in chunk], lang)
                if not got:
                    continue
                for idx, translated in zip(chunk, got):
                    masked, slots = work[idx]
                    # 원문을 그대로 돌려준 응답, 자리표시자를 잃은 응답은 번역으로 치지 않는다(캐시에도 안 넣는다)
                    if (isinstance(translated, str) and translated.strip() and translated != masked
                            and _slots_ok(translated, slots)):
                        with self._lock:
                            self._cache[self._key(masked, lang)] = translated
                            self._dirty = True
                        out[idx] = _restore(translated, slots, lang, free_tr)
                        done.add(idx)
            self.save()
        if scope is not None:
            scope.untranslated.extend(work[i][0] for i in misses if i not in done)
        return out

    def _translate_uncached(self, values: List[str], lang: str) -> Dict[str, str]:
        """사용자가 넣은 값만 따로 묶어 번역한다. 일반 문장과 한 요청에 섞지 않고, 캐시에 쓰지 않는다."""
        out: Dict[str, str] = {}
        if not values or not self._llm_ready():
            return out
        for chunk in self._chunks(list(range(len(values))), values):
            got = self._call_exact([values[i] for i in chunk], lang)
            for idx, translated in zip(chunk, got or []):
                src = values[idx]
                if not isinstance(translated, str) or not translated.strip() or translated == src:
                    continue
                translated = translated.replace("⟦", "[").replace("⟧", "]").strip()
                if len(translated) <= 3 * len(src) + 60:     # 값 하나의 번역이 문단으로 불어나면 버린다
                    out[src] = translated
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
    def _chunks(indices: List[int], texts):
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
            # 백엔드(OpenAI/연구실 Ollama)와 모델 계열에 맞는 파라미터로 부른다.
            # 예전에는 temperature·max_tokens 를 그대로 넘겨서, chat 모델이 gpt-5 계열로 바뀐 뒤
            # 캐시에 없는 문장의 번역 호출이 오류로 끝나고 한국어가 그대로 남았다.
            from services import llm_backend
            backend = llm_backend.active()
            if backend != "ollama" and self.client is not None:
                llm_backend._clients.setdefault("openai", self.client)
            resp = llm_backend.complete_chat(
                [{"role": "system", "content": _SYSTEM},
                 {"role": "user",
                  "content": f"Target language: {LANG_NAME[lang]}\n"
                             f"Translate these {len(src)} strings. Return exactly {len(src)} "
                             f"translations in the same order.\n"
                             f"{json.dumps(src, ensure_ascii=False)}"}],
                max_tokens=4000, temperature=0, json_mode=True, reasoning_effort="low")
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
        """중첩 dict/list 에서 지정한 키의 문자열 값만 번역한다(구조·식별자는 건드리지 않음).

        **입력을 고치지 않고 번역된 사본을 돌려준다.** 예전에는 제자리에서 고쳤는데, 응답 dict 가
        프로세스 공용 객체(지식베이스 항목의 `avoidance_control_ko` 리스트, 문진 선택지 상수 YNU 등)를
        그대로 참조하고 있어서 영어 요청 한 번이 그 원본을 영어로 바꿔 버렸다. 그 뒤로는 한국어
        요청에도 영어가 나오고, 세 언어를 차례로 만드는 백그라운드 작업도 서로 오염됐다."""
        if lang not in ("en", "zh"):
            return obj
        obj = copy.deepcopy(obj)
        keys = set(keys)
        targets: List[Any] = []          # (container, key) 쌍 — key 는 dict 키 또는 리스트 인덱스
        texts: List[str] = []

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k not in keys:
                        walk(v)
                    elif self._needs_translation(v):
                        targets.append((node, k))
                        texts.append(v)
                    elif isinstance(v, list):
                        # 회피 수칙·교차반응 음식처럼 값이 문자열 리스트인 필드도 번역 대상이다
                        for i, item in enumerate(v):
                            if self._needs_translation(item):
                                targets.append((v, i))
                                texts.append(item)
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
        # ⟦ ⟧ 는 아래에서 자리표시자로 쓴다. 입력에 섞여 온 것(사용자 값)은 미리 평범한 괄호로 바꾼다.
        html = html.replace("⟦", "[").replace("⟧", "]")

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
                if tr == src:
                    continue
                if sorted(self._PH.findall(src)) != sorted(self._PH.findall(tr)):
                    logger.warning("번역문에서 줄바꿈 자리표시자가 유실되어 원문을 유지합니다.")
                    self._note_untranslated(src)
                    continue
                if tr.count("**") % 2:      # 굵게 표기가 깨졌으면 강조만 버린다
                    tr = tr.replace("**", "")
                # 번역문은 태그 사이에 그대로 끼워 넣는다 — 번역기가 만든 <, > 가 마크업이 되지 않게 한다
                table[src] = _escape_text_node(tr)

        out = self._TEXT_NODE.sub(lambda m: ">" + table.get(m.group(1), m.group(1)) + "<", masked)
        out = self._MD_BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", out)
        out = out.replace("**", "")         # 짝이 남지 않은 표기 정리
        out = self._PH.sub(lambda m: void[int(m.group(1))] if int(m.group(1)) < len(void) else "", out)
        return re.sub(r"<!--K(\d+)-->",
                      lambda m: blocks[int(m.group(1))] if int(m.group(1)) < len(blocks) else "", out)

    @staticmethod
    def _note_untranslated(src: str) -> None:
        scope = _scope.get()
        if scope is not None:
            scope.untranslated.append(scope.mask(src)[0])

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
            src = blocks[i]
            if t == src:
                continue
            # 마크다운은 원시 HTML 과 링크를 그대로 통과시킨다. 번역문이 원문에 없던 태그·스크립트 링크를
            # 들고 오면 태그는 글자로 바꾸고, 스크립트 링크가 생긴 블록은 원문을 유지한다.
            if _adds_unsafe_link(src, t):
                logger.warning("번역문에 스크립트 링크가 생겨 원문을 유지합니다.")
                self._note_untranslated(src)
                continue
            t = _neutralize_new_markup(src, t)
            # attr_list(`{: onclick=…}`, 코드 펜스의 `{ .x onclick=… }`)는 태그 없이도 속성을 만든다.
            # 리포트 원문은 그 문법을 쓰지 않으므로, 번역문의 중괄호는 글자로만 보이게 한다.
            blocks[i] = t.translate(_ATTR_BRACE)
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
