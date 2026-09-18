#!/usr/bin/env python3
"""항원 지식 생성기 — 레지스트리 148종 중 개별 지식이 없는 항원을 채운다.

두 갈래로 채운다(요청에 따라 1번+3번 방식).

  A. 결정론 부분(기본, 바로 환자에게 보임)
     `data/allergen_category_profiles.json` 의 카테고리·성분군 템플릿
     + `data/pollen_season_korea.json` 의 종별 비산 시기
     + 레지스트리에 이미 붙어 있는 성분 태그(tropomyosin·parvalbumin·PR-10 …)
     → 노출 환경·회피 수칙·증상·문진 프로브·교차반응. 사람이 쓴 문장이라 재현 가능하고 검증된다.

  B. LLM 설명문(선택, `--llm`, 검토 전에는 환자에게 보이지 않음)
     항원 한 줄 소개(biology_ko)만 모델로 만든다. `review_status: "candidate"` 로 저장하고,
     `scripts/review_allergen_knowledge.py` 로 승인해야 환자 화면에 나간다.

왜 나눴나: 회피 수칙 같은 실행 지시는 틀리면 환자가 잘못 행동한다. 그래서 템플릿(사람이 쓴 것)만
쓴다. 반면 "이게 뭔지" 소개 문장은 표현의 문제라 모델이 초안을 잡고 사람이 승인하는 편이 빠르다.

사용법:
    python scripts/build_allergen_knowledge.py            # 결정론 부분만
    python scripts/build_allergen_knowledge.py --llm      # + LLM 소개문(candidate)
    python scripts/build_allergen_knowledge.py --dry-run  # 쓰지 않고 통계만
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

REGISTRY = BASE / "data" / "allergens.json"
SEED_KB = BASE / "data" / "allergen_knowledge_base.json"
PROFILES = BASE / "data" / "allergen_category_profiles.json"
SEASONS = BASE / "data" / "pollen_season_korea.json"
OUT = BASE / "data" / "allergen_knowledge_generated.json"

# 성분 태그 → 음식 템플릿. 위험도가 높은 성분을 먼저 본다(LTP·저장단백은 전신반응과 연관).
COMPONENT_TO_PROFILE = [
    ("nsltp", "food:ltp"),
    ("2s_albumin", "food:seed_storage"),
    ("tropomyosin", "food:shellfish"),
    ("parvalbumin", "food:fish"),
    ("serum_albumin", "food:mammal_meat"),
    ("pr10", "food:pr10"),
    ("profilin", "food:profilin"),
]

KNOWLEDGE_FIELDS = ("biology_ko", "exposure_environment_ko", "season_label_ko",
                    "seasonality_pattern", "peak_months_korea", "indoor_outdoor",
                    "cross_reactivity_ko", "typical_symptoms_ko", "avoidance_control_ko",
                    "relevance_probes_ko", "clinical_pearl_ko")


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def seed_keys(seed) -> set:
    """개별 지식이 이미 있는 이름들.

    KnowledgeService 와 **같은 정규화**를 써야 한다. 예전에는 단순 lower() 로 비교해서
    서비스는 개별 KB 로 잡는 항원을 빌더는 '없다'고 보고 템플릿을 중복 생성했다(9종).
    """
    from services.knowledge_service import _norm
    out = set()
    for e in seed.get("entries", []):
        for k in [e.get("canonical_name"), e.get("korean_name")] + (e.get("aliases") or []):
            if k:
                out.add(_norm(k))
    return out


def component_names(item) -> list:
    out = []
    for c in item.get("components") or []:
        out.append(c if isinstance(c, str) else (c.get("name") or c.get("id") or ""))
    return [x.lower() for x in out if x]


def pick_profile(item, profiles: dict) -> str:
    """항원 → 템플릿 키. 음식은 이름이 아니라 성분군으로 고른다."""
    cat = item.get("category") or "other"
    if cat != "food":
        return cat if cat in profiles else "other"
    comps = component_names(item)
    for comp, key in COMPONENT_TO_PROFILE:
        if comp in comps and key in profiles:
            return key
    return "food"


def resolve(profile_key: str, profiles: dict) -> dict:
    """inherits 를 따라 템플릿을 합친다(자식 값이 이긴다)."""
    prof = dict(profiles.get(profile_key) or {})
    parent = prof.pop("inherits", None)
    if parent:
        merged = dict(profiles.get(parent) or {})
        merged.update({k: v for k, v in prof.items() if v not in (None, "", [], {})})
        return merged
    return prof


def build_entry(item, profiles, seasons) -> dict:
    key = pick_profile(item, profiles)
    prof = resolve(key, profiles)
    name = item.get("canonical_name")
    entry = {
        "canonical_name": name,
        "korean_name": item.get("korean_name"),
        "aliases": item.get("aliases") or [],
        "category": item.get("category"),
        "profile_key": key,
        "source": "category_profile",
        "review_status": "curated_template",
    }
    for f in KNOWLEDGE_FIELDS:
        if f in prof:
            entry[f] = prof[f]

    # 꽃가루는 종별 시기로 덮어쓴다 — 카테고리 기본값(3~5월 등)보다 정확하다
    sp = (seasons.get("species") or {}).get(name)
    if sp:
        entry["season_label_ko"] = sp.get("season_label_ko", entry.get("season_label_ko"))
        entry["peak_months_korea"] = sp.get("months", entry.get("peak_months_korea"))
        notes = []
        if sp.get("cross_reactivity_ko"):
            # 과(科)가 다르면 교차반응도 다르다. 삼나무에 '자작나무과 PR-10' 을 붙이면 틀린 말이 된다.
            entry["cross_reactivity_ko"] = sp["cross_reactivity_ko"]
        if sp.get("note_ko"):
            notes.append(sp["note_ko"])
        if sp.get("is_entomophilous"):
            notes.append("곤충이 옮기는 꽃(충매화)이라 공기 중 꽃가루 농도는 낮은 편입니다.")
        if sp.get("is_mixture"):
            notes.append("여러 종을 섞은 혼합 항원이라 원인 종을 특정할 수 없습니다.")
        if notes:
            entry["species_note_ko"] = " ".join(notes)
        entry["source"] = "category_profile+pollen_calendar"

    comps = component_names(item)
    if comps:
        entry["components"] = comps
    if prof.get("is_control"):
        entry["is_control"] = True
    return entry


LLM_SYSTEM = (
    "You write one-sentence Korean descriptions of allergens for a patient-facing allergy report.\n"
    "Rules:\n"
    "1. Korean, 해요체, 1-2 sentences, max 90 characters total.\n"
    "2. Say what the allergen IS and where a person meets it. No advice, no treatment, no numbers.\n"
    "3. Never say how common an allergy to it is, and never imply the reader has it.\n"
    "4. Plain words a patient knows. No markup.\n"
    "5. If you are not confident what the allergen is, reply exactly: UNKNOWN"
)


def add_llm_descriptions(entries, model: str) -> int:
    """항원 소개문만 모델로 만든다. 검토 전에는 환자에게 보이지 않는다."""
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
    import os
    key = os.getenv("OPENAI_API_KEY", "")
    if not key or key == "your_openai_api_key_here":
        print("  ! OPENAI_API_KEY 가 없어 LLM 단계를 건너뜁니다.")
        return 0
    from openai import OpenAI
    client = OpenAI(api_key=key)
    done = 0
    for e in entries:
        if e.get("biology_ko") or e.get("is_control"):
            continue
        prompt = (f"알레르겐: {e['canonical_name']} (한국어명: {e.get('korean_name')}, "
                  f"분류: {e.get('category')})")
        try:
            r = client.chat.completions.create(
                model=model, max_completion_tokens=2000, reasoning_effort="low",
                messages=[{"role": "system", "content": LLM_SYSTEM},
                          {"role": "user", "content": prompt}])
            text = (r.choices[0].message.content or "").strip()
        except Exception as ex:  # noqa: BLE001
            print(f"  ! {e['canonical_name']}: {ex}")
            continue
        if not text or text == "UNKNOWN" or len(text) > 200:
            continue
        e["biology_ko"] = text
        e["biology_ko_status"] = "candidate"      # 승인 전에는 환자에게 보이지 않는다
        e["biology_ko_model"] = model
        e["biology_ko_generated_at"] = datetime.now(timezone.utc).isoformat()
        done += 1
        if done % 20 == 0:
            print(f"    … {done}건 생성")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="항원 소개문을 LLM 으로 생성(candidate)")
    ap.add_argument("--model", default="gpt-5.6-luna")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    registry = load(REGISTRY)["antigens"]
    if isinstance(registry, dict):
        registry = list(registry.values())
    seed = load(SEED_KB)
    profiles = load(PROFILES)["profiles"]
    seasons = load(SEASONS)
    known = seed_keys(seed)

    from services.knowledge_service import _norm
    missing = [r for r in registry
               if not any(_norm(x or "") in known
                          for x in [r.get("canonical_name"), r.get("korean_name")] + (r.get("aliases") or []))]
    print(f"레지스트리 {len(registry)}종 / 개별 지식 보유 {len(registry) - len(missing)}종 "
          f"/ 생성 대상 {len(missing)}종")

    entries = [build_entry(r, profiles, seasons) for r in missing]

    # 기존 출력에 LLM 소개문·검토 상태가 있으면 그대로 옮겨온다.
    # 템플릿을 고칠 때마다 LLM 을 다시 돌리면 비용이 낭비되고, 승인 이력도 사라진다.
    carried = 0
    if OUT.exists():
        try:
            prev = {e.get("canonical_name"): e for e in load(OUT).get("entries", [])}
            for e in entries:
                old_e = prev.get(e["canonical_name"]) or {}
                for k, v in old_e.items():
                    if k.startswith("biology_ko") and v:
                        e[k] = v
                        carried += 1 if k == "biology_ko" else 0
        except Exception as ex:  # noqa: BLE001
            print(f"  ! 기존 출력을 읽지 못했습니다: {ex}")
    if carried:
        print(f"기존 LLM 소개문 {carried}건 유지")

    by_profile = {}
    for e in entries:
        by_profile[e["profile_key"]] = by_profile.get(e["profile_key"], 0) + 1
    print("\n템플릿 배정:")
    for k, v in sorted(by_profile.items(), key=lambda x: -x[1]):
        print(f"  {k:22} {v:3}종")
    seasoned = sum(1 for e in entries if "species_note_ko" in e or
                   e.get("source", "").endswith("pollen_calendar"))
    print(f"\n종별 꽃가루 시기 적용: {seasoned}종")

    llm_count = 0
    if args.llm:
        print(f"\nLLM 소개문 생성({args.model}) …")
        llm_count = add_llm_descriptions(entries, args.model)
        print(f"  생성 {llm_count}건 (모두 candidate — 승인 전 비노출)")

    out = {
        "version": "1.0",
        "locale": "ko-KR",
        "generated_by": "scripts/build_allergen_knowledge.py",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "description": "카테고리·성분군 템플릿 + 국내 꽃가루 시기로 만든 항원 지식. "
                       "biology_ko 가 candidate 인 항목은 검토 전까지 환자에게 노출되지 않는다.",
        "count": len(entries),
        "llm_candidates": llm_count,
        "entries": entries,
    }
    if args.dry_run:
        print("\n(dry-run) 파일을 쓰지 않았습니다.")
        return
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n기록: {OUT.relative_to(BASE)} ({len(entries)}종)")


if __name__ == "__main__":
    main()
