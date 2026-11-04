"""실제 결과지 29장 평가(2026-10-09)에서 드러난 결함의 회귀 테스트.

1) 두 번 읽기 병합이 같은 항원을 표기 차이('Cucumber (f244)' vs 'Cucumber (f244) (오이)', 로마숫자 Ⅱ vs II,
   한글/영문 순서)로 중복 행으로 추가했다 → 병합 키(name_core/_row_key) 정규화
2) 매퍼가 성분 항원(rPru p 4 (Peach))을 모항원(Peach)으로 접어 성분검사지에서 행이 합쳐졌다 → 성분 행은 매퍼 우회
3) 누운(가로) 사진은 행 번호가 밀리고 없는 항원이 생겼다 → 가로 사진만 Tesseract OSD 로 세운다
"""
import base64
import io

import pytest
from PIL import Image

from models.schemas import AllergenResult, OCRResult, PatientInfo, TestType
from services.ocr_validation import _row_key, merge_double_read, name_core, validate
from utils import image_preprocess as prep
from utils.allergen_mapper import get_allergen_mapper


def _row(idx, name, korean=None, value=None, value_text=None, cls=0, size=None):
    return AllergenResult(index=idx, raw_text="", allergen_name=name, korean_name=korean,
                          value=value, value_text=value_text, class_value=cls, size_text=size)


def _ocr(rows, tt=TestType.MAST):
    return OCRResult(test_type=tt, patient=PatientInfo(), results=rows)


# ---------------------------------------------------------------- 병합 키
@pytest.mark.parametrize("a,b", [
    ("Cucumber (f244)", "Cucumber (f244) (오이)"),
    ("Sycamore (플라타너스)", "플라타너스 (Sycamore)"),
    ("Grass mixture Ⅱ", "Grass mixture II"),
    ("Tree mixtureⅡ", "Tree mixture II"),
    ("Silkworm, pupa", "Silkworm, pupa (번데기)"),
    ("rAra h 2 (Peanut)", "rAra h 2, (Peanut) (땅콩 성분 h2)"),
    ("Alder (오리나무)(T2)", "Alder (오리나무) (T2)"),
])
def test_merge_key_ignores_code_korean_and_font_differences(a, b):
    m = get_allergen_mapper()
    assert _row_key(_row(1, a), m) == _row_key(_row(2, b), m)


@pytest.mark.parametrize("a,b", [
    ("Short ragweed (돼지풀)(W1)", "Ragweed, false (돼지풀)(W4)"),   # 한글명 공유 — 매퍼가 둘 다 Ragweed 로 접었다
    ("Sheep (양모)", "Lamb meat (양고기)"),                        # 레지스트리 Sheep 의 한글명이 양고기
    ("Yellow jacket (말벌)", "Wasp (장수말벌)"),                   # Wasp 가 Yellow jacket 의 별칭
    ("Grass mixture I", "Grass mixture II"),
    ("Mould 1", "Mould 2"),
    ("Egg white", "Egg yolk"),
    ("D. pteronyssinus", "D. farinae"),
    ("Dog hair", "Dog dander"),
])
def test_merge_key_keeps_distinct_printed_rows_apart(a, b):
    m = get_allergen_mapper()
    assert _row_key(_row(1, a), m) != _row_key(_row(2, b), m)


def test_merge_key_ignores_word_order_and_korean_only_rows_use_registry():
    m = get_allergen_mapper()
    assert _row_key(_row(1, "Ragweed, short"), m) == _row_key(_row(2, "Short ragweed"), m)
    assert _row_key(_row(1, "오리나무"), m) == _row_key(_row(2, "Alder (오리나무)(T2)"), m)


def test_second_read_recovers_missed_row_without_touching_its_neighbour():
    """첫 판독이 #76 Short ragweed 를 놓치고 두 번째가 2.5/class 2 로 읽은 경우 — 양성 행이 복구돼야 하고,
    같은 한글명(돼지풀)의 #85 Ragweed, false 에 '다른 판독값'이 붙으면 안 된다(리뷰 H1 재현)."""
    first = _ocr([_row(85, "Ragweed, false (돼지풀)(W4)", value=0.02, cls=0)])
    second = _ocr([_row(76, "Short ragweed (돼지풀)(W1)", value=2.5, cls=2),
                   _row(85, "Ragweed, false (돼지풀)(W4)", value=0.02, cls=0)])
    stats = merge_double_read(first, second)
    assert stats["added"] == 1 and stats["mismatch"] == 0
    assert "double_read_mismatch" not in first.results[0].review_flags
    assert any(r.allergen_name.startswith("Short ragweed") and r.value == 2.5 for r in first.results)


def test_merge_key_keeps_codes_when_only_generic_words_remain():
    """'Grass mix (gx1)' / '(gx2)', 'Tree mixture (tx5)' / '(tx6)' 는 코드로만 구분된다(리뷰 M6)."""
    m = get_allergen_mapper()
    assert _row_key(_row(1, "Grass mix (gx1)"), m) != _row_key(_row(2, "Grass mix (gx2)"), m)
    assert _row_key(_row(1, "Tree mixture (tx5)"), m) != _row_key(_row(2, "Tree mixture (tx6)"), m)
    assert _row_key(_row(1, "Grass mix (gx1)"), m) == _row_key(_row(2, "Grass mix (gx1) (목초 혼합)"), m)


def test_component_numbers_survive_without_spaces():
    """'rAra h2' 처럼 붙여 쓴 성분 번호는 코드가 아니다(리뷰 L11)."""
    m = get_allergen_mapper()
    assert _row_key(_row(1, "rAra h2 (Peanut)"), m) != _row_key(_row(2, "rAra h8 (Peanut)"), m)
    assert _row_key(_row(1, "rAra h2 (Peanut)"), m) == _row_key(_row(2, "rAra h2, (Peanut) (땅콩 성분)"), m)


def test_vision_call_does_not_retry_connection_errors(monkeypatch):
    """연결 오류·타임아웃은 같은 요청으로 다시 보내지 않는다(리뷰 M8) — 400(JSON 모드 미지원)만 재시도."""
    from config.settings import Settings
    from services import llm_backend
    from services import ocr_service as mod

    calls = []

    class Boom(Exception):
        status_code = None

    class Bad400(Exception):
        status_code = 400

    def make_client(exc):
        class C:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kw):
                        calls.append(kw.get("response_format"))
                        if len(calls) == 1:
                            raise exc
                        class M:
                            content = '{"test_type":"MAST","patient":{},"results":[]}'

                        class Ch:
                            message = M()

                        class R:
                            choices = [Ch()]
                        return R()
        return C()

    monkeypatch.setattr(llm_backend, "normalize", lambda b: b)
    svc = mod.OCRService.__new__(mod.OCRService)
    svc.settings = Settings()
    svc.settings.ollama_max_payload_kb = 0

    monkeypatch.setattr(llm_backend, "client", lambda backend=None: make_client(Boom("Connection error.")))
    with pytest.raises(Boom):
        svc._call_vision_model("gpt-5.4", "p", ["aGVsbG8="], backend="openai")
    assert len(calls) == 1, "연결 오류는 재시도하지 않는다"

    calls.clear()
    monkeypatch.setattr(llm_backend, "client", lambda backend=None: make_client(Bad400("response_format is not supported")))
    out = svc._call_vision_model("gpt-5.4", "p", ["aGVsbG8="], backend="openai")
    assert out.startswith("{") and len(calls) == 2 and calls[1] is None, "400 이면 JSON 모드를 빼고 한 번 더"


def test_component_row_is_not_folded_into_parent_allergen():
    m = get_allergen_mapper()
    assert _row_key(_row(1, "rPru p 4 (Peach)"), m) != _row_key(_row(2, "Peach (f95)"), m)
    assert _row_key(_row(1, "rAra h 2 (Peanut)"), m) != _row_key(_row(2, "Peanut"), m)
    assert _row_key(_row(1, "rAra h 2 (Peanut)"), m) != _row_key(_row(2, "rAra h 8 (Peanut)"), m)
    assert name_core("nGluten, (Wheat)") != name_core("Wheat flour")


def test_double_read_merge_does_not_duplicate_same_allergen():
    first = _ocr([_row(1, "Cucumber (f244)", value_text="<0.15"), _row(2, "Sycamore (플라타너스)", value=0.01)])
    second = _ocr([_row(1, "Cucumber (f244) (오이)", value_text="<0.15"), _row(2, "플라타너스 (Sycamore)", value=0.01)])
    stats = merge_double_read(first, second)
    assert stats["added"] == 0 and stats["both"] == 2 and stats["mismatch"] == 0
    assert len(first.results) == 2


def test_double_read_merge_keeps_component_rows_separate():
    first = _ocr([_row(1, "Peach", value=21.25, cls=4), _row(2, "rPru p 1, (Peach)", value=13.81, cls=3)])
    second = _ocr([_row(1, "Peach (복숭아)", value=21.25, cls=4), _row(2, "rPru p 1 (Peach)", value=13.81, cls=3)])
    stats = merge_double_read(first, second)
    assert stats["added"] == 0 and stats["mismatch"] == 0
    assert len(first.results) == 2


def test_validate_duplicate_flag_skips_component_vs_parent():
    ocr = _ocr([_row(1, "Peach", value=21.25, cls=4), _row(2, "rPru p 4 (Peach)", value=0.01, cls=0)])
    validate(ocr)
    assert "duplicate_allergen" not in ocr.results[0].review_flags
    assert "duplicate_allergen" not in ocr.results[1].review_flags


# ---------------------------------------------------------------- 자동 회전
def _landscape(w=1400, h=1050):
    return Image.new("RGB", (w, h), "white")


def test_auto_rotate_only_touches_landscape_images(monkeypatch):
    calls = []
    monkeypatch.setattr(prep, "detect_rotation", lambda img, timeout=60: calls.append(img.size) or (90, 2.0))
    img, angle = prep.auto_rotate(_landscape())
    assert angle == 90 and img.size == (1050, 1400)
    img2, angle2 = prep.auto_rotate(Image.new("RGB", (1050, 1400), "white"))
    assert angle2 is None and img2.size == (1050, 1400)
    assert calls == [(1400, 1050)], "세로 사진에는 OSD 를 부르지 않는다"


def test_auto_rotate_ignores_180_and_low_confidence(monkeypatch):
    monkeypatch.setattr(prep, "detect_rotation", lambda img, timeout=60: (180, 5.0))
    assert prep.auto_rotate(_landscape())[1] is None
    monkeypatch.setattr(prep, "detect_rotation", lambda img, timeout=60: (90, 0.1))
    assert prep.auto_rotate(_landscape())[1] is None
    monkeypatch.setattr(prep, "detect_rotation", lambda img, timeout=60: None)
    assert prep.auto_rotate(_landscape())[1] is None


def test_first_read_uses_rotated_image_even_without_preprocess(monkeypatch):
    """전처리를 꺼도(preprocess=False, double_read=True) 첫 판독과 절반 확대본이 같은 방향이어야 한다(리뷰 M2)."""
    from config.settings import Settings
    from services import ocr_service as mod
    monkeypatch.setattr(prep, "detect_rotation", lambda img, timeout=60: (90, 2.0))
    seen = []

    def fake_read(model, prompt, images, backend=None):
        sizes = [Image.open(io.BytesIO(base64.b64decode(b))).size for b in images]
        seen.append(sizes)
        return _ocr([]), {}

    svc = mod.OCRService.__new__(mod.OCRService)
    svc.settings = Settings()
    svc.settings.ocr_auto_rotate = True
    svc.settings.ocr_text_layer = False
    svc.ocr_prompt = "p"
    svc._read_once = fake_read
    buf = io.BytesIO()
    _landscape().save(buf, format="JPEG")
    out = svc.extract_from_image(buf.getvalue(), model="x", backend="openai", preprocess=False, double_read=True)
    first_w, first_h = seen[0][0]
    assert first_w < first_h, "첫 판독 이미지가 세워져 있어야 한다"
    assert all(w > 0 for w, _ in seen[1]) and len(seen[1]) == 2
    assert "회전 보정 90°" in (out.metadata or {}).get("preprocess", [])


def test_prepare_records_rotation_note(monkeypatch):
    monkeypatch.setattr(prep, "detect_rotation", lambda img, timeout=60: (90, 2.0))
    buf = io.BytesIO()
    _landscape().save(buf, format="JPEG")
    out = prep.prepare(buf.getvalue(), flatten=False)
    assert "회전 보정 90°" in out["notes"]
    full = Image.open(io.BytesIO(base64.b64decode(out["full"])))
    assert full.width < full.height
    out2 = prep.prepare(buf.getvalue(), flatten=False, rotate=False)
    assert not any("회전" in n for n in out2["notes"])
