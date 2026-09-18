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
                                       OntologyService, get_ontology_service)
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
