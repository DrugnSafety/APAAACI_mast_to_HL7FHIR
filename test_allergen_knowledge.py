"""항원 지식 템플릿 — 커버리지·우선순위·검토 게이트.

배경: 레지스트리 148종 중 사람이 쓴 개별 지식은 18종뿐이고, 나머지 128종은 도감 카드의
노출 환경·회피 수칙·교차반응이 전부 비어 있었다. 카테고리·성분군 템플릿으로 채우되
두 가지를 지켜야 한다.
  ① 사람이 쓴 개별 지식(18종)이 언제나 템플릿을 이긴다.
  ② LLM 이 만든 문장은 승인 전까지 환자에게 나가지 않는다.
"""
import json
from pathlib import Path

import pytest

from services.knowledge_service import (GENERATED_KB_PATH, KB_PATH, KnowledgeService,
                                        normalize_category)

DATA = Path(__file__).resolve().parent / "data"
PROFILES = json.loads((DATA / "allergen_category_profiles.json").read_text(encoding="utf-8"))
SEASONS = json.loads((DATA / "pollen_season_korea.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((DATA / "allergens.json").read_text(encoding="utf-8"))["antigens"]
if isinstance(REGISTRY, dict):
    REGISTRY = list(REGISTRY.values())


@pytest.fixture(scope="module")
def ks() -> KnowledgeService:
    return KnowledgeService(enable_web=False)


class TestCoverage:
    def test_every_registry_antigen_resolves_to_knowledge(self, ks):
        """148종 전부가 개별 KB 또는 템플릿으로 답이 나와야 한다."""
        missing = []
        for r in REGISTRY:
            hit = (ks.lookup(r.get("canonical_name")) or ks.lookup(r.get("korean_name"))
                   or ks.lookup_generated(r.get("canonical_name"))
                   or ks.lookup_generated(r.get("korean_name")))
            if not hit:
                missing.append(r.get("canonical_name"))
        assert not missing, f"지식이 없는 항원: {missing[:10]}"

    def test_generated_covers_everything_outside_the_seed(self, ks):
        """템플릿 + 개별 KB 가 겹치지 않고 레지스트리를 정확히 덮는다.

        개별 KB 는 18항목이지만 별칭 때문에 레지스트리 20행과 대응한다. 그래서 항목 수끼리
        더하면 148 이 되지 않는다 — 덮은 '행' 기준으로, 레지스트리 별칭까지 보고 센다
        (생성기가 쓰는 판정과 같아야 한다).
        """
        def seeded_row(r):
            names = [r.get("canonical_name"), r.get("korean_name")] + (r.get("aliases") or [])
            return any(ks.lookup_exact(x) for x in names if x)

        seeded = [r for r in REGISTRY if seeded_row(r)]
        assert len(seeded) + len(ks.generated) == len(REGISTRY)
        for r in seeded:      # 개별 지식이 있는 항원을 템플릿이 중복 생성하지 않았는지
            assert ks.lookup_generated(r.get("canonical_name")) is None

    def test_every_category_has_a_profile(self):
        cats = {normalize_category(r.get("category")) for r in REGISTRY}
        have = set(PROFILES["profiles"])
        assert not (cats - have - {"other"}), f"템플릿 없는 카테고리: {cats - have}"

    def test_non_control_entries_have_actionable_advice(self, ks):
        """대조 항목을 뺀 모든 항원은 회피 수칙과 노출 환경이 있어야 한다(예전엔 전부 비어 있었다)."""
        thin = [e["canonical_name"] for e in ks.generated
                if not e.get("is_control")
                and (not e.get("avoidance_control_ko") or not e.get("exposure_environment_ko"))]
        assert not thin, f"조언이 비어 있는 항원: {thin[:10]}"


class TestProfiles:
    def test_food_is_grouped_by_component_not_name(self, ks):
        """음식은 이름이 아니라 성분군으로 묶여야 임상적으로 맞다."""
        cases = {"Shrimp": "food:shellfish", "Mackerel": "food:fish", "Walnut": "food:ltp",
                 "Cashew": "food:seed_storage", "Beef": "food:mammal_meat"}
        for name, expected in cases.items():
            e = ks.lookup_generated(name)
            if e is None:      # 개별 KB 가 이미 가진 항원(새우 등)은 건너뛴다
                continue
            assert e["profile_key"] == expected, f"{name} -> {e['profile_key']}"

    def test_inherited_profile_keeps_parent_fields(self, ks):
        e = ks.lookup_generated("Walnut")
        assert e["profile_key"] == "food:ltp"
        assert e["relevance_probes_ko"], "food 부모의 문진 프로브를 물려받아야 한다"
        assert "LTP" in e["exposure_environment_ko"], "자식 템플릿이 노출 설명을 덮어써야 한다"

    def test_control_antigens_are_marked_and_give_no_avoidance(self, ks):
        for name in ("Control", "Histamine"):
            e = ks.lookup_generated(name)
            assert e and e.get("is_control") is True
            assert e.get("avoidance_control_ko") == []
            assert "대조" in e.get("clinical_pearl_ko", "") + e.get("exposure_environment_ko", "")


class TestPollenSeasons:
    def test_species_season_overrides_category_default(self, ks):
        """삼나무는 2~4월이다. 카테고리 기본값 3~5월로 두면 시즌을 놓친다."""
        cedar = ks.lookup_generated("Japanese cedar")
        assert cedar["peak_months_korea"] == [2, 3, 4]
        assert "2~4" in cedar["season_label_ko"]

    def test_cross_reactivity_follows_botanical_family(self, ks):
        """삼나무(측백나무과)에 '자작나무과 PR-10' 을 붙이면 틀린 말이다 — 실제로 났던 오류."""
        cedar = ks.lookup_generated("Japanese cedar")
        assert "측백나무과" in cedar["cross_reactivity_ko"]
        assert "자작나무과(자작" not in cedar["cross_reactivity_ko"]
        hazel = ks.lookup_generated("Hazel")
        assert "자작나무과" in hazel["cross_reactivity_ko"]

    def test_insect_pollinated_species_are_flagged(self, ks):
        """충매화는 공기 중 농도가 낮아 임상 의의가 다르다."""
        for name in ("Dandelion", "Chrysanthemum", "Acacia"):
            e = ks.lookup_generated(name)
            assert "충매화" in (e.get("species_note_ko") or ""), name

    def test_mixture_antigens_say_they_cannot_identify_a_species(self, ks):
        for name in ("Tree mixture 1", "Grass"):
            e = ks.lookup_generated(name)
            assert "혼합" in (e.get("species_note_ko") or ""), name

    def test_every_pollen_species_in_the_table_exists_in_the_registry(self):
        names = {r.get("canonical_name") for r in REGISTRY}
        unknown = set(SEASONS["species"]) - names
        assert not unknown, f"레지스트리에 없는 종: {unknown}"


class TestPrecedenceAndReviewGate:
    def test_hand_written_entry_beats_the_template(self, ks):
        """개별 KB 18종은 템플릿보다 우선이다."""
        b = ks.get_backdata("Dermatophagoides farinae")
        assert b["source"] == "knowledge_base"

    def test_template_is_used_when_no_hand_written_entry(self, ks):
        b = ks.get_backdata("Penicillium")
        assert b["source"].startswith("category_profile")
        assert b["avoidance_control_ko"]

    def test_candidate_fields_are_hidden_by_default(self, tmp_path):
        """LLM 문장은 승인 전까지 환자에게 나가면 안 된다."""
        p = tmp_path / "gen.json"
        p.write_text(json.dumps({"entries": [{
            "canonical_name": "Testium", "korean_name": "테스트", "category": "other",
            "biology_ko": "검토 안 된 문장", "biology_ko_status": "candidate",
            "biology_ko_model": "m", "avoidance_control_ko": ["템플릿 수칙"],
        }]}, ensure_ascii=False), encoding="utf-8")

        hidden = KnowledgeService(enable_web=False, generated_path=p)
        out = hidden.get_backdata("Testium")
        assert "biology_ko" not in out or not out.get("biology_ko")
        assert "biology_ko_model" not in out
        assert out["avoidance_control_ko"] == ["템플릿 수칙"], "템플릿 필드는 남아야 한다"

        shown = KnowledgeService(enable_web=False, generated_path=p, include_candidates=True)
        assert shown.get_backdata("Testium")["biology_ko"] == "검토 안 된 문장"

    def test_approved_fields_are_served(self, tmp_path):
        p = tmp_path / "gen.json"
        p.write_text(json.dumps({"entries": [{
            "canonical_name": "Testium", "korean_name": "테스트", "category": "other",
            "biology_ko": "승인된 문장", "biology_ko_status": "approved",
        }]}, ensure_ascii=False), encoding="utf-8")
        assert KnowledgeService(enable_web=False,
                                generated_path=p).get_backdata("Testium")["biology_ko"] == "승인된 문장"

    def test_missing_generated_file_degrades_quietly(self, tmp_path):
        svc = KnowledgeService(enable_web=False, generated_path=tmp_path / "none.json")
        assert svc.generated == []
        assert svc.get_backdata("Penicillium")          # 기본값 경로로 여전히 답한다


class TestFuzzyMatchingDoesNotCrossCategories:
    """부분·퍼지 매칭이 엉뚱한 항원의 지식을 집어오던 버그의 회귀 방지.

    한국어 항원명이 서로의 부분 문자열인 경우가 많다 — '굴'은 '환삼덩굴' 안에 있고,
    '콩'은 '땅콩' 안에 있다. 그래서 굴(음식)에 꽃가루 지식(가을 시즌·야외 회피 수칙)이
    붙었다. 정확 일치 템플릿을 퍼지 매칭보다 먼저 보게 해서 막는다.
    """

    @pytest.mark.parametrize("name,expected_category", [
        ("Oyster", "food"),                 # 이전: 환삼덩굴 꽃가루(pollen_weed)
        ("Bean", "food"),                   # 이전: 땅콩
        ("Rabbit epithelium", "animal"),    # 이전: 고양이 비듬
        ("Rat epithelium", "animal"),       # 이전: 고양이 비듬
        ("Cockroach, American", "insect"),  # 이전: 독일바퀴
        ("Egg yolk", "food"),
        ("Aspergillus niger", "mold"),
        ("Grass", "pollen_grass"),          # 이전: 티모시 꽃가루
    ])
    def test_antigen_keeps_its_own_category(self, ks, name, expected_category):
        b = ks.get_backdata(name)
        assert normalize_category(b["category"]) == expected_category

    def test_oyster_does_not_get_pollen_season(self, ks):
        b = ks.get_backdata("Oyster")
        assert "가을" not in (b.get("season_label_ko") or "")
        assert not any("꽃가루" in a for a in (b.get("avoidance_control_ko") or []))

    def test_exact_seed_match_still_wins(self, ks):
        for name in ("Peanut", "Shrimp", "Cat dander", "Birch pollen"):
            assert ks.get_backdata(name)["source"] == "knowledge_base"

    def test_unknown_name_can_still_use_fuzzy_rescue(self, ks):
        """레지스트리에 없는 OCR 변형 이름은 여전히 퍼지 매칭으로 구제한다."""
        b = ks.get_backdata("Dermatophagoides farinae (Df)")
        assert b["source"] in ("knowledge_base", "knowledge_base_fuzzy")
        assert normalize_category(b["category"]) == "mite"
