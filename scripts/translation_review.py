#!/usr/bin/env python3
"""기계 번역 임상 용어 검수표를 만든다.

왜 필요한가
  서버 생성 콘텐츠(문진·지식베이스·리포트·카드뉴스)는 LLM 이 한국어에서 영어·중국어로
  옮긴다. 환자용 의료 문서라 "집을 비우면 호전" 이 "worsen when the house is empty" 로
  뒤집히는 것 같은 오역은 그냥 넘길 수 없다(실제로 발견해 원문을 고쳤다).
  이 스크립트는 전 구간을 한 번 실행해 (원문, 영어, 중국어) 쌍을 모으고, 임상 용어가
  들어간 문장만 골라 사람이 검수할 표를 만든다.

동작
  1. 데모 검사 결과로 문진 → 판정 → 리포트 → 카드뉴스를 en·zh 로 실행한다.
     translate_batch 를 감싸서 실제로 오간 (원문, 번역) 쌍을 그대로 기록한다.
  2. 캐시(data/i18n_cache.json)에 있으면 API 호출이 없다. 없으면 그때만 번역한다.
  3. 측정 단위(mm·kU/L·℃·%)에 붙은 숫자가 번역에서 달라졌으면 따로 경고한다.
     월 이름이나 수사처럼 말로 푸는 표현은 정상이므로 대상에서 뺀다.

사용
  python3 scripts/translation_review.py [출력.md]
"""
import collections
import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.INFO)

# 오역 시 임상적으로 문제가 되는 용어 — 이 단어가 든 문장을 검수 대상으로 삼는다
TERMS = {
    "판정·개념": ["감작", "임상적", "교차반응", "특이 IgE", "검출한계", "위음성", "양성", "음성", "판정"],
    "증상·질환": ["아나필락시스", "두드러기", "비염", "천식", "결막염", "아토피", "쇼크",
                "호흡곤란", "가려움", "재채기", "콧물", "코막힘", "구강알레르기"],
    "치료·조치": ["면역치료", "항히스타민", "에피네프린", "응급", "회피", "복용", "중단"],
    "검사·측정": ["팽진", "히스타민", "대조", "class", "kU/L", "mm"],
}
FLAT = [(cat, t) for cat, ts in TERMS.items() for t in ts]

# 반드시 숫자 그대로 남아야 하는 측정 단위만 본다.
# "주 1회"→"once a week", "24시간"→"24 hours" 처럼 말로 푸는 것은 정상이라 제외한다.
UNIT = r"(?:mm|kU/?L|IU/?mL|℃|°C|%)"


def numbers(s: str):
    return sorted(re.findall(r"(\d+(?:[.,]\d+)?)\s*" + UNIT, s, flags=re.I))


def collect_pairs():
    """서버를 실제로 돌려 (원문 → {en, zh}) 쌍을 모은다."""
    from fastapi.testclient import TestClient
    import services.translation_service as ts

    pairs = collections.defaultdict(dict)
    original = ts.TranslationService.translate_batch

    def spy(self, texts, lang):
        out = original(self, texts, lang)
        if lang in ("en", "zh"):
            for src, dst in zip(texts, out):
                if isinstance(src, str) and isinstance(dst, str) and src != dst:
                    pairs[src][lang] = dst
        return out

    ts.TranslationService.translate_batch = spy
    try:
        import server
        client = TestClient(server.app)
        ocr = client.get("/api/ocr/demo").json()
        screening = {"has_allergy_history": True, "symptom_seasons": ["spring"],
                     "symptom_systems": ["nose", "eye"]}
        for lang in ("en", "zh"):
            q = client.post("/api/questionnaire",
                            json={"ocr": ocr, "screening": screening, "lang": lang})
            q.raise_for_status()
            answers = {}

            def walk(node):
                if isinstance(node, dict):
                    if node.get("id") and node.get("options"):
                        opt = node["options"][0]
                        val = opt.get("value") if isinstance(opt, dict) else opt
                        answers[node["id"]] = [val] if node.get("type") == "multi" else val
                    for v in node.values():
                        walk(v)
                elif isinstance(node, list):
                    for v in node:
                        walk(v)

            walk(q.json().get("questionnaire"))
            client.post("/api/classify",
                        json={"ocr": ocr, "screening": screening, "answers": answers,
                              "lang": lang, "ui": "quest"}).raise_for_status()
    finally:
        ts.TranslationService.translate_batch = original
    return dict(pairs)


def build(pairs, out_path: Path):
    rows = []
    for ko, tr in pairs.items():
        if not tr.get("en") and not tr.get("zh"):
            continue
        cats = sorted({c for c, term in FLAT if term in ko})
        if cats:
            rows.append({"ko": ko, "en": tr.get("en", ""), "zh": tr.get("zh", ""), "cats": cats})

    alerts = [(lang, r["ko"], r[lang]) for r in rows for lang in ("en", "zh")
              if r[lang] and numbers(r["ko"]) != numbers(r[lang])]
    rows.sort(key=lambda r: (-len(r["cats"]), len(r["ko"])))

    def cell(s):
        return s.replace("\n", "<br>").replace("|", "\\|")

    md = ["# 기계 번역 임상 용어 검수표\n",
          "서버 생성 콘텐츠(문진·지식베이스·리포트·카드뉴스)의 한→영/중 번역 중 "
          "**오역 시 임상적으로 문제가 되는 문장**만 골랐습니다.\n",
          f"- 수집한 번역 쌍: **{len(pairs)}건** (데모 검사 결과 1건으로 전 구간 실행)",
          f"- 임상 용어 포함: **{len(rows)}건**",
          f"- 측정 단위 숫자 자동 대조 불일치: **{len(alerts)}건**\n",
          "재생성: `python3 scripts/translation_review.py`\n",
          "검수 방법: 각 행의 영어·중국어가 한국어 원문과 **같은 임상 지시**를 하는지 확인하고, "
          "고쳐야 하면 `수정안` 칸에 적어 주세요.\n",
          "\n## 이미 찾아 고친 오역 (참고)\n",
          "이 표를 처음 만들다가 방향이 뒤집힌 문장을 찾아 **원문 쪽을 고쳤습니다**. "
          "같은 유형이 남아 있는지 보면서 검수해 주세요.\n",
          "| | 내용 |", "|---|---|",
          "| 증상 | 한국어 `저녁·새벽·이른 아침 악화·집을 비우면 호전·먼지 노출 시 악화` 가 영어로 "
          "`worsen in the evening, early morning, and when the house is empty` 로 번역됐습니다. "
          "집을 비우면 **좋아진다**는 진단 단서가 **나빠진다**로 뒤집혔습니다. |",
          "| 원인 | 방향이 다른 단서를 `·` 로 이어 붙여 각 항목의 악화/호전이 사라졌습니다. "
          "한국어로 읽어도 모호합니다. |",
          "| 조치 | 단서마다 완결된 문장으로 바꾸고 마침표로 나눴습니다. "
          "`집을 비우면 증상이 좋아집니다.` → `Symptoms improve when away from home.` 으로 "
          "정확해졌고 한국어 가독성도 좋아졌습니다. |",
          "| 교훈 | 방향이 다른 단서를 한 줄에 이어 붙이지 않습니다. "
          "번역기는 앞선 서술어를 뒤 항목에 그대로 씌웁니다. |\n"]

    if alerts:
        md += ["\n## ⚠️ 측정 단위 숫자가 달라진 문장\n",
               "| 언어 | 원문 | 번역 |", "|---|---|---|"]
        md += [f"| {l} | {cell(k)[:160]} | {cell(t)[:160]} |" for l, k, t in alerts[:40]]
    else:
        md.append("\n## ✅ 측정 단위 숫자\n\n검수 대상 문장에서 mm·kU/L·℃·% 수치가 바뀐 사례는 없습니다.\n")

    md += ["\n## 검수표\n",
           "| # | 분류 | 한국어 원문 | English | 中文 | 검수 | 수정안 |",
           "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        md.append(f"| {i} | {' · '.join(r['cats'])} | {cell(r['ko'])} | "
                  f"{cell(r['en'])} | {cell(r['zh'])} | ☐ | |")

    out_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return len(rows), len(alerts)


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "translation_review.md"
    pairs = collect_pairs()
    rows, alerts = build(pairs, out)
    print(f"pairs={len(pairs)} rows={rows} alerts={alerts} -> {out}")


if __name__ == "__main__":
    main()
