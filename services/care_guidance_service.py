"""
Care Guidance Service — 쓰는 약 · 주증상 · 감작만 된 항원의 예방과 관찰

리포트와 카드뉴스(퀘스트·클래식)가 같은 문장을 쓰도록, 환자가 실제로 입력한 값만으로 결정론적으로 만든다.

  - medication_guidance()   : 문진에서 고른 약마다 — 알려 준 질환에서 하는 일, 이번 검사에서 증상과 연결된
                              알러젠 노출과의 관계, 꾸준히 써야 하는 이유. 고르지 않은 약은 말하지 않는다.
                              약이 조절하는 증상은 환자가 알려 준 질환과 주증상 부위로만 적는다.
  - symptom_guidance()      : 문진에서 고른 주증상 부위마다 — 노출과의 관계, 생활 관리, 노출 줄이기, 약물 선택지.
                              고르지 않은 증상은 말하지 않는다. symptom_groups() 가 이어지는 알러젠이 같은
                              부위를 한 묶음으로 모은다.
  - sensitized_prevention() : 감작만 항원의 예방과 관찰, 관찰 필요(판정 보류) 항원의 지켜볼 증상.
                              실제 주의 항원과 음성 항원에는 나오지 않는다. 문장은 그 환자에게 있는 항원군
                              (흡입 / 동물 / 음식 / 벌독)에 맞는 것만 고른다. 동물 항원은 함께 사는지와
                              접촉 문진의 답에 따라 따로 안내한다.

문장과 출처는 data/treatment_guidance.json, data/sensitization_prevention.json 에 있다(전문의 검토 전).
약을 시작·중단·증감하라는 문장은 만들지 않는다 — 언제나 '진료에서 상의'로 맺는다.

이 모듈의 *_guidance()/sensitized_prevention() 은 이스케이프하지 않은 값을 돌려준다. 항원 이름(OCR)과
medication_note(환자가 직접 쓴 글)가 섞여 있으므로, 아래 *_md()/*_cards() 가 모든 문자열을 md_text / html.escape
로 감싼다. 다른 곳에서 쓸 때도 반드시 이스케이프해야 한다.
"""

import html
import json
import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from config.settings import BASE_DIR
from services.exposure_guidance_service import (
    _INHALANT, _animal_data, allocated_tips, animal_guidance, animal_species,
    contact_level, is_duplicate_tip, is_occupational, organ_sites, ownership_status)
from services.knowledge_service import normalize_category
from services.screening_service import DISEASE_LABELS_KO
from utils.text_utils import _last_sound

logger = logging.getLogger(__name__)

TREATMENT_PATH = BASE_DIR / "data" / "treatment_guidance.json"
PREVENTION_PATH = BASE_DIR / "data" / "sensitization_prevention.json"

# 문진 선택지의 순서(조절 약 → 증상 약 → 기타). 선택지에 없는 코드는 다루지 않는다.
MEDICATION_ORDER = ("inhaled_steroid", "nasal_steroid", "antihistamine", "leukotriene", "decongestant",
                    "immunotherapy", "biologics", "systemic_steroid")
ORGAN_ORDER = ("lower_airway", "nasal", "ocular", "skin", "gi", "systemic")
_GROUP_ORDER = ("mite", "pollen", "mold", "insect", "food", "venom", "latex", "drug", "other")
_SPECIES_KO = {"cat": "고양이", "dog": "개"}
_SPECIES_EMOJI = {"cat": "🐈", "dog": "🐕"}
# 리포트·카드뉴스가 '우선 실천 수칙'에 실제로 싣는 줄 수와 그 절의 이름. 이미 나온 수칙을 가리킬 때는
# 그 문서에 정말 실린 수칙만 가리켜야 한다(리포트 10줄, 카드 6줄).
REPORT_TIPS = (10, "우선 실천 회피 수칙")
CARD_TIPS = (6, "우선 실천할 생활 수칙")
_MAX_ORGANS_PER_CARD = 3


@lru_cache(maxsize=2)
def _load(path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"안내 자료 로드 실패({path.name}): {e}")
        return {}


def _relevance(a) -> str:
    rel = getattr(a, "relevance", None)
    return getattr(rel, "value", rel) or ""


def _cat(a) -> str:
    return normalize_category(getattr(a, "category", None))


def _label(a) -> str:
    """임상 그룹(집먼지진드기 두 종 등)은 통합 이름으로."""
    try:
        from services.clinical_group_service import get_clinical_group_service
        return get_clinical_group_service().label_of(a)
    except Exception:  # noqa: BLE001
        return getattr(a, "korean_name", None) or getattr(a, "allergen_name", "") or ""


def _collapse(items) -> list:
    try:
        from services.clinical_group_service import get_clinical_group_service
        return [g["members"][0][1] for g in get_clinical_group_service().collapse(items)]
    except Exception:  # noqa: BLE001
        return list(items)


def _relevant(assessments) -> list:
    return _collapse([a for a in assessments or [] if _relevance(a) == "clinically_relevant"])


def _shown(stmt: Optional[Dict[str, Any]], key: str = "text_ko") -> str:
    """환자 화면에 낼 문장. needs_review 문장은 내보내지 않는다."""
    if not stmt or stmt.get("needs_review"):
        return ""
    return (stmt.get(key) or "").strip()


def _josa(word: str, with_final: str, without_final: str) -> str:
    """받침 유무에 맞는 조사를 붙인다(고양이와 / 그 동물과). 받침 판정은 utils.text_utils 한 곳에서 한다."""
    return (word or "") + (with_final if _last_sound(word) else without_final)


def _names(labels: List[str], limit: int = 4) -> str:
    return ", ".join(labels[:limit]) + (" 등" if len(labels) > limit else "")


def _join(*parts: Any) -> str:
    """문장 조각을 한 칸씩 띄워 잇는다. 빈 조각은 버린다 — 마침표가 겹치거나('..') 홀로 뜨지(' .') 않게,
    이 모듈의 문장은 모두 이 함수와 _stop() 으로만 잇는다."""
    return " ".join(str(p).strip() for p in parts if p and str(p).strip())


def _stop(text: str) -> str:
    """문장 끝에 마침표가 없을 때만 붙인다."""
    text = (text or "").strip()
    if not text or text[-1] in ".!?…":
        return text
    if text[-1] in ")”’" and text[-2:-1] in (".", "!", "?"):
        return text
    return text + "."


def _seen_priority_tips(relevant, screening, limit: int) -> List[str]:
    """'우선 실천 수칙'에 그 문서가 실제로 실은 문장 — 같은 수칙을 되풀이하지 않기 위한 기준."""
    return [t["tip"] for t in allocated_tips([(_label(a), a) for a in relevant], screening, limit=limit)]


# ---------------------------------------------------------------------------
# 1) 쓰는 약
# ---------------------------------------------------------------------------
def _exposure_line(T: Dict[str, Any], pairs: List[Tuple[str, str]], organs: set, inhalants: List[str],
                   subject: str, brief: bool = False) -> str:
    """약이 조절하는 증상을 환자의 '실제 주의' 흡입 알러젠과 잇는 문장.

    pairs: [(질환 코드, 그 질환에서 이 약이 조절하는 증상)]. 환자가 그 질환의 부위를 주증상으로 골랐으면
    '노출될 때 생기는 …'로, 고르지 않았으면 그 질환에서 일반적으로 생기는 증상으로 적는다 — 환자가 말하지
    않은 증상을 환자의 증상처럼 쓰지 않는다. 연결된 알러젠이 없으면 없다고 적는다(지어내지 않는다).
    brief: 같은 문서에서 앞의 약에 이미 알러젠 이름을 적었을 때 — 이름과 맺음 문장을 되풀이하지 않는다.
    """
    table = T["exposure_diseases"]
    pairs = [(d, sym) for d, sym in pairs if d in table and sym]
    if not pairs:
        return ""
    if not inhalants:
        return T["no_allergen_brief_ko" if brief else "no_allergen_ko"]
    names = _names(inhalants)
    tpl_reported = T["exposure_reported_brief_tpl_ko" if brief else "exposure_reported_tpl_ko"]
    tpl_general = T["exposure_general_brief_tpl_ko" if brief else "exposure_general_tpl_ko"]
    reported = list(dict.fromkeys(sym for d, sym in pairs if table[d]["organ"] in organs))
    out = []
    if reported:
        out.append(tpl_reported.replace("{allergens}", names).replace("{subject}", subject)
                   .replace("{symptoms_obj}", _josa(", ".join(reported), "을", "를")))
    for d, sym in pairs:
        if table[d]["organ"] not in organs:
            out.append(tpl_general.replace("{allergens}", names).replace("{subject}", subject)
                       .replace("{disease}", DISEASE_LABELS_KO[d]).replace("{symptoms_subj}", _josa(sym, "이", "가")))
    return _join(*out, "" if brief else T["exposure_tail_ko"])


def medication_guidance(assessments, screening) -> Optional[Dict[str, Any]]:
    """문진에서 고른 약의 안내. 약·질환·메모가 모두 없으면 None.

    반환: {title, intro, items:[{code, label, emoji, type, diseases:[이름], lines:[{head, text, card}], short}],
           card_groups:[{emoji, title, items:[...], exposure}], notes:[문장], season_note,
           note(환자가 쓴 글, 미이스케이프), note_tpl, closing, sources:[서지]}
    """
    data = _load(TREATMENT_PATH)
    if screening is None or not data:
        return None
    T, catalog = data["medication_text"], data["medications"]
    reported = set(getattr(screening, "current_medications", None) or [])
    meds = [m for m in MEDICATION_ORDER if m in reported and m in catalog]
    diseases = [d for d in (getattr(screening, "allergic_diseases", None) or [])
                if d != "none" and d in DISEASE_LABELS_KO]
    note = (getattr(screening, "medication_note", None) or "").strip()
    if not meds and not diseases and not note:
        return None

    organs = set(getattr(screening, "organ_systems", None) or [])
    relevant = _relevant(assessments)
    inhalants = [_label(a) for a in relevant if _cat(a) in _INHALANT]
    has_pollen = any(_cat(a).startswith("pollen") for a in relevant)
    exposure_diseases = T["exposure_diseases"]
    used_sources: List[str] = []

    def use(stmt):
        for key in (stmt or {}).get("sources", []):
            if key not in used_sources:
                used_sources.append(key)

    def applies(stmt) -> bool:
        """질환이 붙은 문장은 환자가 그 질환을 알려 준 경우에만 낸다."""
        return bool(stmt) and (not stmt.get("disease") or stmt["disease"] in diseases)

    items = []
    season_meds: List[str] = []     # 꽃가루 시기에 미리 쓰는 방법이 해당하는 약 — 문장은 리포트에 한 번만 싣는다
    for code in meds:
        m = catalog[code]
        lines: List[Dict[str, Any]] = []
        matched = [d for d in diseases if d in m.get("diseases", {})]
        if _shown(m.get("general")):
            lines.append({"head": "하는 일", "text": _shown(m["general"]), "card": True})
            use(m["general"])
        for d in matched:
            if _shown(m["diseases"][d]):
                lines.append({"head": DISEASE_LABELS_KO[d], "text": _shown(m["diseases"][d]), "card": True})
                use(m["diseases"][d])
        unmatched = bool(m.get("diseases")) and not matched
        if unmatched:
            lines.append({"head": "쓰는 이유", "text": T["unmatched_ko"], "card": True,
                          "kind": "unmatched", "brief": T["unmatched_brief_ko"]})
        pairs = [(d, m["diseases"][d].get("symptom_ko", "")) for d in matched]
        exposure = _exposure_line(T, pairs, organs, inhalants, "이 약이")
        if exposure:
            lines.append({"head": "내 알러젠과의 관계", "text": exposure, "card": True, "kind": "exposure",
                          "brief": _exposure_line(T, pairs, organs, inhalants, "이 약이", brief=True)})
        consistency = m.get("consistency") if applies(m.get("consistency")) else m.get("consistency_unmatched")
        if _shown(consistency):
            lines.append({"head": m.get("consistency_head_ko") or "꾸준히 써야 하는 이유",
                          "text": _shown(consistency), "card": True})
            use(consistency)
        first_extra = True
        for extra in m.get("extra", []):
            if _shown(extra) and applies(extra):
                # 카드에는 첫 '알아둘 점'만. 연구 한 건을 인용한 문장은 리포트에만 싣는다
                lines.append({"head": "알아둘 점", "text": _shown(extra),
                              "card": first_extra and extra.get("basis") != "study"})
                first_extra = False
                use(extra)
        if (has_pollen and code in T["pollen_prophylaxis_meds"]
                and set(matched) & {"allergic_rhinitis", "allergic_conjunctivitis"}):
            season_meds.append(m["label_ko"])
            use({"sources": T["pollen_prophylaxis_sources"]})
        items.append({"code": code, "label": m["label_ko"], "emoji": m.get("emoji", "💊"),
                      "type": m.get("type_ko", ""), "diseases": [DISEASE_LABELS_KO[d] for d in matched],
                      "matched": [d for d in matched if d in exposure_diseases], "unmatched": unmatched,
                      "short": _shown(consistency, "card_ko") or _shown(consistency), "lines": lines})

    # 카드뉴스용 묶음 — 같은 묶음의 약이 둘 이상이면 한 장에 모은다
    by_code = {it["code"]: it for it in items}
    card_groups = []
    for g in T["card_groups"]:
        members = [by_code[c] for c in g["meds"] if c in by_code]
        if not members:
            continue
        group_diseases = list(dict.fromkeys(d for it in members for d in it["matched"]))
        card_groups.append({
            "emoji": g["emoji"], "title": g["title_ko"], "items": members,
            "unmatched": (f"{', '.join(it['label'] for it in members if it['unmatched'])}: {T['unmatched_ko']}"
                          if any(it["unmatched"] for it in members) else ""),
            "exposure": _exposure_line(T, [(d, exposure_diseases[d]["symptom_ko"]) for d in group_diseases],
                                       organs, inhalants, "이 약들이")})

    notes: List[str] = []
    treatable = {d for m in catalog.values() for d in m.get("diseases", {})}
    if not meds:
        if diseases:
            notes.append(T["no_med_tpl_ko"].replace(
                "{diseases}", ", ".join(DISEASE_LABELS_KO[d] for d in diseases)))
    elif not any("general" in catalog[c] for c in meds):
        # 약은 골랐지만 어떤 질환에는 해당하는 약이 없는 경우. 전신 스테로이드·면역치료·생물학적 제제는
        # 여러 질환에 걸쳐 쓰이므로 그중 하나라도 있으면 '해당 약 없음'이라고 단정하지 않는다.
        covered = {d for c in meds for d in catalog[c].get("diseases", {})}
        for d in diseases:
            if d in treatable and d not in covered:
                notes.append(f"{DISEASE_LABELS_KO[d]}: 고르신 약 가운데 이 질환에 흔히 쓰는 약은 없었습니다. "
                             "따로 쓰는 약이 있는지, 치료가 필요한지 진료에서 확인하세요.")
    # 제목은 고른 약에 맞춘다 — 약이 없는데 '왜 꾸준히 써야 할까', 짧게 쓰는 약뿐인데 '꾸준히'라고 쓰지 않는다
    if not meds:
        title, intro = T["title_no_med_ko"], T["intro_no_med_ko"]
    elif all(catalog[c].get("short_course") for c in meds):
        title, intro = T["title_short_course_ko"], T["intro_ko"]
    else:
        title, intro = T["title_ko"], T["intro_ko"]
    return {"title": title, "intro": intro, "items": items, "card_groups": card_groups, "notes": notes,
            "short_course_only": title == T["title_short_course_ko"],
            "season_note": (f"꽃가루 시기({', '.join(season_meds)}): {T['pollen_prophylaxis_ko']}"
                            if season_meds else ""),
            "note": note, "note_tpl": T["note_tpl_ko"], "closing": T["closing_ko"],
            "sources": [(data["sources"][k].get("short") or data["sources"][k]["citation"])
                        + " doi:" + data["sources"][k]["doi"] for k in used_sources if k in data["sources"]]}


def asthma_card_line(screening) -> str:
    """'치료와 연결하기' 카드의 천식 줄. 흡입 스테로이드 카드가 뒤에 있으면 그 카드에 맡기고 빈 문자열을 돌려준다
    (같은 덱에서 '증상이 없어도 매일'과 '매일 또는 증상 시'가 엇갈리지 않게, 조절제 문장의 출처는 한 곳)."""
    T = _load(TREATMENT_PATH).get("medication_text", {})
    has_card = "inhaled_steroid" in (getattr(screening, "current_medications", None) or [])
    return "" if has_card else (T.get("asthma_controller_card_ko") or "")


# ---------------------------------------------------------------------------
# 2) 주증상
# ---------------------------------------------------------------------------
def _one_line(a, screening) -> str:
    """그 알러젠의 노출 줄이기 핵심 한 줄(리포트 '한눈에 보기'와 같은 문장)."""
    g = animal_guidance(a, screening)
    if g and g.get("one_line"):
        return g["one_line"]
    from services.report_service import ReportService
    return ReportService._ONE_LINE_ACTION.get(_cat(a), "")


def symptom_guidance(assessments, screening) -> List[Dict[str, Any]]:
    """문진에서 고른 주증상 부위마다 한 묶음. 고르지 않은 부위는 만들지 않는다.

    '주증상' = organ_systems 에서 환자가 고른 항목 전부(문진에 부위별 순위·중증도가 없다).
    반환: [{organ, label, short, emoji, how, how_card, linked:[이름], link_text, selfcare:[문장],
            avoid:[{label, tip}], drugs:[{name, text, taking}], notes:[문장], no_asthma, urgent}]
    drugs 는 약물 선택지(이름과 설명)이고, notes 는 선택지가 아닌 주의 문장이다 — 섞지 않는다.
    avoid 는 이어진 '실제 주의' 알러젠마다 노출 줄이기의 핵심 한 줄이다('우선 실천 수칙' 문장의 되풀이가 아니다).
    """
    data = _load(TREATMENT_PATH)
    if screening is None or not data:
        return []
    reported = set(getattr(screening, "organ_systems", None) or [])
    organs = [o for o in ORGAN_ORDER if o in reported and o in data["symptoms"]]
    if not organs:
        return []
    T = data["symptom_text"]
    meds = set(getattr(screening, "current_medications", None) or [])
    diseases = set(getattr(screening, "allergic_diseases", None) or [])
    relevant = _relevant(assessments)
    drugs_reacted = [a for a in assessments or [] if _relevance(a) == "clinician_review"
                     and (getattr(a, "answers", None) or {}).get("drug_history") == "reaction"]

    out = []
    for o in organs:
        s = data["symptoms"][o]
        # 이어지는 알러젠: 문진에서 확인된 증상 부위가 이 부위인 '실제 주의' 항원(항원군 목록이 아니다)
        linked = [a for a in relevant if o in organ_sites(a)]
        # 약물은 '실제 주의'가 아니다 — 쓴 뒤 이 부위에 반응이 있었다고 답한 약만 따로 적는다
        drug_names = [_label(a) for a in drugs_reacted if o in organ_sites(a)]
        drug_text = (T["drug_reported_tpl_ko"].replace("{drugs}", _names(drug_names)) if drug_names else "")
        if linked:
            link_text = _join(T["linked_tpl_ko"].replace("{allergens}", _names([_label(a) for a in linked])),
                              drug_text)
        elif drug_text:
            link_text = _join(drug_text, T["unlinked_besides_drug_ko"])
        else:
            link_text = T["unlinked_ko"]
        avoid = [{"label": _label(a), "tip": tip} for a in linked for tip in [_one_line(a, screening)] if tip]
        drugs = [{"name": d["name_ko"], "text": _shown(d), "taking": bool(d.get("med") and d["med"] in meds)}
                 for d in s.get("drugs", []) if _shown(d) and d.get("name_ko")]
        out.append({
            "organ": o, "label": s["label_ko"], "short": s.get("short_ko") or s["label_ko"],
            "emoji": s.get("emoji", "•"), "how": _shown(s.get("how")),
            "how_card": _shown(s.get("how"), "card_ko") or _shown(s.get("how")),
            "linked": [_label(a) for a in linked], "drug_linked": drug_names,
            "link_text": link_text,
            "selfcare": [t for t in (_shown(x) for x in s.get("selfcare", [])) if t],
            "avoid": avoid, "drugs": drugs,
            "notes": [t for t in (_shown(x) for x in s.get("drug_notes", [])) if t],
            "no_asthma": s.get("no_asthma_ko", "") if (o == "lower_airway" and "asthma" not in diseases) else "",
            "urgent": s.get("urgent_ko", ""),
        })
    return out


def symptom_groups(assessments, screening) -> List[List[Dict[str, Any]]]:
    """이어지는 '실제 주의' 알러젠이 같은 주증상 부위를 한 묶음으로. 알러젠과 노출 줄이기는 묶음마다 한 번만 적는다."""
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for s in symptom_guidance(assessments, screening):
        groups.setdefault((tuple(s["linked"]), tuple(s["drug_linked"])), []).append(s)
    out = []
    for members in groups.values():
        for i in range(0, len(members), _MAX_ORGANS_PER_CARD):
            out.append(members[i:i + _MAX_ORGANS_PER_CARD])
    return sorted(out, key=lambda g: ORGAN_ORDER.index(g[0]["organ"]))


# ---------------------------------------------------------------------------
# 3) 감작만 된 항원 — 예방과 관찰 / 관찰 필요 항원 — 지켜볼 증상
# ---------------------------------------------------------------------------
def _group_key(cat: str) -> str:
    if cat.startswith("pollen"):
        return "pollen"
    return cat if cat in ("mite", "mold", "insect", "food", "venom", "latex", "drug") else "other"


def _fresh(tips: List[str], seen: List[str], given: Optional[List[str]] = None) -> Tuple[List[str], str]:
    """이미 실린 것과 겹치지 않는 문장만. seen = '우선 실천 수칙'에 실린 문장, given = 이 절의 앞 항목에 적은 문장.
    반환: (남은 문장, 겹쳐서 뺀 것이 어디에 있었는지 — "" / "tips" / "above")"""
    given = given if given is not None else []
    kept, where = [], ""
    for tip in tips:
        if is_duplicate_tip(tip, seen):
            where = where or "tips"
        elif is_duplicate_tip(tip, given):
            where = where or "above"
        else:
            given.append(tip)
            kept.append(tip)
    return kept, where


def _animal_status(a, screening) -> Tuple[str, str]:
    """(안내 종류, 함께 사는지). 동물별 접촉 질문의 답·직업 노출·문진의 반려동물 답을 함께 본다.

    감작만: occupational(일하면서 다룸 — 다른 답보다 우선) / owner(함께 삶) / frequent(함께 살지는 않지만
            자주) / occasional(가끔) / non_owner(접촉 답이 없고 키우지 않음) / unknown
    관찰 필요: occupational_watch(일하면서 다룸) / no_contact(거의 접촉 없음) /
               unsure(접촉은 있으나 증상 관계를 모름) / no_info
    예전 문진의 답(yes=키우거나 자주 접촉, no=거의 접촉 없음)도 읽는다: yes 는 반려동물 답에 따라
    owner 또는 frequent 가 된다.
    """
    own = ownership_status(a, screening)
    level = contact_level(a)
    if _relevance(a) == "indeterminate":
        if is_occupational(a):
            return "occupational_watch", own
        return ("no_contact" if level == "rare" else "unsure" if level else "no_info"), own
    if is_occupational(a):
        return "occupational", own
    if own == "owner":
        return "owner", own
    if level in ("frequent", "legacy_yes"):
        return "frequent", own
    return ("occasional" if level == "occasional" else own), own


def _animal_block(a, screening, P: Dict[str, Any], seen: List[str], given: List[str],
                  tips_name: str) -> Dict[str, Any]:
    A = P["animal"]
    status, own = _animal_status(a, screening)
    block = A[status]
    code = animal_species(a)
    steps = _animal_data()
    species = _SPECIES_KO.get(code) or ((steps.get("species") or {}).get(code) or {}).get("label_ko") or "그 동물"
    name = _label(a)
    answers = getattr(a, "answers", None) or {}

    def fill(text: str) -> str:
        return (text.replace("{species_with}", _josa(species, "과", "와"))
                .replace("{species_obj}", _josa(species, "을", "를"))
                .replace("{species_dat}", species + "에게도")
                .replace("{species}", species).replace("{name}", name).replace("{tips}", tips_name))

    def shorts(keys) -> List[str]:
        by_key = {(grp, s["key"]): s for grp in ("owner", "non_owner", "occupational")
                  for s in (steps.get(grp) or {}).get("steps", [])}
        return [by_key[tuple(k)]["short_ko"] for k in keys if tuple(k) in by_key]

    low, dup_low = _fresh(shorts(block.get("low_keys", [])), seen, given)
    optional, dup_opt = _fresh(shorts(block.get("optional_keys", [])), seen, given)
    same = ""
    if (dup_low or dup_opt) and not (low or optional):   # 남은 실천 항목이 없을 때만 — 어디에 적었는지 가리킨다
        same = fill(A["same_tpl_ko"] if "tips" in (dup_low, dup_opt) else A["same_other_tpl_ko"])
    status_text = block["status_ko"]
    if status == "frequent" and contact_level(a) == "legacy_yes" and own == "unknown":
        status_text = block["status_unknown_ko"]      # 예전 문진: 키우는지 자주 만나는지 가르지 못한다
    elif status == "occupational_watch" and answers.get("animal_work_symptoms") == "yes":
        status_text = block["status_work_ko"]
    work = bool(block.get("work"))                    # 일하면서 다루는 동물 — 직장에서의 증상을 함께 지켜본다
    why = [fill(t) for t in (block.get("why_ko"), block.get("calm_ko")) if t]
    lines = [fill(status_text)] + why + ([fill(block["do_ko"])] if block.get("do_ko") else [])
    return {"label": name, "status": status, "species": species, "emoji": _SPECIES_EMOJI.get(code, "🐇"),
            "headline": fill(block["headline_ko"]), "lines": lines, "why": why, "low": low, "optional": optional,
            "low_label": A["low_label_ko"], "optional_label": A["optional_label_ko"],
            "same": same, "same_reason": "",
            "keep": fill(block.get("keep_ko", "")),
            "watch": list(A["watch"]) + ([A["watch_work_ko"]] if work else []),
            "watch_line": P["groups"]["animal"]["watch_ko"],
            "retest": fill(A["retest_work_ko"] if work else A["retest_ko"]), "work": work,
            "card": fill(block["card_ko"]), "indeterminate": _relevance(a) == "indeterminate",
            "evidence": bool(block.get("why_ko"))}


def sensitized_prevention(assessments, screening, tips: Tuple[int, str] = REPORT_TIPS) -> Optional[Dict[str, Any]]:
    """감작만(sensitized_only) 항원의 예방과 관찰, 관찰 필요(indeterminate) 항원의 지켜볼 증상.
    해당 항원이 없으면 None.

    실제 주의(clinically_relevant) 항원과 음성 항원은 다루지 않는다. 같은 종의 동물 항원이 이미 실제 주의로
    판정됐으면 그 종은 여기서 빼고 기존 동물 관리 안내에 맡긴다.
    tips: (그 문서의 '우선 실천 수칙'에 실리는 줄 수, 그 절의 이름) — 리포트는 REPORT_TIPS, 카드는 CARD_TIPS.

    반환: {title, lead:[문장], scope, intro:[문장], groups:[...], animals:[...], optional_note, action,
           watch_groups:[...], watch_animals:[...], watch_intro, watch_card, source_line}
      - lead   : '감작만' 항목 전체에 대한 한 가지 입장(리포트 2절 첫머리, 카드의 '감작만' 카드, 상담 답변).
                 [정의, 진단 아님·과도한 회피 불필요, (노출 줄이기 — 흡입 알러젠·접촉하는 동물이 있을 때만),
                 재평가]. 감작만 항목이 없으면 빈 목록.
      - intro  : 흡입 알러젠이 있을 때만 — 감작의 경과에 대한 통계와 노출 줄이기의 근거 수준.
      - groups / animals            : 감작만 항목.
      - watch_groups / watch_animals: 관찰 필요 항목('감작만'이 아니다).
      group = {key, label, emoji, kind, names:[이름], low:[문장], optional:[문장], low_label, optional_label,
               reduce_note, same, keep, watch, action, report_watch}
    """
    data = _load(PREVENTION_PATH)
    if not data:
        return None
    pool = [a for a in assessments or [] if _relevance(a) in ("sensitized_only", "indeterminate")]
    if not pool:
        return None
    P = data["patient_text"]
    I = P["inhalant"]
    limit, tips_name = tips
    relevant = _relevant(assessments)
    relevant_species = {animal_species(a) for a in relevant if _cat(a) == "animal"}
    seen = _seen_priority_tips(relevant, screening, limit)
    given: List[str] = []      # 이 절의 앞 항목에 이미 적은 실천 문장

    out_groups: Dict[str, List[Dict[str, Any]]] = {"sensitized_only": [], "indeterminate": []}
    out_animals: Dict[str, List[Dict[str, Any]]] = {"sensitized_only": [], "indeterminate": []}
    for rel in ("sensitized_only", "indeterminate"):
        buckets: Dict[str, list] = {}
        for a in _collapse([x for x in pool if _relevance(x) == rel]):
            cat = _cat(a)
            if cat == "animal":
                if animal_species(a) in relevant_species:
                    continue      # 같은 종이 이미 '실제 주의' — 기존 동물 관리 안내가 맡는다
                block = _animal_block(a, screening, P, seen, given, tips_name)
                # 같은 종류의 안내가 앞에 있으면 근거 문단을 통째로 되풀이하지 않는다(리포트)
                earlier = next((x for x in out_animals[rel] if x["status"] == block["status"] and x["why"]), None)
                if earlier and block["why"]:
                    block["same_reason"] = P["animal"]["same_reason_tpl_ko"].replace("{other}", earlier["species"])
                out_animals[rel].append(block)
            else:
                buckets.setdefault(_group_key(cat), []).append(a)
        for key in _GROUP_ORDER:
            members = buckets.get(key)
            if not members:
                continue
            G = P["groups"][key]
            inhalant = G["kind"] == "inhalant"
            low, optional, same = [], [], ""
            if inhalant and rel == "sensitized_only":
                low, dup_low = _fresh(G.get("low_burden", []), seen)
                optional, dup_opt = _fresh(G.get("optional", []), seen)
                if (dup_low or dup_opt) and not (low or optional):
                    same = I["same_tpl_ko"].replace("{tips}", tips_name)
            out_groups[rel].append({
                "key": key, "label": G["label_ko"], "emoji": G.get("emoji", "•"), "kind": G["kind"],
                "names": [_label(a) for a in members], "low": low, "optional": optional,
                "low_label": I["low_label_ko"], "optional_label": I["optional_label_ko"],
                "reduce_note": G.get("reduce_note_ko", "") if (low or optional) else "", "same": same,
                "keep": G.get("keep_ko", ""), "watch": G["watch_ko"],
                "action": "" if inhalant else G.get("action_ko", ""),
                "report_watch": G.get("report_watch", True)})

    groups, animals = out_groups["sensitized_only"], out_animals["sensitized_only"]
    watch_groups, watch_animals = out_groups["indeterminate"], out_animals["indeterminate"]
    if not (groups or animals or watch_groups or watch_animals):
        return None
    has_inhalant = any(g["kind"] == "inhalant" for g in groups)
    reducible = has_inhalant or any(an["status"] in ("owner", "frequent", "unknown", "occupational")
                                    for an in animals)
    lead = []
    if groups or animals:
        lead = [P["lead_ko"], P["lead_stance_ko"]] + ([P["lead_reduce_ko"]] if reducible else []) + [P["lead_watch_ko"]]
    sources = ([I["source_ko"]] if has_inhalant else []) + \
              ([P["animal"]["source_ko"]] if any(an["evidence"] for an in animals + watch_animals) else []) + \
              ([P["animal"]["source_work_ko"]] if any(an["work"] for an in animals + watch_animals) else [])
    return {"title": P["title_ko"], "lead": lead, "scope": P["scope_ko"],
            "intro": [I["risk_ko"], I["reassure_ko"], I["asthma_ko"], I["why_ko"]] if has_inhalant else [],
            "groups": groups, "animals": animals,
            "optional_note": I["optional_note_ko"] if any(g["optional"] for g in groups) else "",
            "action": I["action_ko"] if has_inhalant else "",
            "watch_groups": watch_groups, "watch_animals": watch_animals,
            "watch_intro": P["indeterminate"]["intro_ko"], "watch_card": P["indeterminate"]["card_ko"],
            "watch_title": P["indeterminate"]["title_ko"],
            "source_line": _join(("근거: " + ", ".join(sources) + ".") if sources else "", P["review_ko"])}


def _group_title(g: Dict[str, Any], sep: str) -> str:
    """항원군 이름과 그 안의 항목 이름. 둘이 같으면(집먼지진드기 — 집먼지진드기) 한 번만 적는다."""
    names = [n for n in g["names"] if n != g["label"]]
    return g["label"] + (sep + ", ".join(names) if names else "")


def sensitized_lead(assessments, screening) -> List[str]:
    """'감작만' 항목 전체에 대한 한 가지 입장. 감작만 항목이 없어도 정의와 '과도한 회피 불필요'는 돌려준다."""
    out = sensitized_prevention(assessments, screening)
    if out and out["lead"]:
        return out["lead"]
    data = _load(PREVENTION_PATH)
    return [data["patient_text"]["lead_ko"], data["patient_text"]["lead_stance_ko"]] if data else []


# ---------------------------------------------------------------------------
# 4) 약물 항원 — '진료 확인 필요'(이 앱이 판정하지 않는 항목)
# ---------------------------------------------------------------------------
_STRENGTH_KO = {"weak": "약한 양성", "moderate": "중등도 양성", "strong": "강한 양성"}
_SEVERITY_KO = {"mild": "🟢 경증", "moderate": "🟠 중등증", "severe": "🔴 중증", "anaphylaxis": "🚨 아나필락시스"}

# 문진에서 아나필락시스 병력을 알려 준 환자에게 리포트와 두 카드뉴스가 똑같이 싣는 문장(머리말 / 본문).
ANAPHYLAXIS_HISTORY_KO = ("아나필락시스 병력이 있다고 하셨어요:",
                          "응급 대처 계획과 에피네프린 자가주사기 처방 여부를 반드시 확인하세요.")


def has_anaphylaxis_history(screening) -> bool:
    return "anaphylaxis" in (getattr(screening, "allergic_diseases", None) or [])


def drug_review_label() -> str:
    """약물 항원의 판정 이름('진료 확인 필요') — 리포트·카드뉴스·상담·FHIR note·API 가 같은 글자를 쓴다."""
    from models.schemas import CLINICIAN_REVIEW_LABEL_KO
    data = _load(PREVENTION_PATH)
    return ((data.get("patient_text") or {}).get("drug_review") or {}).get("label_ko") or CLINICIAN_REVIEW_LABEL_KO


def drug_review(assessments, screening=None) -> Optional[Dict[str, Any]]:
    """약물 항원('진료 확인 필요')의 안내. 약물 항원이 없으면 None.

    약물은 '실제 주의'도 '감작만'도 '관찰 필요'도 아니다 — 리포트의 표·절, 카드뉴스의 표지·도장, 상담 답변이
    이 한 묶음을 쓴다. 어느 문장에도 회피·재투여 지시가 없다.
    반환: {label, title, lead:[문장], tell, items:[{label, strength, history, history_label, symptoms:[문장],
           severity, severity_label, severe, rationale, class_alert, one_line}], watch, action, card, review}
    """
    data = _load(PREVENTION_PATH)
    drugs = [a for a in assessments or [] if _relevance(a) == "clinician_review"]
    if not drugs or not data:
        return None
    D = data["patient_text"]["drug_review"]
    items = []
    for a in drugs:
        ans = getattr(a, "answers", None) or {}
        history = ans.get("drug_history") or "unknown"
        others = ans.get("drug_class_alert") or ""
        severity = (getattr(a, "severity", None) or "") if history == "reaction" else ""
        class_alert = (D["class_alert_tpl_ko"].replace("{class}", ans.get("drug_class") or "같은 계열")
                       .replace("{others}", others) if others else "")
        items.append({
            "label": _label(a), "strength": _STRENGTH_KO.get(getattr(a, "strength", None) or "", ""),
            "history": history, "history_label": D["history_ko"].get(history, D["history_ko"]["unknown"]),
            "symptoms": list(getattr(a, "reported_symptoms", None) or []) if history == "reaction" else [],
            "severity": severity, "severity_label": _SEVERITY_KO.get(severity, ""),
            "severe": severity in ("severe", "anaphylaxis"),
            "rationale": getattr(a, "rationale_ko", None) or "", "class_alert": class_alert,
            "one_line": D["one_line_ko"]["reaction" if history == "reaction"
                                         else "class_alert" if others else "default"]})
    return {"label": D["label_ko"], "title": D["title_ko"], "lead": [D["lead_ko"], D["why_ko"], D["decide_ko"]],
            "tell": D["tell_ko"], "items": items, "severe": D["severe_ko"], "watch": D["watch_ko"],
            "action": D["action_ko"], "card": D["card_ko"], "question": D["chat_question_ko"],
            "review": data["patient_text"]["review_ko"]}


def _drug_fact(it: Dict[str, Any]) -> str:
    """약 한 가지에 대해 환자가 답한 내용 한 줄(답한 것만 적는다)."""
    extra = " · ".join(x for x in [", ".join(it["symptoms"]), it["severity_label"]] if x)
    return it["history_label"] + (f"({extra})" if extra else "")


def drug_review_md(assessments, screening=None) -> str:
    """리포트의 '진료 확인이 필요한 약물' 절."""
    out = drug_review(assessments, screening)
    if not out:
        return ""
    md = [f"## 💊 🩺 {_md(out['title'])}", _join(*(_md(x) for x in out["lead"]), _md(out["tell"]))]
    for it in out["items"]:
        chips = "".join(f" `{_md(x)}`" for x in (it["strength"], it["severity_label"] and f"답하신 반응 {it['severity_label']}")
                        if x)
        said = f" 답하신 증상: {_md(', '.join(it['symptoms']))}." if it["symptoms"] else ""
        rows = [f"- **{_md(it['label'])}**{chips} — {_md(it['rationale'])}{said}"]
        if it["severe"]:
            rows.append(f"    - 🚨 **{_md(it['label'])}** — {_md(out['severe'])}")
        md.append("\n".join(rows))
    md.append(f"- 👀 지켜볼 증상: {_md(out['watch'])}. {_md(out['action'])}")
    md.append(f"*{_md(out['review'])}*")
    return "\n\n".join(md)


def drug_review_card(assessments, screening=None, stamp: bool = False) -> str:
    """카드뉴스의 약물 카드(퀘스트·클래식 공용). stamp=True 면 퀘스트 덱의 판정 도장 모양으로 이름을 찍는다.
    카드 종류는 'review'(퀴즈·실천 체크·접기 없음) — 덱 셸은 건드리지 않는다."""
    out = drug_review(assessments, screening)
    if not out:
        return ""
    head = (f'<div class="stamp-verdict indet">{_e(out["label"])}</div>' if stamp
            else f'<div class="tag" style="background:#fdf3e3;color:#a36a00">🩺 {_e(out["label"])}</div>')
    chips = "".join(f'<div class="chip">💊 <b>{_e(it["label"])}</b>'
                    f'<span class="chip-season">{_e(_drug_fact(it))}</span></div>' for it in out["items"])
    items = "".join(f'<li>🚨 <b>{_e(it["label"])}</b> — {_e(out["severe"])}</li>' for it in out["items"] if it["severe"])
    items += "".join(f'<li>🔗 <b>{_e(it["label"])}</b> — {_e(it["class_alert"])}</li>'
                     for it in out["items"] if it["class_alert"])
    items += f'<li>👀 <b>지켜볼 증상</b> {_e(_stop(out["watch"]))} {_e(out["action"])}</li>'
    items += f'<li>🧑‍⚕️ {_e(out["tell"])}</li>'
    return f"""
        <div class="section review">
          {head}
          <h2>약물은 진료에서<br/>확인합니다</h2>
          <p class="desc">{_e(out["card"])}</p>
          <div class="chips">{chips}</div>
          <ul class="tips">{items}</ul>
        </div>"""


def observation_intro() -> str:
    """리포트 '관찰이 필요한 알러젠' 절의 첫 문장 — 미룬 이유를 한 가지로 단정하지 않는다."""
    P = (_load(PREVENTION_PATH).get("patient_text") or {}).get("indeterminate") or {}
    return P.get("report_intro_ko") or "판정을 미룬 항목입니다."


def treatment_text(key: str) -> str:
    """data/treatment_guidance.json 의 medication_text 문장 — 카드뉴스·리포트가 같은 문장을 쓰게 한다."""
    return (_load(TREATMENT_PATH).get("medication_text") or {}).get(key) or ""


# ---------------------------------------------------------------------------
# 리포트(Markdown) — 모든 값은 md_text 를 거친다
# ---------------------------------------------------------------------------
def _md(value: Any) -> str:
    from services.report_service import md_text
    return md_text(value)


def _steps_md(block: Dict[str, Any], note: str = "") -> List[str]:
    """부담이 적은 조치와 선택 조치를 서로 다른 줄에. 손이 많이 가는 조치는 '부담이 적은 것'에 섞지 않는다."""
    rows = []
    if block["low"]:
        rows.append(f"- {_md(block['low_label'])}{note}: " + "; ".join(_md(t) for t in block["low"]))
    if block["optional"]:
        rows.append(f"- {_md(block['optional_label'])}: " + "; ".join(_md(t) for t in block["optional"]))
    if block["same"]:
        rows.append(f"- {_md(block['same'])}")
    return rows


def _animal_md(an: Dict[str, Any]) -> str:
    lines = [x for x in an["lines"] if x not in an["why"]] if an["same_reason"] else an["lines"]
    if an["same_reason"]:
        lines = lines[:1] + [an["same_reason"]] + lines[1:]
    rows = [f"#### 🐾 {_md(an['headline'])}", _join(*(_md(x) for x in lines))]
    steps = _steps_md(an)
    if steps:
        rows.append("\n".join(steps))
    if an["keep"]:
        rows.append(_md(an["keep"]))
    rows.append("**지켜볼 증상**\n\n" + "\n".join(f"- {_md(w)}" for w in an["watch"]))
    rows.append(_md(an["retest"]))
    return "\n\n".join(rows)


def prevention_md(assessments, screening) -> str:
    """'감작만' 항목의 예방과 관찰 — 리포트 2절(감작만 된 알러젠) 안에 들어간다."""
    out = sensitized_prevention(assessments, screening, REPORT_TIPS)
    if not out or not (out["groups"] or out["animals"]):
        return ""
    md = [f"### 🌱 {_md(out['title'])}", _join(_md(out["scope"]), *(_md(x) for x in out["intro"]))]
    for g in out["groups"]:
        rows = [f"**{g['emoji']} {_md(_group_title(g, ': '))}**\n"]
        rows += _steps_md(g, f" ({_md(g['reduce_note'])})" if g["reduce_note"] else "")
        if g["keep"]:
            rows.append(f"- {_md(g['keep'])}")
        rows.append(f"- 지켜볼 증상: {_md(g['watch'])}")
        if g["action"]:
            rows.append(f"- {_md(g['action'])}")
        md.append("\n".join(rows))
    tail = _join(_md(out["optional_note"]), _md(out["action"]))
    if tail:
        md.append(tail)
    md += [_animal_md(an) for an in out["animals"]]
    md.append(f"*{_md(out['source_line'])}*")
    return "\n\n".join(md)


def observation_md(assessments, screening) -> str:
    """'관찰 필요' 항목의 지켜볼 증상과 그때 할 일 — 리포트의 '관찰이 필요한 알러젠' 절 안에 들어간다.
    카드뉴스의 '관찰 필요' 묶음과 같은 문장을 싣는다. 예전에는 지켜볼 증상만 적고(벌독은 그것도 빼고)
    '바로 응급실로 가세요' 같은 할 일은 카드뉴스에만 있었다."""
    out = sensitized_prevention(assessments, screening, REPORT_TIPS)
    if not out:
        return ""
    md = []
    rows = [f"- {g['emoji']} {_md(_group_title(g, ': '))} — {_md(_join(_stop('지켜볼 증상: ' + g['watch']), g['action']))}"
            for g in out["watch_groups"]]
    if rows:
        md.append(f"**{_md(out['watch_title'])}**\n\n" + "\n".join(rows))
    md += [_animal_md(an) for an in out["watch_animals"]]
    return "\n\n".join(md)


def _drug_rows_md(s: Dict[str, Any], T: Dict[str, Any], indent: str) -> List[str]:
    """약물 선택지. 환자가 쓰는 약은 이름만 적고 설명은 '지금 쓰는 약' 절에 맡긴다(같은 설명을 두 번 싣지 않는다)."""
    rows = []
    if s["no_asthma"]:
        rows.append(f"{indent}- {_md(s['no_asthma'])}")
    if s["drugs"]:
        rows.append(f"{indent}- 약물 치료 선택지(일반 정보):")
        rows += [f"{indent}    - {_md(d['name'])} **({_md(T['taking_report_ko'])})**" if d["taking"]
                 else f"{indent}    - {_md(d['name'])} — {_md(d['text'])}" for d in s["drugs"]]
    rows += [f"{indent}- {_md(n)}" for n in s["notes"]]
    if s["urgent"]:
        rows.append(f"{indent}- ⚠️ {_md(s['urgent'])}")
    return rows


def symptoms_md(assessments, screening) -> str:
    groups = symptom_groups(assessments, screening)
    if not groups:
        return ""
    T = _load(TREATMENT_PATH)["symptom_text"]
    taking = any(d["taking"] for group in groups for s in group for d in s["drugs"])
    md = [f"### 🩺 {_md(T['title_ko'])}", _join(_md(T["intro_ko"]), _md(T["taking_intro_report_ko"]) if taking else "")]
    for group in groups:
        first = group[0]
        avoid = ""
        if first["avoid"]:
            avoid = ("- 노출 줄이기: " + "; ".join(f"{_md(t['label'])} — {_md(t['tip'])}" for t in first["avoid"])
                     + f". {_md(T['avoid_more_report_ko'])}")
        if len(group) == 1:
            s = first
            rows = [f"**{s['emoji']} {_md(s['label'])}**\n",
                    f"- 노출과의 관계: {_join(_md(s['how']), _md(s['link_text']))}"]
            if avoid:
                rows.append(avoid)
            if s["selfcare"]:
                rows.append("- 증상 다스리기: " + _join(*(_md(t) for t in s["selfcare"])))
            rows += _drug_rows_md(s, T, "")
        else:
            rows = ["**" + " · ".join(f"{s['emoji']} {_md(s['label'])}" for s in group) + "**\n",
                    f"- 노출과의 관계: {_md(first['link_text'])}"]
            if avoid:
                rows.append(avoid)
            for s in group:
                rows.append(f"- **{s['emoji']} {_md(s['label'])}:** {_md(s['how'])}")
                if s["selfcare"]:
                    rows.append("    - 증상 다스리기: " + _join(*(_md(t) for t in s["selfcare"])))
                rows += _drug_rows_md(s, T, "    ")
        md.append("\n".join(rows))
    return "\n\n".join(md)


def medication_md(assessments, screening) -> str:
    out = medication_guidance(assessments, screening)
    if not out:
        return ""
    md = [f"## 💊 {_md(out['title'])}", _md(out["intro"])]
    said = set()      # 알러젠 이름·'쓰는 이유를 알 수 없음'은 첫 약에서만 다 적는다(약마다 같은 문장을 되풀이하지 않는다)
    for it in out["items"]:
        head = f"### {it['emoji']} {_md(it['label'])}"
        sub = " · ".join(x for x in [_md(it["type"]), ", ".join(_md(d) for d in it["diseases"])] if x)
        rows = []
        for ln in it["lines"]:
            kind = ln.get("kind")
            text = ln["brief"] if (kind and kind in said) else ln["text"]
            if kind:
                said.add(kind)
            rows.append(f"- **{_md(ln['head'])}:** {_md(text)}")
        md.append("\n\n".join(x for x in (head, sub, "\n".join(rows)) if x))
    if out["season_note"]:
        md.append(f"- {_md(out['season_note'])}")
    for n in out["notes"]:
        md.append(f"- {_md(n)}")
    if out["note"]:
        md.append("- " + _md(out["note_tpl"]).replace("&#123;note&#125;", _md(out["note"])))
    md.append(f"**{_md(out['closing'])}**")
    if out["sources"]:
        md.append("*출처: " + "; ".join(_md(c) for c in out["sources"]) + ". 전문의 검토 전 자료입니다.*")
    return "\n\n".join(md)


# ---------------------------------------------------------------------------
# 카드뉴스(HTML) — 모든 값은 html.escape 를 거친다. 퀘스트·클래식이 같은 카드를 쓴다.
# 카드 종류는 기존 'detail'(동물 실천 카드는 'animal')을 쓴다 — 덱 셸은 건드리지 않는다.
# 카드 수를 줄이기 위해: 이어지는 알러젠이 같은 주증상은 한 장, 같은 묶음의 약은 한 장, 실천 항목이 없는
# 동물·관찰 필요 항목은 '예방과 관찰' 카드의 한 줄로 싣는다. 줄인 내용은 리포트에 그대로 있다.
# ---------------------------------------------------------------------------
def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _card(tag: str, title: str, body: str) -> str:
    return f"""
        <div class="section detail">
          <div class="tag">{tag}</div>
          <h2>{title}</h2>
          {body}
        </div>"""


def _steps_text(block: Dict[str, Any]) -> str:
    return _join(
        _stop(f"{block['low_label']}: " + "; ".join(block["low"])) if block["low"] else "",
        _stop(f"{block['optional_label']}: " + "; ".join(block["optional"])) if block["optional"] else "",
        block["same"])


def _group_item(g: Dict[str, Any]) -> str:
    text = _join(_steps_text(g), g["keep"], _stop("지켜볼 증상: " + g["watch"]), g["action"])
    return f'<li>{g["emoji"]} <b>{_e(_group_title(g, " · "))}</b> — {_e(text)}</li>'


def _animal_item(an: Dict[str, Any]) -> str:
    text = _join(an["card"], an["same"], _stop("지켜볼 증상: " + an["watch_line"]))
    return f'<li>{an["emoji"]} <b>{_e(an["label"])}</b> — {_e(text)}</li>'


def prevention_cards(assessments, screening) -> List[str]:
    out = sensitized_prevention(assessments, screening, CARD_TIPS)
    if not out:
        return []
    has_steps = lambda an: bool(an["low"] or an["optional"])   # noqa: E731
    cards = []
    sens_items = "".join(_group_item(g) for g in out["groups"]) + \
        "".join(_animal_item(an) for an in out["animals"] if not has_steps(an))
    if sens_items and out["action"]:
        sens_items += f'<li>🩺 {_e(out["action"])}</li>'
    watch_items = "".join(_group_item(g) for g in out["watch_groups"]) + \
        "".join(_animal_item(an) for an in out["watch_animals"] if not has_steps(an))
    if sens_items or watch_items:
        body = f'<ul class="tips">{sens_items}</ul>' if sens_items else ""
        if watch_items:
            head = (f'<div class="mini-title">🟡 {_e(out["watch_title"])}</div>' if sens_items
                    else f'<p class="desc">{_e(out["watch_card"])}</p>')
            body += f'{head}<ul class="tips">{watch_items}</ul>'
        cards.append(_card("🌱 예방과 관찰" if sens_items else "🟡 관찰 필요",
                           ("감작만 된 항목,<br/>이렇게 지켜보세요" if sens_items
                            else "관찰이 필요한 항목,<br/>이렇게 지켜보세요"), body))
    for an in out["animals"] + out["watch_animals"]:
        if not has_steps(an):
            continue
        # 실천 항목이 있는 카드만 🐾 태그(= 'animal' 종류, 내 실천 체크)
        steps = "".join(f"<li>{_e(s)}</li>" for s in an["low"]) + \
            "".join(f'<li>{_e(an["optional_label"])} · {_e(s)}</li>' for s in an["optional"])
        watch = "".join(f"<li>{_e(w)}</li>" for w in an["watch"])
        body = (f'<p class="desc">{_e(_join(an["card"], an["same"]))}</p><ul class="tips">{steps}</ul>'
                f'<div class="mini-title">👀 이런 증상이 생기면 다시 평가</div><ul class="tips">{watch}</ul>'
                f'<p class="desc" style="margin-top:8px">{_e(an["retest"])}</p>')
        cards.append(_card(f'🐾 {_e(an["label"])} · 예방', _e(an["headline"]), body))
    return cards


def _drug_names(s: Dict[str, Any], T: Dict[str, Any]) -> str:
    """약물 선택지의 이름만(쓰는 약에는 '지금 사용 중' 표시). 주의 문장은 이름 목록에 섞지 않고 뒤에 문장으로 붙인다."""
    names = " · ".join(d["name"] + (f"({T['taking_ko']})" if d["taking"] else "") for d in s["drugs"])
    return _join(_stop(names), *s["notes"])


def symptom_cards(assessments, screening) -> List[str]:
    groups = symptom_groups(assessments, screening)
    if not groups:
        return []
    T = _load(TREATMENT_PATH)["symptom_text"]
    cards = []
    for group in groups:
        first = group[0]
        avoid = ""
        if first["avoid"]:
            tips = _stop("; ".join(f'{t["label"]} — {t["tip"]}' for t in first["avoid"]))
            avoid = f'<li>🛡️ <b>노출 줄이기</b> {_e(tips)}</li>'
        if len(group) == 1:
            s = first
            items = [f'<li>🔗 <b>노출과의 관계</b> {_e(_join(s["how_card"], s["link_text"]))}</li>', avoid]
            if s["selfcare"]:
                items.append(f'<li>🏠 <b>증상 다스리기</b> {_e(_join(*s["selfcare"][:2]))}</li>')
            if s["no_asthma"]:
                items.append(f'<li>🫁 {_e(s["no_asthma"])}</li>')
            drugs = _drug_names(s, T)
            if drugs:
                items.append(f'<li>💊 <b>약물 치료 선택지</b> {_e(_join(drugs, T["drugs_closing_card_ko"]))}</li>')
            if s["urgent"]:
                items.append(f'<li>⚠️ {_e(s["urgent"])}</li>')
            title = _e(s["label"])
        else:
            items = [f'<li>🔗 <b>노출과의 관계</b> {_e(first["link_text"])}</li>', avoid]
            for s in group:
                drugs = _drug_names(s, T)
                text = _join(s["how_card"], ("🏠 " + s["selfcare"][0]) if s["selfcare"] else "",
                             ("💊 " + drugs) if drugs else "")
                items.append(f'<li>{s["emoji"]} <b>{_e(s["label"])}</b> {_e(text)}</li>')
            items += [f'<li>🫁 {_e(s["no_asthma"])}</li>' for s in group if s["no_asthma"]]
            urgent = _join(*(s["urgent"] for s in group))
            if urgent:
                items.append(f'<li>⚠️ {_e(urgent)}</li>')
            items.append(f'<li>🧑‍⚕️ {_e(T["drugs_closing_card_ko"])}</li>')
            title = _e(" · ".join(s["short"] for s in group) + " 증상")
        cards.append(_card(f'{first["emoji"]} 내 주증상', title, f'<ul class="tips">{"".join(items)}</ul>'))
    return cards


def medication_cards(assessments, screening) -> List[str]:
    out = medication_guidance(assessments, screening)
    if not out:
        return []
    tail = "".join(f"<li>📝 {_e(n)}</li>" for n in out["notes"])
    if out["note"]:
        tail += "<li>📝 " + _e(out["note_tpl"]).replace("{note}", _e(out["note"])) + "</li>"
    closing = f'<li>🧑‍⚕️ {_e(out["closing"])}</li>'
    cards = []
    for i, g in enumerate(out["card_groups"]):
        end = (tail if i == len(out["card_groups"]) - 1 else "") + closing
        if len(g["items"]) == 1:
            it = g["items"][0]
            sub = " · ".join(x for x in [_e(it["type"]), ", ".join(_e(d) for d in it["diseases"])] if x)
            items = "".join(f'<li><b>{_e(ln["head"])}</b> {_e(ln["text"])}</li>' for ln in it["lines"] if ln["card"])
            cards.append(_card(f'{it["emoji"]} 내 약 이해하기', _e(it["label"]),
                               f'<p class="desc">{sub}</p><ul class="tips">{items}{end}</ul>'))
            continue
        items = ""
        for it in g["items"]:
            dis = f' ({", ".join(_e(d) for d in it["diseases"])})' if it["diseases"] else ""
            items += (f'<li>{it["emoji"]} <b>{_e(it["label"])}</b>{dis} — '
                      f'{_e(_join(_stop(it["type"]), it["short"]))}</li>')
        if g["unmatched"]:
            items += f'<li>❓ <b>쓰는 이유</b> {_e(g["unmatched"])}</li>'
        if g["exposure"]:
            items += f'<li>🔗 <b>내 알러젠과의 관계</b> {_e(g["exposure"])}</li>'
        cards.append(_card(f'{g["emoji"]} 내 약 이해하기', _e(g["title"]), f'<ul class="tips">{items}{end}</ul>'))
    if not out["items"] and tail:
        cards.append(_card("💊 내 약 이해하기", "약물 치료에 대해", f'<ul class="tips">{tail}{closing}</ul>'))
    return cards
