"""알레르겐 환경관리 지식(교과서 정리) — 데이터 무결성·서비스·온톨로지 그래프·챗봇 주입·API 계약.

핵심 불변식
  ① 이 지식은 **알레르겐군의 일반 정보**다. 환자 개별 판정은 검사 결과와 문진에서 오고, 이 층은 '실제 주의'
     항원의 군에만 붙는다.
  ② 모든 조치는 단계(tier)·근거 등급·출처를 가지며, 출처는 sources 표에 실재해야 한다. 약 지시는 넣지 않는다.
  ③ 질환 스냅샷(Wikipedia, candidate)과 섞이지 않는다 — 검토 상태 `textbook_curated`, 노드 종류·검색 종류(kind)가 다르다.
  ④ 환자 보고서 값을 묻는 질문("제 진드기 수치가…")에는 붙지 않는다.
"""
import json
import re

import pytest

from models.schemas import AllergenAssessment, ClinicalRelevance, ScreeningProfile
from services.environment_control_service import (DATA_PATH, EnvironmentControlService,
                                                  get_environment_control_service)
from services.ontology_service import ENV_REVIEW_STATUS, NS, PREDICATE_LABEL_KO, get_ontology_service
from services.result_chat_service import ResultChatService


@pytest.fixture(scope="module")
def env() -> EnvironmentControlService:
    return get_environment_control_service()


@pytest.fixture(scope="module")
def data():
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def onto():
    return get_ontology_service()


def _assess(name, category, relevance=ClinicalRelevance.CLINICALLY_RELEVANT, korean=None):
    return AllergenAssessment(allergen_name=name, korean_name=korean, category=category, relevance=relevance)


# ---------------------------------------------------------------------------
# 데이터 무결성
# ---------------------------------------------------------------------------
class TestDataset:
    def test_groups_present(self, data):
        assert set(data["allergen_groups"]) >= {"mite", "cat", "dog", "cockroach", "mold_indoor",
                                                "mold_outdoor", "pollen", "rodent"}

    def test_every_measure_has_tier_grade_and_real_sources(self, data):
        src = set(data["sources"])
        owners = list(data["allergen_groups"].values()) + list(data["aggravating_factors"].values())
        n = 0
        for owner in owners:
            for m in owner["measures"]:
                n += 1
                assert m["tier"] in data["tiers"], m["id"]
                assert m["evidence_grade"] in data["evidence_grades"], m["id"]
                assert m["sources"] and set(m["sources"]) <= src, (m["id"], set(m["sources"]) - src)
                assert m["short_ko"] and m["text_ko"]
        assert n >= 60

    def test_thresholds_principles_cite_real_sources(self, data):
        src = set(data["sources"])
        for g in data["allergen_groups"].values():
            for t in g.get("thresholds", []):
                assert set(t["sources"]) <= src
        for p in data["principles"]:
            assert set(p["sources"]) <= src

    def test_measure_ids_unique(self, data):
        ids = [m["id"] for owner in list(data["allergen_groups"].values()) + list(data["aggravating_factors"].values())
               for m in owner["measures"]]
        assert len(ids) == len(set(ids))

    def test_no_medication_directives_in_patient_text(self, data):
        """교과서에 있는 '흡입스테로이드 용량을 두 배로' 같은 문장은 환자 문장에 넣지 않는다."""
        bad = re.compile(r"(용량을? (두 배|늘리|줄이)|스테로이드를 (쓰|드|복용하)세요|약을 (늘리|줄이|끊으)세요)")
        for owner in list(data["allergen_groups"].values()) + list(data["aggravating_factors"].values()):
            for m in owner["measures"]:
                assert not bad.search(m["text_ko"]), m["id"]
                assert not bad.search(m.get("short_ko", "")), m["id"]

    def test_textbook_sources_are_marked_as_textbook(self, data):
        assert data["sources"]["kaaaci_textbook_env"]["type"] == "textbook_ko"
        for k in ("middleton_ch26", "middleton_ch27", "middleton_ch28", "middleton_ch86"):
            assert data["sources"][k]["type"] == "textbook_en"

    def test_review_status_says_not_yet_reviewed(self, data):
        assert "검토 전" in data["review_status"]

    def test_key_textbook_numbers_are_kept(self, data):
        mite = data["allergen_groups"]["mite"]
        values = {(t["marker"], t["value"]) for t in mite["thresholds"]}
        assert ("Der p 1 / group 1", 2) in values and ("Der p 1", 10) in values
        assert mite["carrier_particle"]["size_um"] == [10, 35]
        cat = data["allergen_groups"]["cat"]
        assert any(t["value"] == 8 for t in cat["thresholds"])
        assert cat["carrier_particle"]["small_fraction_pct"] == [15, 40]
        pollen = data["allergen_groups"]["pollen"]
        assert pollen["korea_calendar"]["weed"]["months"] == [8, 9, 10]
        assert any(m["id"] == "pollen_thunderstorm" for m in pollen["measures"])


# ---------------------------------------------------------------------------
# 서비스
# ---------------------------------------------------------------------------
class TestService:
    @pytest.mark.parametrize("category,name,korean,expected", [
        ("mite", "Dermatophagoides farinae", "미국집먼지진드기", "mite"),
        ("animal", "Cat dander", "고양이 비듬", "cat"),
        ("animal", "Dog dander", "개 비듬", "dog"),
        ("animal", "Mouse epithelium", "쥐 상피", "rodent"),
        ("animal", "Horse dander", "말 비듬", None),          # 자료 없는 종은 지식베이스 문장을 쓴다
        ("insect", "German cockroach", "독일바퀴", "cockroach"),
        ("mold", "Alternaria alternata", "알테르나리아", "mold_outdoor"),
        ("mold", "Aspergillus fumigatus", "아스페르길루스", "mold_indoor"),
        ("pollen_weed", "Japanese hop pollen", "환삼덩굴", "pollen"),
        ("food", "Shrimp", "새우", None),
        ("venom", "Honey bee venom", "꿀벌독", None),
    ])
    def test_group_for(self, env, category, name, korean, expected):
        assert env.group_for(category, name, korean) == expected

    def test_measures_sorted_by_tier_and_labelled(self, env):
        ms = env.measures("mite")
        orders = [env.tier_order(m["tier"]) for m in ms]
        assert orders == sorted(orders)
        assert ms[0]["tier"] == "essential" and ms[0]["tier_ko"].startswith("1단계")
        assert all(m["citations"] and m["citations"][0]["citation"] for m in ms)

    def test_tiered_tips_exclude_not_recommended(self, env):
        tips = env.tiered_tips("cat", limit=20)
        assert tips and "타닌산" not in " ".join(tips)

    def test_measures_matching_prefers_question_keywords(self, env):
        hit = env.measures_matching("mite", "공기청정기가 도움이 되나요?", limit=1)[0]
        assert hit["id"] == "mite_air_cleaner" and hit["tier"] == "limited"
        hit = env.measures_matching("cat", "고양이 목욕시키면 괜찮아요?", limit=1)[0]
        assert hit["id"] == "cat_wash"

    def test_for_assessments_only_uses_clinically_relevant(self, env):
        items = [_assess("Dermatophagoides farinae", "mite"),
                 _assess("Cat dander", "animal", ClinicalRelevance.SENSITIZED_ONLY),
                 _assess("Shrimp", "food")]
        out = env.for_assessments(items)
        assert [b["group"] for b in out] == ["mite"]
        assert out[0]["measures"] and out[0]["sources"]
        assert all(m["tier"] in ("essential", "helpful") for m in out[0]["measures"])

    @pytest.mark.parametrize("q,expected", [
        ("제 집먼지진드기 수치가 얼마였죠?", []),                 # 보고서 값 질문 → 아무것도 붙이지 않는다
        ("집먼지진드기 class가 몇이었나요", []),
        ("고양이를 계속 키워도 될까요?", ["cat"]),
        ("공기청정기가 집먼지진드기에 도움이 되나요?", ["mite"]),
        ("곰팡이는 어떻게 없애요?", ["mold_indoor"]),
        ("What is asthma?", []),                                 # 알레르겐도 관리 의도도 없음
    ])
    def test_groups_for_question(self, env, q, expected):
        assert env.groups_for_question(q) == expected

    def test_intent_without_name_narrows_to_patient_groups(self, env):
        assert env.groups_for_question("공기청정기가 도움이 되나요?", patient_groups=["mite", "cat"]) == ["mite", "cat"]
        assert env.groups_for_question("공기청정기가 도움이 되나요?", patient_groups=[]) == []

    def test_missing_file_degrades_quietly(self, tmp_path):
        s = EnvironmentControlService(path=tmp_path / "nope.json")
        assert not s.available and s.group_for("mite", "x") is None and s.measures("mite") == []


# ---------------------------------------------------------------------------
# 온톨로지 그래프
# ---------------------------------------------------------------------------
class TestOntologyLayer:
    def test_allergen_topics_listed_separately_from_disease_topics(self, onto):
        assert {t["query"] for t in onto.topics()} == {"allergy", "asthma", "allergic rhinitis",
                                                        "atopic dermatitis", "urticaria"}
        keys = {t["topic"] for t in onto.allergen_topics()}
        assert {"mite", "cat", "dog", "cockroach", "pollen"} <= keys
        assert all(t["review_status"] == ENV_REVIEW_STATUS for t in onto.allergen_topics())

    def test_sparql_joins_allergen_group_to_disease_topic(self, onto):
        q = f"""PREFIX allergy: <{NS}> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?measure ?disease WHERE {{
          ?g allergy:query "mite" ; a allergy:AllergenGroup ;
             allergy:hasControlMeasure ?m ; allergy:associatedWithDisease ?d .
          ?m rdfs:label ?measure ; allergy:tier "essential" .
          ?d allergy:query ?disease . }}"""
        out = onto.sparql(q, limit=200)
        assert out["ok"]
        diseases = {r["disease"] for r in out["rows"]}
        assert {"asthma", "allergic rhinitis", "atopic dermatitis"} <= diseases

    def test_graph_has_thresholds_and_sources(self, onto):
        q = f"""PREFIX allergy: <{NS}> SELECT ?v ?unit ?cite WHERE {{
          ?g allergy:query "mite" ; allergy:hasThreshold ?t .
          ?t allergy:value ?v ; allergy:unit ?unit ; allergy:citedIn ?s . ?s rdfs:label ?cite }}
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>"""
        # PREFIX 는 앞에 와야 한다 — 질의를 올바른 순서로 다시 쓴다
        q = f"""PREFIX allergy: <{NS}> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?v ?unit ?cite WHERE {{
          ?g allergy:query "mite" ; allergy:hasThreshold ?t .
          ?t allergy:value ?v ; allergy:unit ?unit ; allergy:citedIn ?s . ?s rdfs:label ?cite }}"""
        out = onto.sparql(q, limit=50)
        assert out["ok"] and {float(r["v"]) for r in out["rows"]} >= {2.0, 10.0}

    def test_new_predicates_have_korean_labels(self):
        for p in ("has_control_measure", "has_major_allergen", "has_threshold", "has_cross_reactivity",
                  "associated_with_disease"):
            assert p in PREDICATE_LABEL_KO

    def test_search_topics_finds_allergen_groups_but_keeps_disease_aliases_clean(self, onto):
        hits = onto.search_topics("집먼지진드기")
        assert [(h["topic"], h["kind"]) for h in hits] == [("mite", "allergen")]
        # 'hay fever' 는 질환(비염) 문서의 별칭 — 알레르겐군이 끼어들면 안 된다
        assert [h["topic"] for h in onto.search_topics("hay fever")] == ["allergic rhinitis"]

    def test_allergen_facts_share_the_citation_shape(self, onto):
        facts = onto.allergen_facts("cat", "목욕", limit=3)
        assert facts and facts[0]["group_id"].startswith("env-measure:")
        f = facts[0]
        assert f["review_status"] == ENV_REVIEW_STATUS and f["predicate"] == "has_control_measure"
        assert f["evidence_id"].startswith("textbook:") and f["sources"] and f["tier_ko"]
        assert "cat_wash" in f["group_id"]

    def test_get_topic_context_and_evidence_handle_allergen_layer(self, onto):
        ctx = onto.get_topic_context("cockroach")
        assert ctx["status"]["kind"] == "allergen" and ctx["facts"]
        ev = onto.get_evidence("textbook:gotzsche_2008")
        assert ev["kind"] == "textbook_source" and "Cochrane" in ev["citation"]
        assert "error" in onto.get_evidence("textbook:nope")

    def test_retrieve_keeps_disease_and_allergen_layers_apart(self, onto):
        r = onto.retrieve("공기청정기가 집먼지진드기에 도움이 되나요?", [], allergen_groups=["mite"])
        assert r["topics"] == [] and [b["topic"] for b in r["allergen_topics"]] == ["mite"]
        assert r["allergen_topics"][0]["facts"][0]["group_id"] == "env-measure:mite_air_cleaner"
        r = onto.retrieve("알레르기 비염이 어떤 병인가요?", [])
        assert "allergic rhinitis" in [b["topic"] for b in r["topics"]] and r["allergen_topics"] == []

    def test_patient_value_question_retrieves_nothing_from_either_layer(self, onto):
        r = onto.retrieve("제 집먼지진드기 수치가 얼마였죠?", ["allergic_rhinitis"], allergen_groups=["mite"])
        assert r["topics"] == [] and r["allergen_topics"] == []


# ---------------------------------------------------------------------------
# 챗봇 주입
# ---------------------------------------------------------------------------
class TestChatInjection:
    def _svc(self):
        return ResultChatService(api_key="")

    def test_management_question_gets_textbook_block_with_tiers(self):
        scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"])
        text, cites = self._svc().ontology_block("고양이를 계속 키워도 될까요?", scr, allergen_groups=["cat"])
        assert "알레르겐 환경관리 일반 지식" in text and "이 환자의 검사 결과가 아님" in text
        assert "1단계" in text and "교과서" in text
        assert cites and all(c["kind"] == "allergen" and c["review_status"] == ENV_REVIEW_STATUS
                             and c["group_id"].startswith("env-measure:") for c in cites)
        assert "일반 질환 지식" not in text        # 질환 스냅샷은 걸리지 않았다

    def test_patient_specific_question_still_injects_nothing(self):
        scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"])
        text, cites = self._svc().ontology_block("제 집먼지진드기 수치가 얼마였죠?", scr, allergen_groups=["mite"])
        assert text == "" and cites == []

    def test_prompt_carries_environment_rules(self):
        p = self._svc()._system_prompt("CTX", "ko", None, "ONTO")
        assert "알레르겐 환경관리 일반 지식" in p
        assert "Never issue directives such as removing a pet" in p
        assert "never contains medication instructions" in p

    def test_patient_allergen_groups_from_relevance_result(self):
        class R:  # 최소 relevance_result 흉내
            assessments = [_assess("Dermatophagoides farinae", "mite"),
                           _assess("Birch pollen", "pollen_tree", ClinicalRelevance.SENSITIZED_ONLY)]
        assert ResultChatService.patient_allergen_groups(R()) == ["mite"]


# ---------------------------------------------------------------------------
# 리포트 근거 요약
# ---------------------------------------------------------------------------
class TestReportEvidence:
    def test_environment_evidence_block_only_for_relevant_inhalants(self):
        from services.report_service import ReportService
        md = ReportService._environment_evidence_md([_assess("Dermatophagoides farinae", "mite"),
                                                      _assess("Cat dander", "animal")])
        assert "환경관리 근거 요약" in md and "집먼지진드기" in md and "고양이" in md
        assert "전문의 검토 전" in md and "약을 바꾸라는 뜻은 없습니다" in md
        assert ReportService._environment_evidence_md([_assess("Shrimp", "food")]) == ""

    def test_cardnews_note_sits_inside_the_prevention_card(self):
        """카드뉴스는 새 카드를 만들지 않고 '생활 수칙' 카드 안에 근거 메모를 붙인다(덱 길이·순서 불변)."""
        from services.cardnews_service import CardNewsService, evidence_note_html, _esc
        note = evidence_note_html([_assess("Dermatophagoides farinae", "mite"), _assess("Cat dander", "animal")], _esc)
        assert "왜 이렇게 하나요" in note and "집먼지진드기" in note and "전문의 검토 전" in note
        assert evidence_note_html([_assess("Shrimp", "food")], _esc) == ""
        card = CardNewsService()._prevention_card([_assess("Dermatophagoides farinae", "mite")], None)
        assert card.count('class="section') == 1 and "왜 이렇게 하나요" in card


# ---------------------------------------------------------------------------
# 지식베이스·템플릿이 교과서 내용을 반영했는가
# ---------------------------------------------------------------------------
class TestKnowledgeBaseRefresh:
    def test_curated_entries_updated_and_linked_to_groups(self):
        from services.knowledge_service import get_knowledge_service
        ks = get_knowledge_service()
        mite = ks.get_backdata("Dermatophagoides farinae", "mite", "미국집먼지진드기")
        assert mite.get("environment_control_group") == "mite"
        assert any("한 가지 조치만으로는" in t for t in mite["avoidance_control_ko"])
        assert "10~35" in mite["biology_ko"]
        cat = ks.get_backdata("Cat dander", "animal", "고양이 비듬")
        assert "90~95%" in cat["biology_ko"] and any("내보내는" in t for t in cat["avoidance_control_ko"])

    def test_template_entries_carry_the_new_text(self):
        from services.knowledge_service import get_knowledge_service
        ks = get_knowledge_service()
        tmpl = ks.get_backdata("Tyrophagus putrescentiae", "mite", None)   # 레지스트리의 저장진드기(템플릿 적용)
        assert tmpl["source"].startswith("category_profile")
        assert any("공기청정기는 진드기에는 효과가 의문" in t for t in tmpl["avoidance_control_ko"])


# ---------------------------------------------------------------------------
# v1.2 — 최신판 교과서 제5장 '알레르기질환과 환경'(2026-10-13): 악화 요인·성분·기전·발생 배경
# ---------------------------------------------------------------------------
class TestChapter5Layer:
    def test_dataset_version_and_new_sources(self, data):
        assert data["version"] == "1.2" and data["compiled_on"] == "2026-10-13"
        ch5 = data["sources"]["kaaaci_textbook_ch5_env"]
        assert ch5["type"] == "textbook_ko" and ch5["pages"] == "77-88" and "송대진" in ch5["citation"]
        assert {"ipcc_ar6_2021", "hammer_2020_pm25", "strachan_1989", "nhis_korea_2014_2016_pm_asthma"} <= set(data["sources"])

    def test_pollutant_components_keep_textbook_thresholds(self, data):
        comps = data["aggravating_factors"]["outdoor_air_pollution"]["components"]
        assert set(comps) == {"pm", "asian_dust", "so2", "no2", "ozone"}
        so2 = {(t["value"], t["unit"]) for t in comps["so2"]["thresholds"]}
        no2 = {(t["value"], t["unit"]) for t in comps["no2"]["thresholds"]}
        assert {(0.5, "ppm"), (0.25, "ppm")} <= so2 and {(4.0, "ppm"), (0.4, "ppm"), (10, "µg/m³")} <= no2
        assert any("1.6%" in e and "17.9%" in e for e in comps["pm"]["evidence_ko"])
        assert any("3일" in e for e in comps["asian_dust"]["evidence_ko"])
        assert any("3.3배" in e for e in comps["ozone"]["evidence_ko"])
        src = set(data["sources"])
        for c in comps.values():
            assert c["sources"] and set(c["sources"]) <= src

    def test_new_factor_and_measures_follow_the_schema(self, data):
        af = data["aggravating_factors"]
        assert "indoor_air_pollution" in af
        ids = {m["id"] for f in af.values() for m in f["measures"]}
        assert {"smoke_ecigarette_not_substitute", "pollution_check_forecast", "ozone_limit_exercise",
                "dust_storm_reduce_exposure", "so2_nasal_breathing", "voc_ventilate_new_materials",
                "climate_thunderstorm_pollen_season"} <= ids
        # 기전·발생 배경은 환자 지시가 아니다 — 농장·반려동물·생우유를 '권하는' 문장이 없어야 한다
        bad = re.compile(r"(농장에서 키우|반려동물을 들이|생우유를 (드|먹이)세요)")
        for it in data["mechanisms"]["items"] + data["etiology_context"]["items"]:
            assert it["sources"] and set(it["sources"]) <= set(data["sources"]), it["id"]
            assert not bad.search(it.get("patient_ko", "")), it["id"]
        assert len(data["mechanisms"]["items"]) >= 6 and len(data["etiology_context"]["items"]) >= 4

    @pytest.mark.parametrize("q,expected", [
        ("미세먼지 심한 날 어떻게 해야 하나요?", ["outdoor_air_pollution"]),
        ("황사 올 때 외출해도 되나요", ["outdoor_air_pollution"]),
        ("전자담배는 괜찮나요", ["tobacco_smoke"]),
        ("새집으로 이사했는데 괜찮을까요?", ["indoor_air_pollution"]),
        ("가스레인지 쓸 때 주의할 점", ["indoor_combustion"]),
        ("공기청정기가 도움이 되나요", []),                 # 알레르겐군 질문이지 악화 요인 질문이 아니다
        ("제 미세먼지 수치가 얼마였죠?", []),               # 보고서 값 질문
    ])
    def test_factors_for_question(self, env, q, expected):
        assert env.factors_for_question(q) == expected

    def test_factor_summary_ranks_question_keywords_first(self, env):
        s = env.factor_summary("outdoor_air_pollution", "황사 올 때 외출해도 되나요")
        assert [c["label_ko"] for c in s["components"]][1] == "황사"
        assert "dust_storm_reduce_exposure" in [m["id"] for m in s["measures"]][:2]
        assert env.mechanisms() and env.etiology_context()

    def test_graph_has_pollutant_mechanism_and_etiology_nodes_joined_to_diseases(self, onto):
        q = f"""
        PREFIX allergy: <{NS}>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?p ?marker ?value ?mech ?d WHERE {{
          ?f allergy:query "outdoor_air_pollution" ; a allergy:AggravatingFactor ;
             allergy:hasComponent ?pp ; allergy:hasMechanism ?mm ; allergy:aggravates ?dd .
          ?pp rdfs:label ?p ; allergy:hasThreshold ?t . ?t allergy:marker ?marker ; allergy:value ?value .
          ?mm rdfs:label ?mech . ?dd rdfs:label ?d }}
        """
        rows = onto.sparql(q, limit=200)["rows"]
        assert rows
        assert {r["d"] for r in rows} >= {"Asthma", "Allergic rhinitis", "Atopic dermatitis"}
        assert any(r["marker"].startswith("SO₂") and float(r["value"]) == 0.5 for r in rows)
        e = onto.sparql(f"PREFIX allergy: <{NS}> SELECT (COUNT(?e) AS ?n) WHERE {{ ?e a allergy:EtiologyFactor }}")["rows"]
        assert int(e[0]["n"]) >= 4
        for p in ("has_component", "has_mechanism", "has_etiology_context"):
            assert p in PREDICATE_LABEL_KO

    def test_retrieve_adds_factor_block_and_mechanisms_only_when_asked_why(self, onto):
        r = onto.retrieve("미세먼지 심한 날 어떻게 해야 하나요?", [])
        assert [b["topic"] for b in r["factor_topics"]] == ["outdoor_air_pollution"]
        assert r["allergen_topics"] == [] and r["mechanisms"] == []
        assert any(c["label"].startswith("미세먼지") and c["thresholds"] == [] for c in r["factor_topics"][0]["components"])
        assert all(f["kind"] == "factor" and f["review_status"] == ENV_REVIEW_STATUS for f in r["factor_topics"][0]["facts"])
        r = onto.retrieve("왜 미세먼지가 천식에 안 좋은가요?", [])
        assert r["factor_topics"] and [m["id"] for m in r["mechanisms"]][0] == "ros_oxidative_stress"
        assert onto.retrieve("제 미세먼지 수치가 얼마였죠?", [])["factor_topics"] == []

    def test_chat_injects_factor_block_without_patient_groups(self):
        svc = ResultChatService(api_key="")
        text, cites = svc.ontology_block("황사 올 때 외출해도 되나요?", ScreeningProfile(allergic_diseases=["asthma"]))
        assert "(악화 요인: 실외 대기오염" in text and "황사" in text and "역치 SO₂" in text
        assert cites and all(c["kind"] == "factor" for c in cites)
        assert "악화 요인" in svc.ontology_block("왜 미세먼지가 천식에 안 좋은가요?", None)[0]
        assert "기전(왜 그런가)" in svc.ontology_block("왜 미세먼지가 천식에 안 좋은가요?", None)[0]
        assert svc.ontology_block("제 미세먼지 수치가 얼마였죠?", None) == ("", [])

    def test_no_medication_directives_in_new_factor_text(self, data):
        bad = re.compile(r"(용량을? (두 배|늘리|줄이)|스테로이드를 (쓰|드|복용하)세요|약을 (늘리|줄이|끊으)세요)")
        for f in data["aggravating_factors"].values():
            assert not bad.search(f["text_ko"])
            for c in (f.get("components") or {}).values():
                assert not bad.search(c["text_ko"])


class TestEditionTracking:
    """2026-10-13: 최신판 5장은 이전 판 '환경관리 및 회피요법' 장의 후속이다. 이전 판에만 기대는 조치를 표시한다."""

    def test_every_measure_has_edition_support_and_counts_match(self, data):
        et = data["edition_tracking"]
        tags = [m["edition_support"] for o in list(data["allergen_groups"].values()) + list(data["aggravating_factors"].values())
                for m in o["measures"]]
        assert set(tags) <= set(et["labels_ko"]) and all(tags)
        assert sum(et["counts"].values()) == len(tags) and et["counts"]["previous_ko_only"] == len(et["previous_ko_only"]) == 16
        assert data["sources"]["kaaaci_textbook_env"]["edition_status"] == "superseded"
        assert data["sources"]["kaaaci_textbook_ch5_env"]["edition_status"] == "current"

    def test_measures_with_middleton_support_are_not_flagged(self, data):
        ids = {x["id"] for x in data["edition_tracking"]["previous_ko_only"]}
        assert {"pollen_mask_goggles", "mite_pillow_choice", "omold_shower_change"} <= ids
        assert {"mite_encasing", "cat_remove_pet", "mite_comprehensive_regimen"}.isdisjoint(ids)

    def test_service_and_graph_expose_edition_support(self, env, onto):
        m = next(x for x in env.measures("pollen") if x["id"] == "pollen_mask_goggles")
        assert m["edition_support"] == "previous_ko_only" and "최신판 미수록" in m["edition_support_ko"]
        assert env.edition_tracking()["checked_on"] == "2026-10-13"
        rows = onto.sparql(f'PREFIX allergy: <{NS}> SELECT (COUNT(?m) AS ?n) WHERE {{ ?m a allergy:ControlMeasure ; allergy:editionSupport "previous_ko_only" }}')["rows"]
        assert int(rows[0]["n"]) == 16
