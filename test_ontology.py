"""온톨로지 RAG — 그래프 적재·SPARQL 가드·검색 게이팅·챗봇 주입 계약.

핵심 불변식은 두 가지다.
  ① 이 지식은 **질환 일반 설명 전용**이다. 환자 개별 사실(어떤 항원이 양성인지, 수치가 얼마인지)은
     언제나 검사 보고서에서 온다.
  ② 스냅샷의 모든 임상 주장은 검토 전(candidate)이고 출처가 백과사전이다. 그래서 검토 상태·출처를
     잃어버리면 안 되고, 확정 사실처럼 쓰면 안 된다.
"""
import pytest

from models.schemas import ScreeningProfile
from services.ontology_service import (NS, PREDICATE_LABEL_KO, SENSITIVE_PREDICATES,
                                       OntologyService, _clean_quote, get_ontology_service)
from services.result_chat_service import ResultChatService


@pytest.fixture(scope="module")
def svc() -> OntologyService:
    return get_ontology_service()


class TestSnapshotLoads:
    def test_graph_is_available(self, svc):
        assert svc.available, "온톨로지 스냅샷을 불러오지 못했습니다."

    def test_five_topics(self, svc):
        qs = {t["query"] for t in svc.topics()}
        assert qs == {"allergy", "asthma", "allergic rhinitis", "atopic dermatitis", "urticaria"}

    def test_missing_file_degrades_quietly(self, tmp_path):
        """스냅샷이 없어도 챗봇은 계속 돌아야 한다 — 일반지식만 빠진다."""
        s = OntologyService(snapshot_path=tmp_path / "nope.json")
        assert not s.available
        assert s.retrieve("천식이 뭔가요?", []) == {"available": False, "topics": [],
                                                "reason": s._load_error}
        assert s.sparql("SELECT ?s WHERE { ?s ?p ?o }")["ok"] is False

    def test_every_predicate_has_a_korean_label(self, svc):
        """라벨이 없으면 'has_duration' 같은 내부 이름이 사용자에게 그대로 보인다."""
        svc._ensure()
        preds = {i["predicate"] for t in svc._topics.values()
                 for i in t.get("clinical_context", {}).get("items", [])}
        assert not (preds - set(PREDICATE_LABEL_KO)), f"라벨 없는 술어: {preds - set(PREDICATE_LABEL_KO)}"


class TestSparql:
    def test_select_returns_rows(self, svc):
        q = f"""PREFIX allergy: <{NS}>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?label WHERE {{
          ?t allergy:query "asthma" ; allergy:hasExpression ?g .
          ?g allergy:predicate "has_differential" ; rdfs:label ?label . }}"""
        out = svc.sparql(q, limit=10)
        assert out["ok"] and out["type"] == "SELECT"
        assert "COPD" in {r["label"] for r in out["rows"]}

    def test_ask_works(self, svc):
        out = svc.sparql(f'PREFIX allergy: <{NS}> ASK {{ ?t allergy:query "urticaria" }}')
        assert out["ok"] and out["boolean"] is True

    @pytest.mark.parametrize("q", [
        "DELETE WHERE { ?s ?p ?o }",
        "INSERT DATA { <a:b> <a:c> <a:d> }",
        "DROP ALL",
        "CLEAR GRAPH <g>",
        "SELECT ?s WHERE { SERVICE <http://evil.example/sparql> { ?s ?p ?o } }",
    ])
    def test_write_and_remote_queries_are_refused(self, svc, q):
        out = svc.sparql(q)
        assert out["ok"] is False

    def test_limit_is_capped(self, svc):
        out = svc.sparql("SELECT ?s WHERE { ?s ?p ?o }", limit=100000)
        assert len(out["rows"]) <= 500


class TestRetrievalGating:
    """환자 개별 질문에 일반지식이 딸려 나오면, 쓰지도 않은 자료가 출처로 표시된다."""

    @pytest.mark.parametrize("q", [
        "제 집먼지진드기 수치가 얼마였죠?",
        "제 결과에서 뭐가 제일 조심할 것인가요?",
        "고양이를 계속 키워도 될까요?",
    ])
    def test_patient_specific_questions_retrieve_nothing(self, svc, q):
        assert svc.relevant_topics(q, ["allergic_rhinitis", "asthma"]) == []

    @pytest.mark.parametrize("q,expected", [
        ("알레르기 비염이 어떤 병인가요?", "allergic rhinitis"),
        ("천식이랑 어떻게 구분해요?", "asthma"),
        ("What is atopic dermatitis?", "atopic dermatitis"),
        ("荨麻疹是什么", "urticaria"),
    ])
    def test_general_questions_pick_the_right_topic(self, svc, q, expected):
        assert expected in svc.relevant_topics(q, [])

    def test_diseases_only_narrow_an_already_general_question(self, svc):
        """기저질환은 주제를 좁히기만 한다. 혼자서 검색을 켜지 않는다."""
        assert svc.relevant_topics("증상이 왜 생기나요?", ["asthma"]) == ["asthma"]
        assert svc.relevant_topics("제 수치 알려주세요", ["asthma"]) == []


class TestFacts:
    def test_facts_are_scoped_to_their_topic(self, svc):
        """주제 필터가 새면 천식 질문에 아토피 감별진단이 붙는다(실제로 겪은 회귀)."""
        labels = {f["label"] for f in svc.facts("asthma", ["has_differential"], limit=10)}
        assert "COPD" in labels
        assert "psoriasis" not in labels and "seborrheic dermatitis" not in labels

    def test_every_fact_carries_citation_fields(self, svc):
        for f in svc.facts("allergic rhinitis", None, limit=5):
            assert f["review_status"] and f["group_id"].startswith("clinical-expression-group")
            assert f["topic_url"].startswith("https://")
            assert f["predicate_ko"] != f["predicate"] or f["predicate"] in PREDICATE_LABEL_KO

    def test_all_claims_are_candidate_not_accepted(self, svc):
        """스냅샷에 accepted 는 0건이다. 승인된 것처럼 다루면 안 된다."""
        for topic in ("asthma", "allergic rhinitis", "atopic dermatitis", "urticaria"):
            assert all(f["review_status"] == "candidate" for f in svc.facts(topic, None, limit=20))

    def test_mortality_is_kept_out_of_default_retrieval(self, svc):
        r = svc.retrieve("천식으로 죽기도 하나요?", ["asthma"])
        got = {f["predicate"] for b in r["topics"] for f in b["facts"]}
        assert not (got & SENSITIVE_PREDICATES)
        assert svc.facts("asthma", ["has_mortality"], limit=5), "SPARQL 경로로는 조회 가능해야 한다"

    def test_treatment_can_be_excluded(self, svc):
        r = svc.retrieve("비염은 무슨 약으로 치료해요?", [], include_treatment=False)
        got = {f["predicate"] for b in r["topics"] for f in b["facts"]}
        assert "has_medication" not in got and "has_treatment" not in got


class TestChatInjection:
    def _svc(self):
        return ResultChatService(api_key="")

    def test_block_has_citations_and_candidate_warning(self):
        scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"])
        text, cites = self._svc().ontology_block("알레르기 비염이 어떤 병인가요?", scr)
        assert "일반 질환 지식" in text and "이 환자의 검사 결과가 아님" in text
        assert "candidate" in text
        assert cites and all(c["group_id"] and c["url"] and c["review_status"] for c in cites)

    def test_patient_specific_question_injects_nothing(self):
        scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"])
        text, cites = self._svc().ontology_block("제 집먼지진드기 수치가 얼마였죠?", scr)
        assert text == "" and cites == []

    def test_prompt_separates_general_knowledge_from_the_report(self):
        p = self._svc()._system_prompt("REPORT-CTX", "ko", None, "ONTO-BLOCK")
        assert "ONTO-BLOCK" in p and "REPORT-CTX" in p
        assert "It is NOT about this patient" in p
        assert "the report wins" in p
        assert "never print the internal IDs" in p

    def test_prompt_without_ontology_has_no_dangling_rules(self):
        p = self._svc()._system_prompt("REPORT-CTX", "ko", None, "")
        assert "GENERAL DISEASE KNOWLEDGE" not in p

    def test_treatment_items_carry_a_no_recommendation_rule(self):
        p = self._svc()._system_prompt("CTX", "ko", None, "ONTO")
        assert "never recommend one" in p


class TestGuideRequirements:
    """`docs/20260916-allergy-chatbot/chatbot-integration-guide.md` 가 명시한 요구사항.

    가이드는 답변에 관계명·claim ID·evidence ID·원문 URL 을 표시하고, 임상 관계의 검토 상태와
    용어 매핑의 검토 상태를 **따로** 밝히라고 한다. 또 자료가 없는 관계를 의학적 부재로
    단정하지 말라고 한다.
    """

    def test_facts_carry_claim_and_evidence_ids_with_quote(self, svc):
        facts = svc.facts("asthma", ["evaluated_with"], limit=3)
        assert facts
        f = facts[0]
        assert f["claim_id"].startswith("clinical-claim:")
        assert f["evidence_id"].startswith("evidence:")
        assert f["source_url"].startswith("https://en.wikipedia.org/")
        assert f["quote"], "라벨만으로는 맥락을 알 수 없다 — 원문 인용이 있어야 한다"

    def test_quotes_are_stripped_of_wikitext(self):
        raw = ("'''" + "Based on symptoms" + "'''" +
               ", [[spirometry]]<ref name=\"x\" /> and tests {{cn}}")
        out = _clean_quote(raw)
        assert "[[" not in out and "<ref" not in out and "{{" not in out
        assert "'''" not in out
        assert "spirometry" in out and "tests" in out

    def test_mapping_review_is_separate_from_claim_review(self, svc):
        """asthma 는 매핑이 승인 5건이지만 임상 주장은 여전히 전부 candidate 다."""
        asthma = svc.topic_status("asthma")
        assert asthma["mapping_state"] == "accepted" and asthma["mapping_accepted"] == 5
        assert asthma["claim_review"] == "candidate"
        rhinitis = svc.topic_status("allergic rhinitis")
        assert rhinitis["mapping_state"] == "candidate" and rhinitis["mapping_accepted"] == 0

    def test_uncollected_topic_is_flagged_not_silently_empty(self, svc):
        """Allergy 는 본문·관계가 미수집이다. 조용히 비우면 '없다'로 읽힌다."""
        st = svc.topic_status("allergy")
        assert st["data_collected"] is False and st["claim_review"] == "none_collected"
        r = svc.retrieve("알레르기가 뭔가요?", [])
        assert any(u["topic"] == "allergy" for u in r["uncollected"])

    def test_definition_falls_back_to_hpo_and_symp(self, svc):
        """DO 만 보면 allergy 처럼 DO 가 없는 주제에서 정의를 통째로 놓친다."""
        d = svc.definition("allergy")
        assert d and d["system"] == "HPO" and d["code"].startswith("HP:")
        assert svc.definition("asthma")["system"] == "DO"

    def test_snomed_absence_is_recorded(self, svc):
        """가이드: SNOMED CT 판본은 아직 반입되지 않았다."""
        assert svc.topic_status("asthma")["snomed_available"] is False
        assert svc.get_terminology("asthma")["snomed"].get("available") is False


class TestReadOnlyTools:
    """가이드가 제안한 네 가지 읽기 전용 도구."""

    def test_search_topics_resolves_aliases_with_a_caveat(self, svc):
        hits = svc.search_topics("hay fever")
        assert [h["topic"] for h in hits] == ["allergic rhinitis"]
        assert "임상 하위유형" in hits[0]["alias_note"], "별칭=동일 임상개념 아님을 알려야 한다"

    def test_search_topics_handles_urticaria_hives(self, svc):
        assert [h["topic"] for h in svc.search_topics("hives")] == ["urticaria"]
        assert svc.search_topics("") == []

    def test_get_topic_context(self, svc):
        out = svc.get_topic_context("asthma", "has_differential", limit=5)
        assert out["status"]["expression_groups"] == 68
        assert "COPD" in {f["label"] for f in out["facts"]}
        assert svc.get_topic_context("nope")["error"]

    def test_get_evidence_returns_source_and_hash(self, svc):
        f = svc.facts("asthma", ["evaluated_with"], limit=1)[0]
        ev = svc.get_evidence(f["evidence_id"])
        assert ev["source_url"].startswith("https://")
        assert ev["text_sha256"] and ev["clean_text"]
        assert svc.get_evidence("evidence:nonexistent").get("error")

    def test_get_terminology_preserves_system_code_release(self, svc):
        out = svc.get_terminology("asthma")
        codes = {(t["system"], t["code"]) for t in out["terms"]}
        assert ("DO", "DOID:2841") in codes
        assert all(t["release"] for t in out["terms"] if t["code"])
        assert out["note"] and out["constraints"]


class TestChatBlockCarriesProvenance:
    def _block(self, q, diseases):
        from models.schemas import ScreeningProfile
        from services.result_chat_service import ResultChatService
        return ResultChatService(api_key="").ontology_block(
            q, ScreeningProfile(allergic_diseases=diseases))

    def test_block_includes_quote_and_both_review_states(self):
        text, cites = self._block("천식은 어떻게 진단하나요?", ["asthma"])
        assert "원문:" in text
        assert "임상 관계 검토: candidate" in text and "용어 매핑 검토: accepted" in text
        assert cites[0]["claim_id"] and cites[0]["evidence_id"] and cites[0]["quote"]

    def test_block_states_uncollected_rather_than_absent(self):
        text, _ = self._block("알레르기가 뭔가요?", [])
        assert "수집된 임상 관계 없음" in text
        assert "의학적으로 없다" in text

    def test_prompt_has_guide_rules(self):
        from services.result_chat_service import ResultChatService
        p = ResultChatService(api_key="")._system_prompt("CTX", "ko", None, "ONTO")
        assert "Missing data is NOT medical absence" in p
        assert "imply the clinical claims were approved" in p
        assert "Do not merge them" in p
        # 규칙 24 는 "내 지식이라고 표시하면 덧붙여도 된다"에서 "덧붙이지 말라"로 바뀌었다
        # (astra 검증: 표시만 하면 근거 범위를 우회할 수 있었다)
        assert "Do not add clinical content that is in neither" in p
