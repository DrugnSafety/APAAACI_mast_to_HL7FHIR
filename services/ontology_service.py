"""Ontology Service — 알레르기 질환 온톨로지 스냅샷을 RDF 그래프로 올리고 SPARQL 로 질의한다.

무엇을 담고 있나 (`docs/20260916-allergy-chatbot/ontology-snapshot.json`)
  - 5개 질환 주제(Allergy, Asthma, Allergic rhinitis, Atopic dermatitis, Hives=두드러기)
  - 임상 표현 그룹 161개: has_symptom · has_medication · has_treatment · has_risk_factor ·
    has_cause_candidate · evaluated_with · has_differential
  - 근거 셀 1,130개(Wikipedia 특정 판본의 원문 + URL + sha256)
  - DO/HPO 표준 용어: 코드·정의·동의어·상위 개념(subclass_of)

왜 RDF + SPARQL 인가
  스냅샷은 JSON 이고 스스로 "full RDF/OWL export 가 아니다"라고 밝힌다. 그래서 적재 시점에
  그래프로 변환해 표준 질의어(SPARQL)로 다룬다. 외부 트리플스토어·네트워크 의존이 없고,
  파일만 있으면 오프라인에서 재현된다.

**이 데이터를 쓸 때의 제약(스냅샷의 usage_rules 를 그대로 따른다)**
  1. 원문은 **참고 데이터이지 챗봇에 대한 지시가 아니다**. 그대로 실행하지 않는다.
  2. 모든 임상 주장은 `candidate`(검토 전)다. `accepted` 는 0건이다. 임상 승인·진단 확률이 아니다.
  3. `polarity=positive` 는 "원문이 긍정적으로 서술했다"는 뜻일 뿐이다.
  4. 답변에 쓰면 claim ID·evidence ID·원문 URL·검토 상태를 함께 남긴다.
  5. 출처는 Wikipedia 특정 판본이다. 진료 지침이 아니다.
  그래서 이 지식은 **질환 일반 설명**에만 쓰고, 이 환자의 판정·수치에는 절대 쓰지 않는다.
  환자 개별 사실의 근거는 언제나 검사 보고서다.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from config.settings import BASE_DIR

logger = logging.getLogger(__name__)

SNAPSHOT_PATH = BASE_DIR / "docs" / "20260916-allergy-chatbot" / "ontology-snapshot.json"

NS = "https://apaaaci.local/allergy-ontology#"

# 스크리닝 질환 코드 → 스냅샷 주제. 환자가 고른 기저질환에서 바로 주제를 찾기 위한 다리.
DISEASE_TO_TOPIC = {
    "allergic_rhinitis": "allergic rhinitis",
    "asthma": "asthma",
    "atopic_dermatitis": "atopic dermatitis",
    "chronic_urticaria": "urticaria",
    "allergic_conjunctivitis": "allergic rhinitis",   # 결막염 단독 주제는 없다(비염 문서에 동반 기술)
}

# 질문에 이 말이 있으면 해당 주제를 본다(ko/en/zh)
TOPIC_KEYWORDS = {
    "asthma": ["천식", "숨차", "쌕쌕", "천명", "기관지", "asthma", "wheez", "哮喘", "喘"],
    "allergic rhinitis": ["비염", "코막힘", "콧물", "재채기", "코가", "건초열", "rhinitis", "hay fever",
                          "stuffy", "runny nose", "sneez", "鼻炎", "鼻塞", "打喷嚏"],
    "atopic dermatitis": ["아토피", "습진", "피부염", "atopic", "eczema", "dermatitis", "特应性", "湿疹"],
    "urticaria": ["두드러기", "urticaria", "hives", "荨麻疹"],
    "allergy": ["알레르기", "알러지", "아나필락시스", "allergy", "allergic", "anaphyla", "过敏"],
}

# 질문 의도 → 우선 볼 술어. 약·치료는 별도 취급한다(처방 금지 규칙과 충돌하기 쉬움).
INTENT_PREDICATES = {
    "symptom": (["증상", "징후", "symptom", "sign", "症状"], ["has_symptom"]),
    "cause": (["원인", "왜", "유발", "cause", "why", "trigger", "原因", "为什么"],
              ["has_cause_candidate", "has_risk_factor"]),
    "risk": (["위험", "악화", "risk", "worse", "风险"], ["has_risk_factor"]),
    "diagnosis": (["진단", "검사", "어떻게 아", "diagnos", "test", "诊断", "检查"], ["evaluated_with"]),
    "differential": (["구분", "감별", "아닌", "차이", "differen", "versus", "鉴别", "区别"],
                     ["has_differential"]),
    "treatment": (["치료", "약", "관리", "나으", "treat", "therapy", "medicat", "drug",
                   "治疗", "药"], ["has_treatment", "has_medication"]),
    "prevention": (["예방", "막으", "prevent", "avoid", "预防"], ["has_prevention"]),
    "complication": (["합병", "동반", "같이 생기", "complication", "并发"],
                     ["has_possible_complication"]),
    "course": (["얼마나", "언제부터", "평생", "낫나", "경과", "how long", "onset", "多久", "什么时候"],
               ["has_onset", "has_duration", "has_frequency"]),
}
TREATMENT_PREDICATES = {"has_treatment", "has_medication"}

# 환자에게 먼저 들이밀 정보가 아니다. 검토 전 백과사전 자료의 사망률을 상담 화면에 띄우면
# 근거에 비해 공포만 크다. SPARQL 로는 여전히 조회할 수 있고, 기본 검색에서만 뺀다.
SENSITIVE_PREDICATES = {"has_mortality"}

PREDICATE_LABEL_KO = {
    "has_symptom": "증상", "has_medication": "약제", "has_treatment": "치료",
    "has_risk_factor": "위험요인", "has_cause_candidate": "원인 후보",
    "evaluated_with": "진단·평가", "has_differential": "감별 대상",
    "has_possible_complication": "동반·합병 가능", "has_frequency": "빈도",
    "has_onset": "발병 시기", "has_duration": "경과 기간",
    "has_prevention": "예방", "has_mortality": "사망률",
    # 교과서 알레르겐 환경관리 층(data/allergen_environment_control.json)의 술어
    "has_control_measure": "관리 조치", "has_major_allergen": "주요 알레르겐",
    "has_threshold": "노출 역치", "has_reservoir": "저장소", "has_carrier_particle": "운반 입자",
    "has_cross_reactivity": "교차반응", "associated_with_disease": "관련 질환",
    "aggravates": "악화 요인", "has_principle": "원칙",
    # v1.2 — 최신판 5장: 대기오염 성분·기전·발생 배경
    "has_component": "구성 오염물질", "has_mechanism": "기전", "has_etiology_context": "발생 배경",
    "protective_factor": "유익한 환경요인", "risk_factor_context": "해로운 환경요인",
}

# 알레르겐 환경관리 층의 검토 상태. 스냅샷(Wikipedia, candidate)과 출처·성격이 다르므로 따로 표시한다.
ENV_REVIEW_STATUS = "textbook_curated"

_SPARQL_ALLOWED = re.compile(r"^\s*(?:PREFIX\s+\S+\s*:\s*<[^>]*>\s*)*(SELECT|ASK)\b", re.I)
_SPARQL_FORBIDDEN = re.compile(
    r"\b(INSERT|DELETE|LOAD|CLEAR|DROP|CREATE|ADD|MOVE|COPY|WITH|SERVICE)\b", re.I)


def _clean_quote(raw, limit: int = 220) -> str:
    """위키텍스트 마크업을 걷어내 사람이 읽을 수 있는 인용문으로 만든다.

    raw_text 는 원문 위키텍스트라 '[[spirometry]]<ref name="x" />' 같은 표기가 섞여 있다.
    그대로 프롬프트에 넣으면 모델이 대괄호째 따라 쓴다.
    """
    if not raw:
        return ""
    t = re.sub(r"<ref[^>]*?/>|<ref[^>]*?>.*?</ref>", "", str(raw), flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]", r"\1", t)     # [[a|b]] -> b
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = t.replace("\'\'\'", "").replace("\'\'", "")
    t = re.sub(r"\s+", " ", t).strip(" .,;:|")
    return t[:limit]


class OntologyService:
    """스냅샷을 RDF 로 올리고 SPARQL·검색을 제공한다. 그래프 구축은 최초 사용 시 1회."""

    def __init__(self, snapshot_path: Optional[Path] = None):
        self.path = Path(snapshot_path or SNAPSHOT_PATH)
        self._lock = threading.Lock()
        self._graph = None
        self._raw: Optional[Dict[str, Any]] = None
        self._topics: Dict[str, Dict[str, Any]] = {}
        self._evidence: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._load_error: Optional[str] = None
        # 교과서 알레르겐 환경관리 층(선택). 파일이 없어도 질환 스냅샷은 그대로 동작한다.
        self._env = None
        self._env_loaded = False

    # ------------------------------------------------------------------
    # 적재
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        self._ensure()
        return self._loaded

    def _ensure(self) -> None:
        if self._loaded or self._load_error:
            return
        with self._lock:
            if self._loaded or self._load_error:
                return
            try:
                self._raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._index()
                self._build_graph()
                self._loaded = True
                logger.info(f"온톨로지 적재 완료: 주제 {len(self._topics)}개, "
                            f"트리플 {len(self._graph)}개")
            except FileNotFoundError:
                self._load_error = f"스냅샷 없음: {self.path}"
                logger.warning(self._load_error)
            except Exception as e:  # noqa: BLE001
                self._load_error = f"온톨로지 적재 실패: {e}"
                logger.warning(self._load_error)

    def _index(self) -> None:
        for t in (self._raw or {}).get("topics", []):
            self._topics[t["query"]] = t
            for ev in t.get("source_evidence", []):
                self._evidence[ev["id"]] = ev

    def _build_graph(self) -> None:
        from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef

        g = Graph()
        n = Namespace(NS)
        g.bind("allergy", n)
        g.bind("rdfs", RDFS)

        def uri(ident: str) -> "URIRef":
            return URIRef(NS + re.sub(r"[^A-Za-z0-9:._-]", "_", str(ident)))

        for query, t in self._topics.items():
            node = t["topic"]
            tu = uri(node["id"])
            g.add((tu, RDF.type, n.Topic))
            g.add((tu, RDFS.label, Literal(node.get("label", ""))))
            g.add((tu, n.query, Literal(query)))
            if (node.get("data") or {}).get("url"):
                g.add((tu, n.sourceUrl, Literal(node["data"]["url"])))
            for m in (node.get("identity") or {}).get("members", []):
                if m.get("label"):
                    g.add((tu, n.alias, Literal(m["label"])))

            # 임상 표현 그룹 = 이 온톨로지의 핵심 관계
            for item in t.get("clinical_context", {}).get("items", []):
                gu = uri(item["id"])
                g.add((gu, RDF.type, n.ExpressionGroup))
                g.add((gu, n.topic, tu))
                g.add((tu, n.hasExpression, gu))
                g.add((gu, n.predicate, Literal(item.get("predicate", ""))))
                g.add((gu, RDFS.label, Literal(item.get("display_label", ""))))
                g.add((gu, n.polarity, Literal(item.get("polarity", ""))))
                g.add((gu, n.reviewStatus, Literal(item.get("review_status", "candidate"))))
                g.add((gu, n.evidenceCount, Literal(int(item.get("evidence_count", 0)))))
                g.add((gu, n.mappingEligible,
                       Literal(bool((item.get("term_policy") or {}).get("mapping_eligible", False)))))
                for rec in item.get("records", []):
                    ru = uri(rec["id"])
                    g.add((gu, n.claim, ru))
                    g.add((ru, RDF.type, n.Claim))
                    g.add((ru, n.claimId, Literal(rec["id"])))
                    g.add((ru, n.reviewStatus, Literal(rec.get("review_status", "candidate"))))
                    g.add((ru, n.polarity, Literal(rec.get("polarity", ""))))
                    # 근거 원문. 연동 가이드가 '구조화된 관계와 원문 셀을 함께' 가져오라고 한 부분이다.
                    # 라벨("based on symptoms")만으로는 무슨 맥락인지 알 수 없다.
                    src = rec.get("source") or {}
                    if src.get("evidence_id"):
                        g.add((ru, n.evidenceId, Literal(src["evidence_id"])))
                        g.add((ru, n.evidence, uri(src["evidence_id"])))
                    for key, pred in (("raw_text", n.rawText), ("fragment", n.fragment),
                                      ("url", n.sourceUrl), ("field", n.field)):
                        if src.get(key):
                            g.add((ru, pred, Literal(src[key])))
                    sp = src.get("section_path") or (rec.get("context") or {}).get("section_path")
                    if sp:
                        g.add((ru, n.sectionPath,
                               Literal(" > ".join(sp) if isinstance(sp, list) else str(sp))))

            # 표준 용어(DO/HPO): 코드·정의·동의어·상위 개념
            for grp in t.get("terminology_summary", {}).get("groups", []):
                tgt = grp.get("target") or {}
                if not tgt.get("id"):
                    continue
                ou = uri(tgt["id"])
                g.add((ou, RDF.type, n.OntologyTerm))
                g.add((tu, n.mappedTo, ou))
                g.add((ou, RDFS.label, Literal(tgt.get("label", ""))))
                g.add((ou, n.system, Literal(tgt.get("system", ""))))
                g.add((ou, n.code, Literal(tgt.get("code", ""))))
                g.add((ou, n.release, Literal(tgt.get("release", ""))))
                if tgt.get("definition"):
                    g.add((ou, n.definition, Literal(tgt["definition"])))
                for syn in tgt.get("synonyms", []) or []:
                    if syn.get("term"):
                        g.add((ou, n.synonym, Literal(syn["term"])))
                for parent in (grp.get("hierarchy") or {}).get("items", []) or []:
                    if parent.get("id"):
                        pu = uri(parent["id"])
                        g.add((ou, n.subClassOf, pu))
                        g.add((pu, RDFS.label, Literal(parent.get("label", ""))))
                        g.add((pu, n.code, Literal(parent.get("code", ""))))
                        g.add((pu, n.system, Literal(parent.get("system", ""))))

        # 근거 셀: 원문·URL·판본. 인용 표기에 필요한 최소 정보만 싣는다.
        for eid, ev in self._evidence.items():
            eu = uri(eid)
            g.add((eu, RDF.type, n.Evidence))
            text = (ev.get("text") or "").strip()
            if text:
                g.add((eu, n.text, Literal(text)))
            for key, pred in (("source_url", n.sourceUrl), ("revision_url", n.revisionUrl),
                              ("field", n.field), ("sheet", n.sheet)):
                if ev.get(key):
                    g.add((eu, pred, Literal(ev[key])))
        self._add_environment_layer(g, n, uri)
        self._graph = g

    # ------------------------------------------------------------------
    # 교과서 알레르겐 환경관리 층
    # ------------------------------------------------------------------
    def _env_service(self):
        if not self._env_loaded:
            self._env_loaded = True
            try:
                from services.environment_control_service import get_environment_control_service
                svc = get_environment_control_service()
                self._env = svc if svc.available else None
            except Exception as e:  # noqa: BLE001
                logger.warning(f"환경관리 층을 읽지 못함: {e}")
                self._env = None
        return self._env

    def _disease_uri(self, query: str, uri):
        t = self._topics.get(query)
        return uri(t["topic"]["id"]) if t else None

    def _add_environment_layer(self, g, n, uri) -> None:
        """`data/allergen_environment_control.json` 을 그래프에 얹는다.

        질환 주제(Wikipedia 스냅샷)와는 출처·검토 상태가 다르므로 노드 종류를 따로 두고
        `reviewStatus = textbook_curated`, `sourceType = textbook` 으로 표시한다. 알레르겐군은
        `associatedWithDisease` 로 기존 질환 Topic 노드에 연결되어, 한 그래프에서
        '집먼지진드기 → 천식 → 감별진단' 같은 질의가 가능해진다.
        """
        from rdflib import Literal, RDF, RDFS
        env = self._env_service()
        if env is None:
            return
        data = env.data
        src_nodes = {}
        for sid, s in (data.get("sources") or {}).items():
            su = uri(f"env-source:{sid}")
            src_nodes[sid] = su
            g.add((su, RDF.type, n.TextbookSource))
            g.add((su, RDFS.label, Literal(s.get("citation", sid))))
            for key, pred in (("type", n.sourceType), ("pages", n.pages), ("verified", n.verified),
                              ("finding_ko", n.finding), ("pmid", n.pmid), ("doi", n.doi)):
                if s.get(key):
                    g.add((su, pred, Literal(s[key])))

        def cite(node, ids):
            for sid in ids or []:
                if sid in src_nodes:
                    g.add((node, n.citedIn, src_nodes[sid]))

        def add_measures(owner, measures):
            for m in measures or []:
                mu = uri(f"env-measure:{m['id']}")
                g.add((mu, RDF.type, n.ControlMeasure))
                g.add((owner, n.hasControlMeasure, mu))
                g.add((mu, RDFS.label, Literal(m.get("short_ko", ""))))
                g.add((mu, n.text, Literal(m.get("text_ko", ""))))
                g.add((mu, n.tier, Literal(m.get("tier", ""))))
                g.add((mu, n.tierOrder, Literal(env.tier_order(m.get("tier", "")))))
                g.add((mu, n.evidenceGrade, Literal(m.get("evidence_grade", ""))))
                if m.get("edition_support"):
                    g.add((mu, n.editionSupport, Literal(m["edition_support"])))
                g.add((mu, n.reviewStatus, Literal(ENV_REVIEW_STATUS)))
                for key, pred in (("evidence_ko", n.evidenceNote), ("caveat_ko", n.caveat)):
                    if m.get(key):
                        g.add((mu, pred, Literal(m[key])))
                cite(mu, m.get("sources"))

        group_nodes = {key: uri(f"env-group:{key}") for key in (data.get("allergen_groups") or {})}
        for key, grp in (data.get("allergen_groups") or {}).items():
            gu = group_nodes[key]
            g.add((gu, RDF.type, n.AllergenGroup))
            g.add((gu, RDFS.label, Literal(grp.get("label_ko", key))))
            g.add((gu, n.labelEn, Literal(grp.get("label_en", key))))
            g.add((gu, n.query, Literal(key)))
            g.add((gu, n.reviewStatus, Literal(ENV_REVIEW_STATUS)))
            g.add((gu, n.sourceType, Literal("textbook")))
            for c in grp.get("categories") or []:
                g.add((gu, n.category, Literal(c)))
            for a in (grp.get("aliases_ko") or []) + (grp.get("aliases_en") or []):
                g.add((gu, n.alias, Literal(a)))
            for key2, pred in (("patient_summary_ko", n.patientSummary),
                               ("evidence_summary_ko", n.evidenceSummary),
                               ("persistence_ko", n.persistence)):
                if grp.get(key2):
                    g.add((gu, pred, Literal(grp[key2])))
            for r in grp.get("reservoirs_ko") or []:
                g.add((gu, n.hasReservoir, Literal(r)))
            for k_ in grp.get("korea_ko") or []:
                g.add((gu, n.koreaNote, Literal(k_)))
            cp = grp.get("carrier_particle") or {}
            if cp:
                g.add((gu, n.hasCarrierParticle, Literal(cp.get("description_ko", ""))))
                size = cp.get("size_um") or []
                if len(size) == 2:
                    g.add((gu, n.particleSizeMinUm, Literal(float(size[0]))))
                    g.add((gu, n.particleSizeMaxUm, Literal(float(size[1]))))
                if cp.get("airborne_ko"):
                    g.add((gu, n.airborneBehavior, Literal(cp["airborne_ko"])))
                if cp.get("implication_ko"):
                    g.add((gu, n.controlImplication, Literal(cp["implication_ko"])))
            for d in grp.get("diseases") or []:
                du = self._disease_uri(d, uri)
                g.add((gu, n.associatedDiseaseLabel, Literal(d)))
                if du is not None:
                    g.add((gu, n.associatedWithDisease, du))
            for i, al in enumerate(grp.get("major_allergens") or []):
                au = uri(f"env-allergen:{key}:{i}")
                g.add((au, RDF.type, n.MajorAllergen))
                g.add((gu, n.hasMajorAllergen, au))
                g.add((au, RDFS.label, Literal(al.get("name", ""))))
                for key2, pred in (("family_ko", n.proteinFamily), ("found_in_ko", n.foundIn),
                                   ("note_ko", n.note)):
                    if al.get(key2):
                        g.add((au, pred, Literal(al[key2])))
            for i, th in enumerate(grp.get("thresholds") or []):
                tu = uri(f"env-threshold:{key}:{i}")
                g.add((tu, RDF.type, n.ExposureThreshold))
                g.add((gu, n.hasThreshold, tu))
                g.add((tu, n.marker, Literal(th.get("marker", ""))))
                g.add((tu, n.value, Literal(float(th.get("value", 0)))))
                g.add((tu, n.unit, Literal(th.get("unit", ""))))
                g.add((tu, n.meaning, Literal(th.get("meaning_ko", ""))))
                g.add((tu, RDFS.label, Literal(f"{th.get('marker', '')} {th.get('value', '')} {th.get('unit', '')}")))
                cite(tu, th.get("sources"))
            for i, xr in enumerate(grp.get("cross_reactivity") or []):
                xu = uri(f"env-xreact:{key}:{i}")
                g.add((xu, RDF.type, n.CrossReactivity))
                g.add((gu, n.hasCrossReactivity, xu))
                g.add((xu, RDFS.label, Literal(xr.get("with_label_ko", xr.get("with", "")))))
                g.add((xu, n.withGroup, Literal(xr.get("with", ""))))
                if xr.get("via_ko"):
                    g.add((xu, n.via, Literal(xr["via_ko"])))
                if xr.get("note_ko"):
                    g.add((xu, n.note, Literal(xr["note_ko"])))
                if xr.get("with") in group_nodes:
                    g.add((gu, n.crossReactsWith, group_nodes[xr["with"]]))
            cal = grp.get("korea_calendar") or {}
            for ck, cv in cal.items():
                for mth in cv.get("months") or []:
                    g.add((gu, n.peakMonthKorea, Literal(f"{ck}:{int(mth)}")))
            add_measures(gu, grp.get("measures"))

        # 최신판 5장: 미세먼지·VOC·담배연기는 천식·비염뿐 아니라 아토피피부염 악화와도 연관된다
        _AGGRAVATES = {
            "outdoor_air_pollution": ("asthma", "allergic rhinitis", "atopic dermatitis"),
            "indoor_air_pollution": ("asthma", "allergic rhinitis", "atopic dermatitis"),
            "tobacco_smoke": ("asthma", "allergic rhinitis", "atopic dermatitis"),
        }
        factor_nodes = {}
        for key, f in (data.get("aggravating_factors") or {}).items():
            fu = uri(f"env-factor:{key}")
            factor_nodes[key] = fu
            g.add((fu, RDF.type, n.AggravatingFactor))
            g.add((fu, RDFS.label, Literal(f.get("label_ko", key))))
            g.add((fu, n.query, Literal(key)))
            g.add((fu, n.text, Literal(f.get("text_ko", ""))))
            g.add((fu, n.reviewStatus, Literal(ENV_REVIEW_STATUS)))
            g.add((fu, n.sourceType, Literal("textbook")))
            for k_ in f.get("korea_ko") or []:
                g.add((fu, n.koreaNote, Literal(k_)))
            for d in _AGGRAVATES.get(key, ("asthma", "allergic rhinitis")):
                du = self._disease_uri(d, uri)
                if du is not None:
                    g.add((fu, n.aggravates, du))
            cite(fu, f.get("sources"))
            add_measures(fu, f.get("measures"))
            # 성분별 오염물질(미세먼지·황사·SO₂·NO₂·오존) — 역치를 ExposureThreshold 로 같은 모양으로 싣는다
            for ck, c in (f.get("components") or {}).items():
                cu = uri(f"env-pollutant:{ck}")
                g.add((cu, RDF.type, n.Pollutant))
                g.add((fu, n.hasComponent, cu))
                g.add((cu, RDFS.label, Literal(c.get("label_ko", ck))))
                g.add((cu, n.query, Literal(ck)))
                g.add((cu, n.text, Literal(c.get("text_ko", ""))))
                g.add((cu, n.reviewStatus, Literal(ENV_REVIEW_STATUS)))
                for e in c.get("evidence_ko") or []:
                    g.add((cu, n.evidenceNote, Literal(e)))
                for k_ in c.get("korea_ko") or []:
                    g.add((cu, n.koreaNote, Literal(k_)))
                for i, th in enumerate(c.get("thresholds") or []):
                    tu = uri(f"env-threshold:{ck}:{i}")
                    g.add((tu, RDF.type, n.ExposureThreshold))
                    g.add((cu, n.hasThreshold, tu))
                    g.add((tu, n.marker, Literal(th.get("marker", ""))))
                    g.add((tu, n.value, Literal(float(th.get("value", 0)))))
                    g.add((tu, n.unit, Literal(th.get("unit", ""))))
                    g.add((tu, n.meaning, Literal(th.get("meaning_ko", ""))))
                    g.add((tu, RDFS.label, Literal(f"{th.get('marker', '')} {th.get('value', '')} {th.get('unit', '')}")))
                cite(cu, c.get("sources"))

        # 기전(ROS·TLR·GSTP1·운반체/보조제·장벽·후생유전·Th2/Th17) — 대기오염·담배·실내공기 요인에 건다
        for m in (data.get("mechanisms") or {}).get("items") or []:
            mu = uri(f"env-mechanism:{m['id']}")
            g.add((mu, RDF.type, n.Mechanism))
            g.add((mu, RDFS.label, Literal(m.get("label_ko", m["id"]))))
            g.add((mu, n.query, Literal(m["id"])))
            g.add((mu, n.text, Literal(m.get("text_ko", ""))))
            if m.get("patient_ko"):
                g.add((mu, n.patientSummary, Literal(m["patient_ko"])))
            g.add((mu, n.reviewStatus, Literal(ENV_REVIEW_STATUS)))
            cite(mu, m.get("sources"))
            for fk in ("outdoor_air_pollution", "indoor_air_pollution", "tobacco_smoke"):
                if fk in factor_nodes:
                    g.add((factor_nodes[fk], n.hasMechanism, mu))

        # 발생 배경(위생가설·미생물총·유전-환경) — 질환 Topic 에 건다(지시가 아니라 설명)
        for e in (data.get("etiology_context") or {}).get("items") or []:
            eu = uri(f"env-etiology:{e['id']}")
            g.add((eu, RDF.type, n.EtiologyFactor))
            g.add((eu, RDFS.label, Literal(e.get("label_ko", e["id"]))))
            g.add((eu, n.query, Literal(e["id"])))
            g.add((eu, n.text, Literal(e.get("text_ko", ""))))
            if e.get("patient_ko"):
                g.add((eu, n.patientSummary, Literal(e["patient_ko"])))
            for b in e.get("beneficial_ko") or []:
                g.add((eu, n.protectiveFactor, Literal(b)))
            for h in e.get("harmful_ko") or []:
                g.add((eu, n.riskFactorContext, Literal(h)))
            g.add((eu, n.reviewStatus, Literal(ENV_REVIEW_STATUS)))
            cite(eu, e.get("sources"))
            for d in ("asthma", "allergic rhinitis", "atopic dermatitis"):
                du = self._disease_uri(d, uri)
                if du is not None:
                    g.add((du, n.hasEtiologyContext, eu))

        for p in data.get("principles") or []:
            pu = uri(f"env-principle:{p['id']}")
            g.add((pu, RDF.type, n.Principle))
            g.add((pu, RDFS.label, Literal(p.get("text_ko", ""))))
            if p.get("basis_ko"):
                g.add((pu, n.basis, Literal(p["basis_ko"])))
            g.add((pu, n.reviewStatus, Literal(ENV_REVIEW_STATUS)))
            cite(pu, p.get("sources"))
            for gu in group_nodes.values():
                g.add((gu, n.hasPrinciple, pu))

    # ------------------------------------------------------------------
    # SPARQL
    # ------------------------------------------------------------------
    def sparql(self, query: str, limit: int = 50) -> Dict[str, Any]:
        """읽기 전용 SPARQL(SELECT/ASK)만 실행한다.

        그래프는 메모리에 있고 갱신 경로가 없으므로 수정 질의는 의미가 없다. 그래도 명시적으로
        막는다 — 질의문이 외부(챗봇·API)에서 들어올 수 있기 때문이다.
        """
        self._ensure()
        if not self._loaded:
            return {"ok": False, "error": self._load_error or "온톨로지를 사용할 수 없습니다."}
        if not _SPARQL_ALLOWED.match(query or "") or _SPARQL_FORBIDDEN.search(query or ""):
            return {"ok": False, "error": "SELECT 또는 ASK 질의만 실행할 수 있습니다."}
        try:
            res = self._graph.query(query)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"질의 오류: {e}"}
        if res.type == "ASK":
            return {"ok": True, "type": "ASK", "boolean": bool(res.askAnswer)}
        cols = [str(v) for v in (res.vars or [])]
        rows = []
        for i, row in enumerate(res):
            if i >= max(1, min(limit, 500)):
                break
            rows.append({c: (str(row[c]) if row[c] is not None else None) for c in cols})
        return {"ok": True, "type": "SELECT", "columns": cols, "rows": rows,
                "truncated": len(rows) >= max(1, min(limit, 500))}

    # ------------------------------------------------------------------
    # 챗봇용 검색
    # ------------------------------------------------------------------
    def topics(self) -> List[Dict[str, Any]]:
        self._ensure()
        out = []
        for q, t in self._topics.items():
            node = t["topic"]
            out.append({"query": q, "label": node.get("label"),
                        "url": (node.get("data") or {}).get("url"),
                        "expression_groups": len(t.get("clinical_context", {}).get("items", [])),
                        "evidence_cells": len(t.get("source_evidence", []))})
        return out

    def relevant_topics(self, question: str, diseases: Optional[Iterable[str]] = None) -> List[str]:
        """볼 주제를 고른다. **질문 쪽에 일반지식 의도가 있을 때만** 고른다.

        기저질환만으로 주제를 붙이면 "제 집먼지진드기 수치가 얼마였죠?" 처럼 순전히 이 환자의
        보고서를 묻는 질문에도 일반지식이 딸려 나온다. 그러면 답변에 쓰이지도 않은 참고자료가
        출처로 표시되어 사용자를 오해시킨다. 그래서 기저질환은 '어느 주제를 볼지' 만 거들고,
        검색을 켜는 방아쇠는 질문 문구(질환어 또는 의도어)다.
        """
        self._ensure()
        q = (question or "").lower()
        keyword_topics = [t for t, words in TOPIC_KEYWORDS.items()
                          if t in self._topics and any(w.lower() in q for w in words)]
        has_intent = bool(self.intent_predicates(question))
        if not keyword_topics and not has_intent:
            return []

        hits: List[str] = list(keyword_topics)
        # 의도는 있는데 질환을 특정하지 않았다면(예: "증상이 왜 생겨요?") 환자의 기저질환으로 좁힌다
        for code in (diseases or []):
            topic = DISEASE_TO_TOPIC.get(code)
            if topic and topic in self._topics and topic not in hits:
                hits.append(topic)
        return hits

    @staticmethod
    def intent_predicates(question: str) -> List[str]:
        q = (question or "").lower()
        preds: List[str] = []
        for _, (words, ps) in INTENT_PREDICATES.items():
            if any(w.lower() in q for w in words):
                for p in ps:
                    if p not in preds:
                        preds.append(p)
        return preds

    def facts(self, topic: str, predicates: Optional[Iterable[str]] = None,
              limit: int = 8) -> List[Dict[str, Any]]:
        """주제의 표현 그룹을 인용 정보와 함께 돌려준다(SPARQL 경유).

        근거가 많은 것부터 준다 — 원문에서 여러 번 확인된 표현일수록 대표성이 있다.
        """
        self._ensure()
        if not self._loaded or topic not in self._topics:
            return []
        node_id = self._topics[topic]["topic"]["id"]
        filt = ""
        preds = [p for p in (predicates or []) if p]
        if preds:
            joined = ", ".join(f'"{p}"' for p in preds)
            filt = f"FILTER(?predicate IN ({joined}))"
        q = f"""
        PREFIX allergy: <{NS}>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?group ?label ?predicate ?polarity ?status ?evidence ?claimId ?evidenceId ?quote ?url
        WHERE {{
          ?topic allergy:hasExpression ?group .
          ?topic allergy:query "{topic}" .
          ?group rdfs:label ?label ;
                 allergy:predicate ?predicate ;
                 allergy:polarity ?polarity ;
                 allergy:reviewStatus ?status ;
                 allergy:evidenceCount ?evidence .
          OPTIONAL {{ ?group allergy:claim ?c .
                     OPTIONAL {{ ?c allergy:claimId ?claimId }}
                     OPTIONAL {{ ?c allergy:evidenceId ?evidenceId }}
                     OPTIONAL {{ ?c allergy:rawText ?quote }}
                     OPTIONAL {{ ?c allergy:sourceUrl ?url }} }}
          {filt}
        }} ORDER BY DESC(?evidence)
        """
        res = self.sparql(q, limit=max(limit * 3, 30))
        if not res.get("ok"):
            return []
        out: List[Dict[str, Any]] = []
        seen = set()
        for row in res["rows"]:
            key = (row["predicate"], (row["label"] or "").lower())
            if key in seen:
                continue
            seen.add(key)
            gid = (row["group"] or "").split("#")[-1]
            out.append({
                "group_id": gid.replace("clinical-expression-group_", "clinical-expression-group:"),
                "label": row["label"],
                "predicate": row["predicate"],
                "predicate_ko": PREDICATE_LABEL_KO.get(row["predicate"], row["predicate"]),
                "polarity": row["polarity"],
                "review_status": row["status"],
                "evidence_count": int(row["evidence"] or 0),
                "claim_id": row.get("claimId"),
                "evidence_id": row.get("evidenceId"),
                "quote": _clean_quote(row.get("quote")),
                "source_url": row.get("url"),
                "topic": topic,
                "topic_url": (self._topics[topic]["topic"].get("data") or {}).get("url"),
                "node_id": node_id,
            })
            if len(out) >= limit:
                break
        return out

    def definition(self, topic: str) -> Optional[Dict[str, Any]]:
        """표준 용어의 정의 — 질환을 한 문장으로 설명할 때 쓴다.

        체계 우선순위 DO > HPO > SYMP. DO 만 보면 allergy·urticaria 처럼 DO 가 없거나 얕은 주제에서
        정의를 통째로 놓친다(스냅샷에 DO 5 · HPO 5 · SYMP 2).
        """
        self._ensure()
        if not self._loaded or topic not in self._topics:
            return None
        groups = self._topics[topic].get("terminology_summary", {}).get("groups", [])
        for system in ("DO", "HPO", "SYMP"):
            for grp in groups:
                tgt = grp.get("target") or {}
                if tgt.get("definition") and tgt.get("system") == system:
                    return {"label": tgt.get("label"), "system": system,
                            "code": tgt.get("code"), "release": tgt.get("release"),
                            "definition": tgt["definition"],
                            "parents": [p.get("label")
                                        for p in (grp.get("hierarchy") or {}).get("items", [])
                                        if p.get("label")]}
        return None

    def topic_status(self, topic: str) -> Dict[str, Any]:
        """주제의 **수집 범위**와 **매핑 검토 상태**.

        연동 가이드가 둘을 분리해 표시하라고 한다.
          - 임상 주장의 검토 상태(전부 candidate)와
          - 용어 매핑의 검토 상태(asthma 만 accepted 5건)는 별개다.
        또 수집량이 0 인 주제(allergy)를 '의학적으로 그런 관계가 없다'로 읽으면 안 된다.
        """
        self._ensure()
        if not self._loaded or topic not in self._topics:
            return {}
        t = self._topics[topic]
        md = t["topic"].get("mapping_display") or {}
        items = t.get("clinical_context", {}).get("items", [])
        return {
            "topic": topic,
            "label": t["topic"].get("label"),
            "url": (t["topic"].get("data") or {}).get("url"),
            "expression_groups": len(items),
            "evidence_cells": len(t.get("source_evidence", [])),
            "claim_review": "candidate" if items else "none_collected",
            "mapping_state": md.get("state"),
            "mapping_accepted": md.get("accepted_count", 0),
            "mapping_candidate": md.get("candidate_count", 0),
            "mapping_systems": md.get("target_systems", []),
            "snomed_available": bool((t.get("terminology_summary", {})
                                      .get("snomed") or {}).get("available")),
            "data_collected": bool(items),
        }

    # ------------------------------------------------------------------
    # 교과서 알레르겐 환경관리 층 — 조회
    # ------------------------------------------------------------------
    def allergen_topics(self) -> List[Dict[str, Any]]:
        """환경관리 층의 알레르겐군 목록(그래프 적재 여부와 함께)."""
        self._ensure()
        env = self._env_service()
        if env is None:
            return []
        out = []
        for key, grp in env.groups.items():
            out.append({"topic": key, "kind": "allergen", "label": grp.get("label_ko"),
                        "label_en": grp.get("label_en"), "categories": grp.get("categories") or [],
                        "measures": len(grp.get("measures") or []),
                        "diseases": grp.get("diseases") or [],
                        "review_status": ENV_REVIEW_STATUS, "source_type": "textbook"})
        return out

    def allergen_status(self, key: str) -> Dict[str, Any]:
        env = self._env_service()
        if env is None or key not in env.groups:
            return {}
        s = env.summary(key)
        return {"topic": key, "kind": "allergen", "label": s.get("label_ko"), "url": "",
                "measures": s.get("measures_total", 0), "review_status": ENV_REVIEW_STATUS,
                "claim_review": ENV_REVIEW_STATUS, "mapping_state": None, "mapping_accepted": 0,
                "mapping_candidate": 0, "mapping_systems": [], "snomed_available": False,
                "data_collected": True, "source_type": "textbook",
                "source_note": env.review_status, "diseases": s.get("diseases") or []}

    def allergen_facts(self, key: str, question: str = "", limit: int = 5) -> List[Dict[str, Any]]:
        """알레르겐군의 관리 조치를 그래프(SPARQL)에서 꺼내 질문 키워드 순 → 단계 순으로 돌려준다.

        질환 facts 와 같은 모양(group_id·predicate·review_status·quote·source_url)을 지켜
        챗봇 주입·UI 출처 줄이 그대로 쓰게 한다. 교과서 출처는 URL 이 없으므로 `source` 에 인용을 싣는다.
        """
        self._ensure()
        env = self._env_service()
        if not self._loaded or env is None or key not in env.groups:
            return []
        q = f"""
        PREFIX allergy: <{NS}>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?m ?label ?text ?tier ?order ?grade ?evidence ?caveat WHERE {{
          ?g allergy:query "{key}" ; a allergy:AllergenGroup ; allergy:hasControlMeasure ?m .
          ?m rdfs:label ?label ; allergy:text ?text ; allergy:tier ?tier ; allergy:tierOrder ?order ;
             allergy:evidenceGrade ?grade .
          OPTIONAL {{ ?m allergy:evidenceNote ?evidence }}
          OPTIONAL {{ ?m allergy:caveat ?caveat }}
        }} ORDER BY ?order
        """
        res = self.sparql(q, limit=100)
        rows = res.get("rows") if res.get("ok") else []
        # 챗봇은 환자 화면이다 — rejected 는 빼고, 게이트가 켜져 있으면 승인·수정된 조치만
        by_id = {m["id"]: m for m in env.measures(key, patient_facing=True)}
        ranked = (env.measures_matching(key, question, limit=limit) if question
                  else env.measures(key, limit=limit, patient_facing=True))
        order = [m["id"] for m in ranked]
        in_graph = {(r["m"] or "").split("env-measure:")[-1] for r in rows}
        out = []
        for mid in order:
            m = by_id.get(mid)
            if not m or (in_graph and mid not in in_graph):
                continue
            srcs = m.get("sources") or []
            out.append({
                "group_id": f"env-measure:{mid}", "label": m.get("short_ko", ""),
                "text": m.get("text_ko", ""), "predicate": "has_control_measure",
                "predicate_ko": PREDICATE_LABEL_KO["has_control_measure"],
                "tier": m.get("tier"), "tier_ko": m.get("tier_ko"),
                "evidence_grade": m.get("evidence_grade"), "evidence_grade_ko": m.get("evidence_grade_ko"),
                "evidence": m.get("evidence_ko", ""), "caveat": m.get("caveat_ko", ""),
                "polarity": "", "review_status": ENV_REVIEW_STATUS, "evidence_count": len(srcs),
                "claim_id": None, "evidence_id": f"textbook:{srcs[0]}" if srcs else None,
                "quote": "", "source_url": "", "sources": m.get("citations") or [],
                "source": "; ".join(c.get("citation", "") for c in (m.get("citations") or [])[:2]),
                "topic": key, "topic_url": "", "kind": "allergen",
            })
        return out

    def allergen_context(self, key: str, question: str = "", per_topic: int = 5) -> Dict[str, Any]:
        env = self._env_service()
        if env is None or key not in env.groups:
            return {}
        s = env.summary(key)
        return {"topic": key, "kind": "allergen", "label": s.get("label_ko"),
                "summary": s.get("patient_summary_ko", ""),
                "evidence_summary": s.get("evidence_summary_ko", ""),
                "carrier_particle": s.get("carrier_particle") or {},
                "thresholds": s.get("thresholds") or [],
                "cross_reactivity": s.get("cross_reactivity") or [],
                "facts": self.allergen_facts(key, question, limit=per_topic),
                "status": self.allergen_status(key)}

    def allergen_groups_for_question(self, question: str,
                                     patient_groups: Optional[Iterable[str]] = None) -> List[str]:
        env = self._env_service()
        if env is None:
            return []
        return env.groups_for_question(question, patient_groups)

    def factor_context(self, key: str, question: str = "", per_topic: int = 4) -> Dict[str, Any]:
        """악화 요인(미세먼지·황사·담배·새집·기후) 블록 — 알레르겐군 블록과 같은 facts 모양.

        그래프의 Pollutant 노드에서 성분별 역치를, 데이터셋에서 조치를 가져온다. 환자 판정과 무관한 일반 지식이다.
        """
        self._ensure()
        env = self._env_service()
        if env is None:
            return {}
        s = env.factor_summary(key, question, limit=per_topic)
        if not s:
            return {}
        facts = []
        for m in s.get("measures") or []:
            srcs = m.get("sources") or []
            facts.append({
                "group_id": f"env-measure:{m['id']}", "label": m.get("short_ko", ""),
                "text": m.get("text_ko", ""), "predicate": "has_control_measure",
                "predicate_ko": PREDICATE_LABEL_KO["has_control_measure"],
                "tier": m.get("tier"), "tier_ko": m.get("tier_ko"),
                "evidence_grade": m.get("evidence_grade"), "evidence_grade_ko": m.get("evidence_grade_ko"),
                "evidence": m.get("evidence_ko", ""), "caveat": m.get("caveat_ko", ""),
                "polarity": "", "review_status": ENV_REVIEW_STATUS, "evidence_count": len(srcs),
                "claim_id": None, "evidence_id": f"textbook:{srcs[0]}" if srcs else None,
                "quote": "", "source_url": "", "sources": m.get("citations") or [],
                "source": "; ".join(c.get("citation", "") for c in (m.get("citations") or [])[:2]),
                "topic": key, "topic_url": "", "kind": "factor",
            })
        # 성분별 역치는 그래프에서 읽는다(환경관리 층이 그래프에 실렸는지 확인하는 역할도 한다)
        pollutants = []
        if self._loaded and s.get("components"):
            q = f"""
            PREFIX allergy: <{NS}>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT ?p ?label ?marker ?value ?unit ?meaning WHERE {{
              ?f allergy:query "{key}" ; a allergy:AggravatingFactor ; allergy:hasComponent ?p .
              ?p rdfs:label ?label .
              OPTIONAL {{ ?p allergy:hasThreshold ?t . ?t allergy:marker ?marker ; allergy:value ?value ;
                          allergy:unit ?unit ; allergy:meaning ?meaning }}
            }}
            """
            res = self.sparql(q, limit=100)
            by_label: Dict[str, Dict[str, Any]] = {}
            for r in (res.get("rows") if res.get("ok") else []):
                item = by_label.setdefault(r["label"], {"label": r["label"], "thresholds": []})
                if r.get("marker"):
                    item["thresholds"].append({"marker": r["marker"], "value": r["value"], "unit": r["unit"],
                                               "meaning_ko": r["meaning"]})
            pollutants = list(by_label.values())
        # 성분 설명·근거는 데이터셋 순서대로(그래프 행은 순서가 없다)
        comps = []
        for c in s.get("components") or []:
            g_item = next((p for p in pollutants if p["label"] == c["label_ko"]), None)
            comps.append({"label": c["label_ko"], "text": c["text_ko"], "evidence": c["evidence_ko"],
                          "korea": c["korea_ko"],
                          "thresholds": (g_item or {}).get("thresholds") or c["thresholds"]})
        return {"topic": key, "kind": "factor", "label": s.get("label_ko"), "summary": s.get("text_ko", ""),
                "ecigarette": s.get("ecigarette_ko", ""), "components": comps, "korea": s.get("korea_ko") or [],
                "facts": facts, "status": {"topic": key, "kind": "factor", "label": s.get("label_ko"),
                                           "review_status": ENV_REVIEW_STATUS, "source_type": "textbook",
                                           "measures": s.get("measures_total", 0)}}

    def mechanism_notes(self, limit: int = 3) -> List[Dict[str, Any]]:
        """'왜 미세먼지가 알레르기를 악화시키나요' 에 붙일 기전 한 줄 설명(patient_ko)."""
        env = self._env_service()
        if env is None:
            return []
        return [{"id": m["id"], "label": m.get("label_ko", ""), "patient": m.get("patient_ko", ""),
                 "review_status": ENV_REVIEW_STATUS} for m in env.mechanisms()[:limit]]

    def retrieve(self, question: str, diseases: Optional[Iterable[str]] = None,
                 max_topics: int = 2, per_topic: int = 6,
                 include_treatment: bool = True,
                 allergen_groups: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        """질문 1건에 대한 RAG 결과. 주제별 정의 + 관련 표현 그룹 + 인용 정보.

        `allergen_groups` 는 이 환자의 '실제 주의' 항원이 속한 알레르겐군(mite·cat …)이다. 질문에
        관리·회피 의도는 있는데 알레르겐 이름이 없을 때('공기청정기가 도움이 되나요?') 어느 군을 볼지
        좁히는 데만 쓴다 — 기저질환이 질환 주제를 좁히는 것과 같은 역할이다.
        """
        self._ensure()
        if not self._loaded:
            return {"available": False, "topics": [], "reason": self._load_error}
        allergen_blocks = [self.allergen_context(k, question, per_topic=min(per_topic, 5))
                           for k in self.allergen_groups_for_question(question, allergen_groups)]
        allergen_blocks = [b for b in allergen_blocks if b]
        env = self._env_service()
        factor_blocks = [self.factor_context(k, question, per_topic=min(per_topic, 4))
                         for k in (env.factors_for_question(question) if env is not None else [])]
        factor_blocks = [b for b in factor_blocks if b]
        # '왜 그런가요' 류의 질문이면 기전 한 줄 설명을 함께 준다
        ql = (question or "").lower()
        mechanism_blocks = (self.mechanism_notes() if factor_blocks and
                            any(w in ql for w in ("왜", "이유", "기전", "어떻게 영향", "why", "mechanism", "为什么")) else [])
        topics = self.relevant_topics(question, diseases)[:max_topics]
        preds = self.intent_predicates(question)
        if not include_treatment:
            preds = [p for p in preds if p not in TREATMENT_PREDICATES]
        blocks = []
        uncollected: List[Dict[str, Any]] = []
        for tp in topics:
            facts = self.facts(tp, preds or None, limit=per_topic + len(SENSITIVE_PREDICATES))
            facts = [f for f in facts if f["predicate"] not in SENSITIVE_PREDICATES]
            if not include_treatment:
                facts = [f for f in facts if f["predicate"] not in TREATMENT_PREDICATES]
            facts = facts[:per_topic]
            st = self.topic_status(tp)
            if not st.get("data_collected"):
                uncollected.append(st)
            blocks.append({"topic": tp, "definition": self.definition(tp), "facts": facts,
                           "status": st})
        return {"available": True, "topics": blocks, "predicates": preds,
                "uncollected": uncollected,
                "allergen_topics": allergen_blocks,
                "factor_topics": factor_blocks,
                "mechanisms": mechanism_blocks,
                "allergen_source": ("알레르겐 환경관리 교과서 정리(한글 교과서 이전 판 환경관리·회피요법 장 + 최신판 5장 "
                                    "알레르기질환과 환경 + Middleton 26·27·28·86장), 알레르기 전문의 검토 전"),
                "source": "Wikipedia 기반 온톨로지 스냅샷(2026-09-16), 임상 주장은 모두 검토 전(candidate)"}

    # ------------------------------------------------------------------
    # 연동 가이드가 제안한 읽기 전용 도구 4종
    # ------------------------------------------------------------------
    def search_topics(self, q: str) -> List[Dict[str, Any]]:
        """문자열로 주제를 찾는다. 별칭(hay fever → Allergic rhinitis)도 본다.

        가이드 주의: 문서 별칭 통합은 '같은 Wikipedia 문서'라는 뜻이지 임상 하위유형 동등성이
        아니다. 그래서 어떤 별칭으로 걸렸는지 함께 돌려준다.
        """
        self._ensure()
        needle = (q or "").strip().lower()
        if not needle:
            return []
        out = []
        for topic, t in self._topics.items():
            node = t["topic"]
            aliases = [m.get("label", "") for m in (node.get("identity") or {}).get("members", [])]
            hay = [topic, node.get("label", "")] + aliases
            matched = [h for h in hay if h and needle in h.lower()]
            if matched:
                st = self.topic_status(topic)
                st["kind"] = "disease"
                st["matched_on"] = matched[:4]
                st["alias_note"] = ("문서 식별상의 별칭이며 임상 하위유형이 같다는 뜻은 아닙니다."
                                    if matched[0].lower() != topic else "")
                out.append(st)
        # 교과서 알레르겐군(집먼지진드기·고양이 …)도 같은 검색으로 찾는다. 질환 주제 뒤에 붙인다.
        env = self._env_service()
        if env is not None:
            for key in env.search_groups(needle):
                st = self.allergen_status(key)
                grp = env.group(key)
                names = [grp.get("label_ko", ""), grp.get("label_en", "")] + \
                        list(grp.get("aliases_ko") or []) + list(grp.get("aliases_en") or [])
                st["matched_on"] = [x for x in names if x and needle in x.lower()][:4]
                st["alias_note"] = ""
                out.append(st)
        return out

    def get_topic_context(self, topic: str, predicate: Optional[str] = None,
                          limit: int = 20) -> Dict[str, Any]:
        """주제의 임상 관계 + 검토 상태 + 수집 범위."""
        self._ensure()
        if topic not in self._topics:
            env = self._env_service()
            if env is not None and topic in env.groups:
                ctx = self.allergen_context(topic, "", per_topic=limit)
                return {"status": ctx["status"], "definition": {"label": ctx["label"], "system": "textbook",
                                                                "code": "", "release": "",
                                                                "definition": ctx["summary"], "parents": []},
                        "facts": ctx["facts"], "allergen": ctx}
            known = list(self._topics) + list((env.groups if env is not None else {}))
            return {"error": f"알 수 없는 주제: {topic}", "known": known}
        return {"status": self.topic_status(topic), "definition": self.definition(topic),
                "facts": self.facts(topic, [predicate] if predicate else None, limit=limit)}

    def get_evidence(self, evidence_id: str) -> Dict[str, Any]:
        """근거 셀 원문. claim 의 evidence_id 로 실제 문장·출처·판본을 되짚는다.

        `textbook:<source_id>` 는 환경관리 층의 교과서·논문 출처다(원문 셀이 아니라 서지·요약).
        """
        self._ensure()
        if (evidence_id or "").startswith("textbook:"):
            env = self._env_service()
            sid = evidence_id.split(":", 1)[1]
            s = (env.data.get("sources") or {}).get(sid) if env is not None else None
            if not s:
                return {"error": f"근거를 찾을 수 없습니다: {evidence_id}"}
            return {"id": evidence_id, "kind": "textbook_source", "text": s.get("finding_ko", ""),
                    "clean_text": s.get("finding_ko", ""), "citation": s.get("citation", ""),
                    "source_url": "", "pmid": s.get("pmid", ""), "doi": s.get("doi", ""),
                    "pages": s.get("pages", ""), "verified": s.get("verified", ""),
                    "review_status": ENV_REVIEW_STATUS}
        ev = self._evidence.get(evidence_id)
        if not ev:
            # clinical-claim 의 source.evidence_id 는 'evidence:...' 이고 소유 셀 목록은
            # 'wikipedia-evidence:...' 다. 둘 다 받아준다.
            for eid, cell in self._evidence.items():
                if eid.endswith(evidence_id.split(":")[-1]):
                    ev = cell
                    break
        if not ev:
            return {"error": f"근거를 찾을 수 없습니다: {evidence_id}"}
        return {"id": ev.get("id"), "text": (ev.get("text") or "").strip(),
                "clean_text": _clean_quote(ev.get("text"), limit=1000),
                "source_url": ev.get("source_url"), "revision_url": ev.get("revision_url"),
                "revision_timestamp": ev.get("revision_timestamp"),
                "field": ev.get("field"), "sheet": ev.get("sheet"),
                "section": ev.get("section"), "text_sha256": ev.get("text_sha256")}

    def get_terminology(self, topic: str) -> Dict[str, Any]:
        """표준 용어 매핑 — 체계·코드·판본·검토 상태·상위 개념."""
        self._ensure()
        if topic not in self._topics:
            return {"error": f"알 수 없는 주제: {topic}"}
        t = self._topics[topic]
        ts = t.get("terminology_summary", {})
        terms = []
        for grp in ts.get("groups", []):
            tgt = grp.get("target") or {}
            terms.append({
                "label": tgt.get("label"), "system": tgt.get("system"), "code": tgt.get("code"),
                "release": tgt.get("release"), "definition": tgt.get("definition"),
                "synonyms": [x.get("term") for x in (tgt.get("synonyms") or []) if x.get("term")],
                "parents": [{"label": p.get("label"), "code": p.get("code"),
                             "system": p.get("system"), "predicate": p.get("predicate")}
                            for p in (grp.get("hierarchy") or {}).get("items", [])],
            })
        st = self.topic_status(topic)
        return {"topic": topic, "systems": ts.get("systems", []), "terms": terms,
                "mapping_state": st.get("mapping_state"),
                "mapping_accepted": st.get("mapping_accepted"),
                "mapping_candidate": st.get("mapping_candidate"),
                "snomed": ts.get("snomed", {}),
                "constraints": ts.get("constraints", []),
                "note": "매핑 검토 상태는 임상 주장의 검토 상태와 별개입니다."}


_ontology_service: Optional[OntologyService] = None


def get_ontology_service() -> OntologyService:
    global _ontology_service
    if _ontology_service is None:
        _ontology_service = OntologyService()
    return _ontology_service


# ---------------------------------------------------------------------------
# 리포트·카드뉴스용 질환 일반 정보(한국어 요약)
# ---------------------------------------------------------------------------
# 요약은 scripts/build_ontology_summaries.py 가 손으로 고른 라벨 대응표로 만든다(LLM 없음).
# 런타임은 파일만 읽는다 — 환자에게 남는 문서의 내용이 요청마다 흔들리지 않게.
SUMMARIES_PATH = BASE_DIR / "data" / "ontology_disease_summaries_ko.json"
_SUMMARIES: Optional[Dict[str, Any]] = None


def load_disease_summaries() -> Dict[str, Any]:
    global _SUMMARIES
    if _SUMMARIES is None:
        try:
            _SUMMARIES = json.loads(SUMMARIES_PATH.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"질환 요약 파일을 읽지 못함({SUMMARIES_PATH.name}): {e}")
            _SUMMARIES = {"topics": {}}
    return _SUMMARIES


def disease_summaries_for(screening=None, limit: int = 3) -> List[Dict[str, Any]]:
    """환자가 문진에서 고른 기저 질환의 일반 정보. 고른 게 없으면 '알레르기' 일반 설명 하나.

    환자가 말하지 않은 질환을 끌어오지 않는다 — 증상 부위만 보고 '천식'을 붙이면 진단처럼 읽힌다.
    """
    data = load_disease_summaries()
    topics = data.get("topics") or {}
    picked: List[str] = []
    for d in (getattr(screening, "allergic_diseases", None) or []):
        t = DISEASE_TO_TOPIC.get(d)
        if t and t in topics and t not in picked:
            picked.append(t)
    if not picked and "allergy" in topics:
        picked = ["allergy"]
    out = []
    for t in picked[:limit]:
        item = dict(topics[t])
        item["topic"] = t
        out.append(item)
    return out


def disease_summary_notice() -> str:
    return load_disease_summaries().get("notice_ko") or ""
