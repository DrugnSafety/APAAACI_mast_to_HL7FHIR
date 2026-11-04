"""환경관리 조치의 검토·승인 워크플로 — 상태 변경·수정 문장·게이트·HTML 검토 결과 반영.

불변식
  ① rejected 조치는 게이트와 무관하게 환자 화면(수칙·판정 안내·챗봇 facts)에서 빠진다.
  ② 게이트 approved_only 에서는 승인·수정된 조치만 환자 화면에 나가고, 개발용 조회(measures 기본)는 영향받지 않는다.
  ③ revised 는 환자 문장을 바꾸되 원문을 review.original_* 에 남긴다.
  ④ 검토 HTML 의 내보내기(JSON)를 그대로 넣으면 체크한 조치가 승인되고, 사람이 처리할 메모는 돌려준다.
  ⑤ 모든 변경이 로그 파일에 남고, 실제 데이터 파일은 테스트가 건드리지 않는다.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from models.schemas import AllergenAssessment, ClinicalRelevance
from services.environment_control_service import DATA_PATH, EnvironmentControlService, PATIENT_GATE_ENV

BASE = Path(__file__).resolve().parent
SCRIPT = BASE / "scripts" / "review_environment_control.py"


@pytest.fixture
def svc(tmp_path):
    data = tmp_path / "env.json"
    shutil.copy(DATA_PATH, data)
    return EnvironmentControlService(path=data, log_path=tmp_path / "log.json")


def _assess(name, category, relevance=ClinicalRelevance.CLINICALLY_RELEVANT):
    return AllergenAssessment(allergen_name=name, category=category, relevance=relevance)


class TestStatusChanges:
    def test_default_is_candidate_and_stats_count_everything(self, svc):
        st = svc.review_stats()
        assert st["total"]["candidate"] == sum(c["candidate"] for c in st["per_owner"].values()) > 60
        assert st["total"]["approved"] == 0

    def test_approve_reject_revise_round_trip(self, svc):
        svc.set_review("mite_encasing", "approved", by="검토자", note="OK")
        svc.set_review("cat_tannic_acid", "rejected", by="검토자", note="국내 유통 없음")
        svc.set_review("mite_humidity", "revised", by="검토자", short_ko="실내 습도 45% 미만", text_ko="실내 상대습도를 45% 미만으로 유지하세요.")
        svc.save()
        again = EnvironmentControlService(path=svc.path, log_path=svc.log_path)
        m = again.find_measure("mite_encasing")["measure"]["review"]
        assert m["status"] == "approved" and m["reviewed_by"] == "검토자" and m["reviewed_on"] and m["note"] == "OK"
        assert again.measure_status(again.find_measure("cat_tannic_acid")["measure"]) == "rejected"
        rev = again.find_measure("mite_humidity")["measure"]["review"]
        assert rev["status"] == "revised" and rev["original_short_ko"].startswith("실내 습도 50% 미만")
        shown = {x["id"]: x for x in again.measures("mite")}["mite_humidity"]
        assert shown["short_ko"] == "실내 습도 45% 미만" and shown["text_ko"].startswith("실내 상대습도를 45%")
        assert again.data["review_summary"]["approved"] == 1 and again.data["review_summary"]["rejected"] == 1
        log = json.loads(svc.log_path.read_text(encoding="utf-8"))["events"]
        assert [e["event"] for e in log] == ["approved", "rejected", "revised"]

    def test_unknown_measure_or_status_is_refused(self, svc):
        with pytest.raises(KeyError):
            svc.set_review("nope", "approved")
        with pytest.raises(ValueError):
            svc.set_review("mite_encasing", "blessed")
        with pytest.raises(ValueError):
            svc.set_review("mite_encasing", "revised")          # 바뀐 문장 없이 revised 불가

    def test_real_data_and_log_files_untouched(self, svc):
        from services.environment_control_service import REVIEW_LOG_PATH
        before = DATA_PATH.read_text(encoding="utf-8")
        log_before = REVIEW_LOG_PATH.read_text(encoding="utf-8") if REVIEW_LOG_PATH.exists() else None
        svc.set_review("mite_encasing", "approved")
        svc.save()
        assert DATA_PATH.read_text(encoding="utf-8") == before
        assert (REVIEW_LOG_PATH.read_text(encoding="utf-8") if REVIEW_LOG_PATH.exists() else None) == log_before


class TestPatientGate:
    def test_rejected_is_hidden_everywhere_even_without_gate(self, svc, monkeypatch):
        monkeypatch.delenv(PATIENT_GATE_ENV, raising=False)
        svc.set_review("cat_hand_face", "rejected")
        ids_dev = {m["id"] for m in svc.measures("cat")}
        ids_all = {m["id"] for m in svc.measures("cat", include_rejected=True)}
        assert "cat_hand_face" not in ids_dev and "cat_hand_face" in ids_all
        assert "만진 뒤 손 씻기, 눈·얼굴 비비지 않기" not in svc.tiered_tips("cat", limit=20)

    def test_approved_only_gate_limits_patient_outputs_but_not_dev_view(self, svc, monkeypatch):
        monkeypatch.setenv(PATIENT_GATE_ENV, "approved_only")
        svc.set_review("mite_encasing", "approved")
        svc.set_review("mite_hot_wash", "revised", short_ko="침구 주 1회 60℃ 세탁")
        assert svc.tiered_tips("mite", limit=20) == ["매트리스·베개·이불에 진드기 차단 커버", "침구 주 1회 60℃ 세탁"]
        blocks = svc.for_assessments([_assess("Dermatophagoides farinae", "mite")])
        assert {m["id"] for m in blocks[0]["measures"]} == {"mite_encasing", "mite_hot_wash"}
        assert svc.for_assessments([_assess("Cat dander", "animal")]) and \
            svc.for_assessments([_assess("Cat dander", "animal")])[0]["measures"] == []   # 고양이는 승인 0건
        assert len(svc.measures("mite")) > 2                         # 개발용 조회는 그대로
        # 챗봇 매칭도 환자 화면이다: 승인 전인 '공기청정기' 조치는 걸리지 않고 승인된 1단계 조치로 대신한다
        assert {m["id"] for m in svc.measures_matching("mite", "공기청정기가 도움이 되나요?")} == {"mite_encasing", "mite_hot_wash"}
        assert svc.measures_matching("mite", "공기청정기가 도움이 되나요?", patient_facing=False)[0]["id"] == "mite_air_cleaner"

    def test_gate_value_is_validated(self, monkeypatch):
        from services.environment_control_service import patient_gate
        monkeypatch.setenv(PATIENT_GATE_ENV, "whatever")
        assert patient_gate() == "candidate_ok"
        monkeypatch.setenv(PATIENT_GATE_ENV, "APPROVED_ONLY")
        assert patient_gate() == "approved_only"


class TestImportFromReviewPage:
    EXPORT = {
        "checked": ["m:mite_encasing", "m:mite_hot_wash", "f:smoke_home_free", "m:does_not_exist", "dis:0", "dd:2", "next:1"],
        "unchecked": ["m:mite_air_cleaner"],
        "notes": {"note:mite": "습도 목표는 45% 미만으로", "note:kb:Cat dander": "목욕 문장 삭제 요청",
                  "note:design": "게이트는 검토 후 켜기", "note:pollen": "   "},
    }

    def test_import_applies_measures_and_group_notes_and_returns_manual_items(self, svc):
        out = svc.import_review_export(self.EXPORT, by="검토자")
        assert set(out["applied"]) == {"mite_encasing", "mite_hot_wash", "smoke_home_free", "note:mite"}
        assert out["unknown"] == ["m:does_not_exist"]
        assert out["manual"]["checked"] == ["dis:0", "dd:2", "next:1"]
        assert out["manual"]["notes"] == {"note:kb:Cat dander": "목욕 문장 삭제 요청", "note:design": "게이트는 검토 후 켜기"}
        assert svc.measure_status(svc.find_measure("mite_encasing")["measure"]) == "approved"
        assert svc.measure_status(svc.find_measure("mite_air_cleaner")["measure"]) == "candidate"
        assert svc.groups["mite"]["review_note"] == "습도 목표는 45% 미만으로"
        assert svc.summary("mite")["review_counts"]["approved"] == 2

    def test_cli_import_and_stats(self, svc, tmp_path):
        from services.environment_control_service import REVIEW_LOG_PATH
        log_before = REVIEW_LOG_PATH.read_text(encoding="utf-8") if REVIEW_LOG_PATH.exists() else None
        export = tmp_path / "review.json"
        export.write_text(json.dumps(self.EXPORT, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), "--path", str(svc.path), "--import", str(export), "--by", "검토자"],
                           capture_output=True, text=True, cwd=str(BASE))
        assert r.returncode == 0, r.stderr
        assert "반영 4건" in r.stdout and "알 수 없는 키 1건" in r.stdout and "note:kb:Cat dander" in r.stdout
        # --path 를 주면 로그도 그 옆에 쓴다 — 실제 로그 파일을 건드리지 않는다
        assert (tmp_path / "env_review_log.json").exists()
        assert (REVIEW_LOG_PATH.read_text(encoding="utf-8") if REVIEW_LOG_PATH.exists() else None) == log_before
        r = subprocess.run([sys.executable, str(SCRIPT), "--path", str(svc.path), "--stats"], capture_output=True, text=True, cwd=str(BASE))
        assert "approved 3" in r.stdout
        r = subprocess.run([sys.executable, str(SCRIPT), "--path", str(svc.path), "--list", "--status", "approved"],
                           capture_output=True, text=True, cwd=str(BASE))
        assert "mite_encasing" in r.stdout and "검토자" in r.stdout
