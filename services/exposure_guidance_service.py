"""
Exposure Guidance Service — 노출 → 증상 → 질환 연결과 동물 항원 관리 안내

증상은 알러젠에 노출될 때 생긴다. 리포트와 카드뉴스가 같은 문장을 쓰도록, 환자가 실제로
입력한 값(문진의 질환·주증상 부위·반려동물, 알러젠별 문진에서 확인된 증상)만으로 결정론적으로 만든다.
환자가 말하지 않은 증상은 지어내지 않는다.

  - exposure_links()   : 실제 주의 알러젠마다 '어디서 노출되나 → 환자가 알려준 어떤 질환·증상으로 이어지나'
  - animal_guidance()  : 동물 항원의 구체적 관리 안내(함께 사는 경우 / 키우지 않는 경우 / 일하면서 다루는 경우)
                         문장과 출처는 data/animal_allergen_management.json
  - contact_level() · is_occupational() · animal_exposure_note()
                       : 동물별 접촉 문진의 답(함께 삶 / 자주 / 가끔 / 거의 없음, 일하면서 다루는지)을 읽는 한 곳
  - allocated_tips()   : '우선 실천 회피 수칙' — 실제 주의 알러젠마다 고르게, 겹치는 문장 없이
"""

import json
import logging
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

from config.settings import BASE_DIR
from services.knowledge_service import normalize_category
from utils.text_utils import josa

logger = logging.getLogger(__name__)

ANIMAL_PATH = BASE_DIR / "data" / "animal_allergen_management.json"

_INHALANT = ("mite", "animal", "pollen_tree", "pollen_grass", "pollen_weed", "mold", "insect")

# 노출될 때 증상이 확인된 알러젠이 없을 때 '우선 실천 수칙' 자리에 싣는 문장. 예전에는 누구에게나 진드기·꽃가루
# 수칙(침구 고온 세탁·차단 커버·외출 자제)을 적어, 음식·벌독만 양성인 환자에게도 침구 세탁을 시켰고 '감작만'
# 항목의 '과도한 회피는 필요하지 않습니다'와도 어긋났다.
NO_RELEVANT_NOTE_KO = "이번 문진에서 노출될 때 증상이 확인된 알러젠이 없어, 꼭 지켜야 할 회피 수칙은 없습니다."
NO_RELEVANT_TIPS_KO = ("증상이 생기면 날짜와 장소, 그때 한 일을 적어 두세요",
                       "같은 상황에서 증상이 되풀이되면 진료에서 다시 평가받으세요")

# 카테고리별 '어디서 노출되나'. {season} 은 그 알러젠의 시기 라벨.
_EXPOSURE_KO = {
    "mite": "잠자는 동안의 침구(이불·베개·매트리스), 그리고 이불을 털거나 청소할 때",
    "pollen_tree": "{season}맑고 바람 부는 날의 외출, 창문을 열어 둔 환기, 밖에 넌 빨래",
    "pollen_grass": "{season}잔디밭·풀밭·공원에서의 야외 활동과 창문 환기",
    "pollen_weed": "{season}하천변·공터·풀밭 주변 외출과 창문 환기",
    "mold": "욕실·지하실·누수 자리 같은 습한 공간, 장마철, 낙엽이나 퇴비를 다룰 때",
    "insect": "바퀴 흔적이 있는 주방과 오래된 건물의 먼지",
    # 벌독은 들이마시는 항원이 아니다. 쏘일 때만 노출된다.
    "venom": "벌에 쏘일 때(등산·벌초·성묘·캠핑 같은 야외 활동, 벌집 근처)",
    "food": "그 음식을 먹을 때(외식·가공식품에 섞여 들어간 경우 포함)",
    # 라텍스는 닿아서, 약물은 쓸 때 노출된다 — 들이마시는 항원이 아니다.
    "latex": "고무장갑·풍선·콘돔, 병원·치과의 장갑·의료기기처럼 라텍스로 만든 제품에 닿을 때",
    "drug": "그 약(같은 계열의 약 포함)을 먹거나 주사로 맞을 때",
}
_ANIMAL_EXPOSURE_KO = {
    "owner": "집 안에서 함께 지내는 내내, 특히 안거나 같은 방에서 잘 때",
    "non_owner": "{species} 있는 집을 방문할 때, 그리고 키우는 사람의 옷에 묻어 온 학교·직장 공간",
    "unknown": "{species} 접촉, 또는 같은 공간에 있을 때",
}

# 환자가 고른 질환 → (이 노출로 설명될 수 있는 카테고리, 질환 이름, 그 질환이 대표하는 증상 부위)
# 질환 이름만 쓴다 — 환자가 말하지 않은 세부 증상을 덧붙이지 않는다.
# 만성 두드러기·부비동염·약물 알레르기는 알레르겐 노출 외 원인이 많아 자동으로 잇지 않는다.
_DISEASE_LINK = {
    "allergic_rhinitis": (_INHALANT, "알레르기 비염", "nasal"),
    "asthma": (_INHALANT, "천식", "lower_airway"),
    "allergic_conjunctivitis": (_INHALANT, "알레르기 결막염", "ocular"),
    # 집먼지진드기에 감작된 아토피피부염은 진드기 면역치료로 호전된다는 근거가 있다(KAAACI 2023)
    "atopic_dermatitis": (("mite",), "아토피 피부염", "skin"),
    "food_allergy": (("food",), "음식 알레르기", None),
    "anaphylaxis": (("food", "venom"), "아나필락시스 병력", "systemic"),
}
# 환자가 고른 주증상 부위 → (이 노출로 설명될 수 있는 카테고리, 문구)
_ORGAN_LINK = {
    "nasal": (_INHALANT, "코 증상(재채기·콧물·코막힘)"),
    "ocular": (_INHALANT, "눈 증상(가려움·충혈·눈물)"),
    "lower_airway": (_INHALANT + ("food",), "기침·쌕쌕거림·숨참"),
    "skin": (("animal", "food"), "피부 증상(두드러기·가려움)"),
    "gi": (("food",), "소화기 증상(복통·설사·구토)"),
    "systemic": (("food", "venom"), "전신 증상"),
}



def organ_sites(a) -> set:
    """이 알러젠의 증상이 이어질 수 있는 주증상 부위(ORGAN_SYSTEM_OPTIONS 코드).

    문진이 '어느 부위에 증상이 있었는지'를 따로 물은 항원(음식·라텍스·기타·벌독)은 확인된 부위
    (assessment.symptom_sites)를 그대로 쓴다. 흡입 알러젠은 여러 부위를 한 문장으로 묻기 때문에('코·눈 증상이
    심해지나요?') 항원군의 기본 부위(_ORGAN_LINK)로 보고, 부위를 알 수 없는 경우(symptom_sites=None)도 같다.
    예전에는 항원군 목록만 보아, 라텍스에 닿아 두드러기가 났다고 답한 환자의 피부 증상 카드에
    '이 증상과 연결된 알러젠은 확인되지 않았습니다'가 실렸다."""
    cat = normalize_category(getattr(a, "category", None))
    sites = getattr(a, "symptom_sites", None)
    if sites is not None and cat not in _INHALANT:
        return set(sites)
    return {o for o, (cats, _phrase) in _ORGAN_LINK.items() if cat in cats}


# 문진에서 확인된 단서(reported_symptoms 안의 문구) → 그 단서가 노출과 어떻게 이어지는지
_CUE_EXPLAIN = [
    ("mite", "이른 아침", "밤새 침구에서 노출된 뒤라 아침에 증상이 심해지는 것으로 설명됩니다."),
    ("mite", "먼지를 만지거나", "이불을 털거나 청소하면 가라앉아 있던 진드기 입자가 떠올라 증상이 납니다."),
    ("mite", "침실 잠자리", "잠자리 주변에서 심해지는 것은 침구가 주된 노출원이라는 뜻입니다."),
    ("mite", "침구를 관리한 뒤", "침구 노출을 줄이자 좋아졌다는 점이 가장 직접적인 근거입니다."),
    ("mite", "집을 비우면", "집을 떠나면 노출이 끊겨 좋아지므로 원인이 집 안에 있다는 뜻입니다."),
    ("insect", "집을 비우면", "집을 떠나면 노출이 끊겨 좋아지므로 원인이 집 안에 있다는 뜻입니다."),
]


@lru_cache(maxsize=1)
def _animal_data() -> Dict[str, Any]:
    try:
        return json.loads(ANIMAL_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"동물 항원 관리 안내 로드 실패: {e}")
        return {}


def _is_relevant(a) -> bool:
    rel = getattr(a, "relevance", None)
    return getattr(rel, "value", rel) == "clinically_relevant"


def animal_species(a) -> str:
    """동물 항원의 종: cat / dog / 그 밖의 종 키(hamster 등) / other."""
    en = (getattr(a, "allergen_name", "") or "").lower()
    ko = (getattr(a, "korean_name", "") or "").strip()
    if re.search(r"\bcat\b", en) or "고양이" in ko:
        return "cat"
    if re.search(r"\bdog\b", en) or ko.startswith("개") or "강아지" in ko:
        return "dog"
    for key, words in (_animal_data().get("other_species_keywords") or {}).items():
        if any(re.search(rf"\b{re.escape(w)}\b", en) or w in ko for w in words):
            return key
    return "other"


# 동물별 접촉 문진의 답(assessment.answers["animal_contact"]). 예전 문진은 '키우거나 자주 접촉(yes)' /
# '거의 접촉 없음(no)' 둘뿐이었다 — 저장된 세션의 답을 그대로 읽을 수 있게 남겨 둔다.
CONTACT_LEVELS = ("live", "frequent", "occasional", "rare")
_CONTACT_KO = {"live": "함께 삶", "frequent": "함께 살지는 않지만 자주 접촉(주 1회 이상)",
               "occasional": "가끔 접촉(한 달에 1~3번)", "rare": "거의 접촉 없음",
               "legacy_yes": "키우거나 자주 접촉"}
_CONTACT_FREQ_KO = {"daily": "거의 매일", "several": "주 2~6회", "weekly": "주 1회쯤"}
WORK_TYPES_KO = {"vet": "동물병원·수의 진료", "lab": "실험동물", "farm": "축산·농장·승마장",
                 "pet_shop": "반려동물 판매·분양", "grooming": "미용·호텔·훈련", "care": "보호소·동물원·돌봄",
                 "other": "그 밖에 동물을 다루는 일"}


def _answer(a, key: str) -> str:
    return str((getattr(a, "answers", None) or {}).get(key) or "")


def contact_level(a) -> str:
    """live / frequent / occasional / rare / legacy_yes(예전 문진의 '키우거나 자주 접촉') / ""(답 없음)."""
    v = _answer(a, "animal_contact")
    if v in CONTACT_LEVELS:
        return v
    return {"yes": "legacy_yes", "no": "rare"}.get(v, "")


def is_occupational(a) -> bool:
    """문진에서 이 동물을 일하면서 다룬다고 답했는가."""
    return _answer(a, "animal_occupational") == "yes"


def animal_exposure_note(a) -> str:
    """동물 항원의 노출 상황 한 줄(FHIR note·상담 컨텍스트). 답이 없으면 빈 문자열."""
    parts = []
    level = contact_level(a)
    if level:
        freq = _CONTACT_FREQ_KO.get(_answer(a, "animal_contact_freq"), "") if level == "frequent" else ""
        parts.append(_CONTACT_KO[level] + (f" — {freq}" if freq else ""))
    if is_occupational(a):
        types = [WORK_TYPES_KO[t] for t in _answer(a, "animal_work_types").split(",") if t in WORK_TYPES_KO]
        work = "직업적으로 다룸" + (f"({', '.join(types)})" if types else "")
        ws = {"yes": "일하는 날 증상이 심해지고 쉬는 날 좋아짐", "no": "일하는 날과 쉬는 날의 증상 차이 없음",
              "unsure": "일하는 날과 쉬는 날의 증상 차이는 모름"}.get(_answer(a, "animal_work_symptoms"), "")
        parts.append(work + (f", {ws}" if ws else ""))
    return "; ".join(parts)


def ownership_status(a, screening) -> str:
    """owner(함께 삼) / non_owner(키우지 않음) / unknown(문진에서 알 수 없음).
    동물별 접촉 질문에 답했으면 그 답이 먼저다(함께 삶 → owner, 자주·가끔·거의 없음 → non_owner).
    답이 없거나 예전 문진의 답(yes)이면 반려동물 답(pets)으로 정한다."""
    level = contact_level(a)
    if level == "live":
        return "owner"
    if level in ("frequent", "occasional", "rare") and _answer(a, "animal_contact") != "no":
        return "non_owner"
    pets = list(getattr(screening, "pets", None) or [])
    if not pets:
        return "unknown"
    species = animal_species(a)
    if species in ("cat", "dog"):
        return "owner" if species in pets else "non_owner"
    if "other" not in pets:
        return "non_owner"
    other = (getattr(screening, "pets_other", None) or "").lower()
    words = (_animal_data().get("other_species_keywords") or {}).get(species) or []
    if other and any(re.search(rf"\b{re.escape(w)}\b", other) if w.isascii() else w in other
                     for w in words):
        return "owner"
    return "unknown"


def animal_guidance(a, screening) -> Optional[Dict[str, Any]]:
    """동물 항원 1건의 관리 안내. 동물 항원이 아니거나 데이터가 없으면 None.

    반환: {status, species, species_label, headline, action_sentence, one_line,
           steps:[{short, text}], extra:[text], caveat,
           occupational: None | {headline, action_sentence, steps:[{short, text}]},
           exposure: 그 종에만 쓰는 '어디서 노출되나' 문장(없으면 "")}
    """
    if normalize_category(getattr(a, "category", None)) != "animal":
        return None
    data = _animal_data()
    if not data:
        return None
    species = animal_species(a)
    sp = (data.get("species") or {}).get(species) or (data.get("species") or {}).get("other") or {}
    status = ownership_status(a, screening)
    # 말·소처럼 집에서 함께 사는 동물이 아닌 종은 함께 사는지와 무관하게 그 종의 안내를 쓴다
    contact_block = sp.get("contact_block")
    block = contact_block or data.get(status) or {}
    skip = set(sp.get("skip_steps") or [])        # 케이지에서 기르는 동물에게 '목욕'을 안내하지 않는다
    steps = [{"short": s.get("short_ko", ""),
              "text": s.get("text_ko", "").replace("{washing_note}", sp.get("washing_note_ko", ""))}
             for s in block.get("steps", []) if s.get("key") not in skip]
    if status == "owner" and not contact_block:
        steps += [{"short": s.get("short_ko", ""), "text": s.get("text_ko", "")} for s in sp.get("steps", [])]
    extra: List[str] = [n["text_ko"] for n in sp.get("notes", []) if n.get("text_ko")]
    # 일하면서 다루는 동물 — 직장 노출 안내를 따로 덧붙인다(종의 안내가 이미 직업 노출을 다루면 생략)
    occupational = None
    occ = data.get("occupational") or {}
    if is_occupational(a) and occ and not (contact_block or {}).get("covers_occupational"):
        occupational = {"headline": occ.get("headline_ko", ""),
                        "action_sentence": occ.get("action_sentence_ko", ""),
                        "steps": [{"short": s.get("short_ko", ""), "text": s.get("text_ko", "")}
                                  for s in occ.get("steps", [])]}
    if contact_block:
        return {"status": status, "species": species, "species_label": sp.get("label_ko") or "그 동물",
                "headline": block.get("headline_ko", ""), "action_sentence": block.get("action_sentence_ko", ""),
                "one_line": block.get("one_line_ko", ""), "steps": steps, "extra": extra,
                "caveat": data.get("caveat_ko", ""), "occupational": occupational,
                "exposure": block.get("exposure_ko", "")}
    if status == "owner":
        extra.append(block["separation"]["text_ko"])
        timeline = block["timeline"]["text_ko"]
        if species != "cat":
            timeline += " " + block["timeline"]["species_note_ko"]
        extra.append(timeline)
    if status == "unknown":
        # 함께 사는지 모르면 양쪽에 공통인 것만 말한다: 침실 분리와 간접 노출
        owner_steps = {s["key"]: s for s in (data.get("owner") or {}).get("steps", [])}
        non_steps = {s["key"]: s for s in (data.get("non_owner") or {}).get("steps", [])}
        for s in (owner_steps.get("bedroom"), non_steps.get("clothing")):
            if s:
                steps.append({"short": s["short_ko"], "text": s["text_ko"]})
    return {
        "status": status,
        "species": species,
        "species_label": sp.get("label_ko") or "그 동물",
        "headline": block.get("headline_ko", ""),
        "action_sentence": block.get("action_sentence_ko", ""),
        "one_line": block.get("one_line_ko", ""),
        "steps": steps,
        "extra": extra,
        "caveat": data.get("caveat_ko", ""),
        "occupational": occupational,
        "exposure": "",
    }


def avoidance_tips(a, screening) -> List[str]:
    """회피 수칙 목록(요약용). 동물 항원은 함께 사는지에 맞춘 짧은 문장으로, 나머지는 지식베이스 그대로.
    동물 항원의 긴 설명은 알러젠 블록·관리 카드가 맡으므로 여기서는 되풀이하지 않는다."""
    g = animal_guidance(a, screening)
    if g and g["steps"]:
        work = (g.get("occupational") or {}).get("steps", [])
        # 일하면서 다루는 동물이면 직장 노출 수칙을 앞에 둔다 — 노출이 가장 큰 곳이다
        return [s["short"] for s in work[1:3] + g["steps"] if s["short"]]
    return [t.strip() for t in (getattr(a, "kb", None) or {}).get("avoidance_control_ko", [])
            if t and t.strip()]


def is_duplicate_tip(tip: str, seen: List[str]) -> bool:
    """표현만 다른 같은 수칙인가(예: '침구 주 1회 55~60℃ 세탁' vs '침구는 55~60℃ … 주 1회 세탁').

    낱말을 2글자 조각으로 나눠 견준다. 낱말째 견주면 '진드기 차단(anti-mite) 커버로 매트리스·베개·이불
    감싸기' 와 '진드기 차단 커버 사용' 이 다른 수칙으로 남는다.
    """
    def grams(t):
        words = re.findall(r"[가-힣a-zA-Z0-9]+", (t or "").lower())
        return {w[i:i + 2] for w in words for i in range(max(1, len(w) - 1))}
    def seasons(t):
        return {w for w in ("봄", "여름", "가을", "겨울") if w in (t or "")}
    k = grams(tip)
    if not k:
        return True
    for prev in seen:
        # 계절이 다르면 문장이 닮아도 다른 수칙이다(봄철 외출 자제 / 가을철 외출 자제)
        if seasons(tip) and seasons(prev) and seasons(tip) != seasons(prev):
            continue
        kp = grams(prev)
        if kp and len(k & kp) / min(len(k), len(kp)) >= 0.6:
            return True
    return False


def allocated_tips(items, screening, limit: int) -> List[Dict[str, Any]]:
    """'우선 실천 회피 수칙'을 실제 주의 알러젠마다 고르게 나눈다.

    items: [(표시 이름, assessment)] — 임상 그룹으로 접은 뒤의 대표 항원.
    알러젠을 돌아가며 한 개씩 뽑는다(1순위 수칙이 모두 나온 뒤 2순위). 예전에는 첫 알러젠의 수칙을
    끝까지 담아, 진드기·자작나무·고양이 환자의 열 줄이 전부 진드기 수칙이었고 유럽·미국 진드기의
    같은 수칙이 표현만 달리 두 번씩 나왔다.
    반환: [{"label", "category", "tip"}]
    """
    queues = []
    for label, a in items:
        tips = avoidance_tips(a, screening)
        if tips:
            queues.append((label, normalize_category(getattr(a, "category", None)), list(tips)))
    out: List[Dict[str, Any]] = []
    seen: List[str] = []
    while len(out) < limit and any(q[2] for q in queues):
        for label, cat, tips in queues:
            while tips:
                tip = tips.pop(0)
                if not is_duplicate_tip(tip, seen):
                    seen.append(tip)
                    out.append({"label": label, "category": cat, "tip": tip})
                    break
            if len(out) >= limit:
                break
    return out


def _exposure_text(a, screening) -> str:
    cat = normalize_category(getattr(a, "category", None))
    if cat == "animal":
        g = animal_guidance(a, screening)
        status = g["status"] if g else "unknown"
        label = g["species_label"] if g and g["species"] in ("cat", "dog") else (
            getattr(a, "korean_name", None) or getattr(a, "allergen_name", "") or "그 동물")
        text = (g or {}).get("exposure") or _ANIMAL_EXPOSURE_KO[status].replace("{species}", label)
        if g and g.get("occupational"):
            text = (_animal_data().get("occupational") or {}).get("exposure_ko", "") + ", 그리고 " + text
        return text
    tpl = _EXPOSURE_KO.get(cat)
    if not tpl:
        return ""
    season = ""
    if "{season}" in tpl:
        # 리포트·카드의 다른 자리와 같은 시기 한 가지를 쓴다(종별 자료 우선, 없으면 분류군 추정)
        from services.pollen_forecast_service import get_pollen_forecast_service
        season = get_pollen_forecast_service().season_label(a, screening)
    return tpl.replace("{season}", f"{season}, " if season else "")


def exposure_links(items, screening) -> Dict[str, Any]:
    """실제 주의 알러젠별 노출 → 증상 → 질환 연결.

    items: [(표시 이름, assessment)] — 임상 그룹으로 접은 뒤의 대표 항원.
    반환: {"links": [{label, category, exposure, targets:[...], confirmed, notes:[...]}],
           "free_text": [...]}
    targets 는 환자가 처음에 고른 질환·주증상 부위 가운데 이 노출로 설명될 수 있는 것만 담는다.
    confirmed 는 알러젠별 문진에서 확인된 증상 문장 그대로다.
    """
    diseases = [d for d in (getattr(screening, "allergic_diseases", None) or []) if d != "none"]
    organs = list(getattr(screening, "organ_systems", None) or [])
    links = []
    for label, a in items:
        if not _is_relevant(a):
            continue
        cat = normalize_category(getattr(a, "category", None))
        exposure = _exposure_text(a, screening)
        if not exposure:
            continue
        targets: List[str] = []
        covered = set()
        for d in diseases:
            cats, phrase, organ = _DISEASE_LINK.get(d, ((), "", None))
            if cat in cats:
                targets.append(phrase)
                covered.add(organ)
        sites = organ_sites(a)
        for o in organs:
            phrase = _ORGAN_LINK.get(o, ((), ""))[1]
            if phrase and o in sites and o not in covered:
                targets.append(phrase)
        confirmed = "; ".join(s for s in (getattr(a, "reported_symptoms", None) or []) if s)
        notes = [text for c, cue, text in _CUE_EXPLAIN if c == cat and cue in confirmed]
        if cat.startswith("pollen") and getattr(a, "season_overlap", None) is True:
            notes.append("알려주신 악화 시기가 이 꽃가루 시즌과 겹칩니다.")
        oas = list(getattr(a, "oas_foods", None) or [])
        if oas:
            notes.append(f"날로 먹을 때의 입·목 가려움({', '.join(oas)})도 같은 감작에서 이어집니다.")
        links.append({"label": label, "category": cat, "exposure": exposure,
                      "targets": targets, "confirmed": confirmed, "notes": notes})

    free_text: List[str] = []
    if links:
        trig = (getattr(screening, "triggers_free_text", None) or "").strip()
        if trig:
            free_text.append(f"스스로 느끼는 유발 요인으로 적어주신 내용: “{trig}”. "
                             "위 노출 상황과 맞는지 진료에서 함께 확인하세요.")
        other = (getattr(screening, "disease_other", None) or "").strip()
        if other:
            free_text.append(f"기타 질환으로 적어주신 “{other}”{josa(other, '은는')} 자동으로 잇지 않았습니다. "
                             "진료에서 알려 주세요.")
    return {"links": links, "free_text": free_text}
