#!/usr/bin/env python3
"""온톨로지(질환 일반 지식)를 리포트·카드뉴스용 한국어 요약으로 만든다 — 결정론적, LLM 없음.

왜 손으로 고른 대응표인가
  온톨로지 원문은 영어이고 추출 라벨에 잡음이 많다(비염 '치료'에 'house dust mites'·'intranasal',
  천식 '치료'에 'misinformation'·'side effect', 아토피 '평가'에 감별진단 'psoriasis').
  LLM 으로 번역·선별해 보니 천식의 'inhaled corticosteroids'를 '비강 스테로이드 스프레이'로,
  두드러기의 항히스타민제 두 종을 '유발 요인 피하기'로 옮겼다. 환자에게 남는 문서에는 쓸 수 없다.
  그래서 쓸 라벨을 여기서 명시적으로 고르고 한국어를 붙인다. 목록에 없는 라벨은 들어가지 않는다.

지키는 규칙 (스냅샷 usage_rules)
  - 각 항목은 온톨로지의 실제 라벨에 묶이고 claim id 를 함께 남긴다. 라벨이 스냅샷에서 사라지면
    빌드가 실패한다(조용히 틀린 내용을 내보내지 않는다).
  - 약은 계열 이름만. 개별 약 이름·용량·수치·확률은 쓰지 않는다.
  - 모든 항목은 검토 전(candidate)이다. 결과 파일에 그 상태를 그대로 남긴다.

사용
  python3 scripts/build_ontology_summaries.py            # → data/ontology_disease_summaries_ko.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "ontology_disease_summaries_ko.json"

# 필드 → 온톨로지 술어
FIELD_PREDICATES = {
    "symptoms": ["has_symptom"],
    "evaluation": ["evaluated_with"],
    "management": ["has_treatment", "has_medication", "has_prevention"],
    "related": ["has_risk_factor"],
}

# (한국어, [근거 라벨...]) — 정의는 표준 용어(DO/HPO) 정의를 옮긴 것
CURATED = {
    "allergy": {
        "title_ko": "알레르기",
        "definition_ko": "알레르기는 보통은 해롭지 않은 물질에 몸의 면역계가 반응하는 것입니다.",
    },
    "allergic rhinitis": {
        "title_ko": "알레르기 비염",
        "definition_ko": "꽃가루·먼지·곰팡이·동물 비듬·바퀴벌레나 집먼지진드기 배설물 같은 알레르기 유발 물질 "
                         "때문에 코 점막에 알레르기 염증이 생기는 병입니다. 재채기, 콧물, 코막힘, 눈 가려움과 "
                         "눈물이 나타납니다.",
        "symptoms": [("재채기", ["sneezing"]), ("코막힘·코 가려움", ["stuffy itchy nose"]),
                     ("눈물", ["watery eyes"]), ("눈 주위 부기", ["swelling around the eyes"])],
        "evaluation": [("증상 확인", ["based on symptoms"]), ("피부단자검사", ["skin prick test"]),
                       ("특이 IgE 혈액검사", ["blood tests for specific antibodies"])],
        "management": [("비강 스테로이드 스프레이", ["nasal steroids"]),
                       ("항히스타민제", ["antihistamines", "antihistamines such as loratadine"]),
                       ("코 세척", ["nasal irrigation"]),
                       ("알레르기 면역치료", ["allergen immunotherapy"])],
        "related": [("천식", ["asthma"]), ("아토피 피부염", ["atopic dermatitis"]),
                    ("알레르기 결막염", ["allergic conjunctivitis"])],
    },
    "asthma": {
        "title_ko": "천식",
        "definition_ko": "기관지에 만성 염증이 생겨 기도가 좁아지는 병으로, 환경 요인과 유전 요인이 함께 "
                         "작용합니다. 쌕쌕거림(숨 쉴 때 휘파람 같은 소리)이 되풀이되고, 가슴 답답함·숨참·"
                         "가래·기침이 나타납니다.",
        "symptoms": [("반복되는 쌕쌕거림", ["recurring episodes of wheezing"]),
                     ("가슴 답답함", ["chest tightness"]), ("숨참", ["shortness of breath"]),
                     ("기침", ["coughing"])],
        "evaluation": [("증상 확인", ["based on symptoms"]), ("폐기능검사(폐활량 측정)", ["spirometry"]),
                       ("치료에 대한 반응 확인", ["response to therapy"])],
        "management": [("유발 요인 피하기", ["avoiding triggers"]),
                       ("흡입 스테로이드", ["inhaled corticosteroids"]),
                       ("기관지 확장제", ["bronchodilators"]),
                       ("호흡 재활", ["pulmonary rehabilitation"])],
        "related": [("다른 아토피 질환", ["atopic disease"])],
    },
    "atopic dermatitis": {
        "title_ko": "아토피 피부염",
        "definition_ko": "피부의 알레르기성 염증이 좋아졌다 나빠지기를 되풀이하는 만성 질환으로, 가려움과 "
                         "각질이 생깁니다.",
        "symptoms": [("가려움", ["itchy"]), ("붉어짐", ["red"]), ("부기", ["swollen"]),
                     ("피부 갈라짐", ["cracked skin"])],
        "evaluation": [("다른 원인을 배제한 뒤 증상으로 진단",
                        ["based on symptoms after ruling out other possible causes"])],
        "management": [("매일 목욕 후 보습제 바르기", ["daily bathing followed by moisturising cream"]),
                       ("악화 요인 피하기", ["avoiding things that worsen the condition"]),
                       ("악화 시 스테로이드 연고", ["steroid creams for flares Humidifier"])],
        "related": [],
    },
    "urticaria": {
        "title_ko": "두드러기",
        "definition_ko": "피부에 옅은 붉은색으로 부풀어 오르고 가려운 발진이 생기는 피부 질환입니다.",
        "symptoms": [("붉게 부풀어 오른 가려운 발진", ["itchy bumps", "raised", "red"])],
        "evaluation": [("증상 확인", ["based on symptoms"])],
        "management": [("항히스타민제", ["antihistamine"]), ("스테로이드", ["corticosteroid"]),
                       ("류코트리엔 조절제", ["leukotriene inhibitors"])],
        "related": [("천식", ["asthma"]), ("건초열(알레르기 비염)", ["hay fever"])],
    },
}


def main() -> None:
    from services.ontology_service import get_ontology_service

    onto = get_ontology_service()
    out = {"source": "docs/20260916-allergy-chatbot/ontology-snapshot.json",
           "method": "curated label map (scripts/build_ontology_summaries.py), no LLM",
           "review_status": "candidate",
           "notice_ko": "Wikipedia 기반 온톨로지에서 고른 질환 일반 정보입니다. 전문가 검토 전이며, "
                        "이 환자의 검사 결과가 아닙니다.",
           "topics": {}}
    missing = []
    for topic, cur in CURATED.items():
        d = onto.definition(topic) or {}
        topic_out = {"title_ko": cur["title_ko"], "definition_ko": cur["definition_ko"],
                     "definition_source": {k: d.get(k) for k in ("system", "code", "release", "label")},
                     "source_url": onto.topic_status(topic).get("url"), "review_status": "candidate"}
        for field, preds in FIELD_PREDICATES.items():
            by_label = {f["label"]: f for f in onto.facts(topic, preds, 200)}
            items = []
            for ko, labels in cur.get(field, []):
                hits = [by_label[lb] for lb in labels if lb in by_label]
                gone = [lb for lb in labels if lb not in by_label]
                if gone:
                    missing.append(f"{topic}/{field}: {gone}")
                    continue
                items.append({"ko": ko, "labels_en": labels,
                              "claims": sorted({h["claim_id"] for h in hits if h.get("claim_id")})})
            topic_out[field] = items
        out["topics"][topic] = topic_out
        print(f"{topic}: " + " · ".join(f"{k} {len(topic_out[k])}" for k in FIELD_PREDICATES))
    if missing:
        sys.exit("스냅샷에 없는 라벨(대응표 갱신 필요):\n  " + "\n  ".join(missing))
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {OUT}")


if __name__ == "__main__":
    main()
