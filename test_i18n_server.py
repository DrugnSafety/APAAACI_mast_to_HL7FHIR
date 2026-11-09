"""서버가 만드는 문구가 화면 언어를 따라가는지 검증한다.

왜 필요한가: 화면 크롬(web/i18n.js)은 번역돼 있는데 서버가 만드는 문구는 한국어로 남아,
영어/중국어 화면 한복판에 한글이 그대로 나오던 회귀가 있었다. 두 군데였다.
  1) /api/health 의 스크리닝 선택지 라벨 — 언어 무관하게 한국어 고정
  2) 상담 챗봇 추천 답변 — 세 언어 문장 템플릿 안에 한국어 항원명·회피 수칙을 그대로 끼워 넣음
API 키 없이 돌아야 하므로, LLM 을 타는 경로는 검사하지 않는다.
"""
import re

from services.screening_service import get_screening_service, normalize_lang
from services.translation_service import TranslationService

HANGUL = re.compile(r"[가-힣]")


class TestScreeningOptionLabels:
    """스크리닝 선택지는 요청 언어로 나와야 한다(칩 라벨이 한국어로 남던 문제)."""

    def test_korean_is_unchanged(self):
        opts = get_screening_service().disease_options("ko")
        labels = {o["code"]: o["label"] for o in opts}
        assert labels["allergic_rhinitis"] == "알레르기 비염"

    def test_english_has_no_hangul(self):
        sc = get_screening_service()
        for getter in (sc.disease_options, sc.medication_options, sc.organ_system_options):
            for opt in getter("en"):
                assert not HANGUL.search(opt["label"]), f"영어 라벨에 한글: {opt}"

    def test_chinese_has_no_hangul(self):
        sc = get_screening_service()
        for getter in (sc.disease_options, sc.medication_options, sc.organ_system_options):
            for opt in getter("zh"):
                assert not HANGUL.search(opt["label"]), f"중국어 라벨에 한글: {opt}"

    def test_codes_are_identical_across_languages(self):
        """라벨만 바뀌고 code 는 그대로여야 한다 — code 가 바뀌면 선택값이 서버에서 안 읽힌다."""
        sc = get_screening_service()
        for getter in (sc.disease_options, sc.medication_options, sc.organ_system_options):
            codes = [[o["code"] for o in getter(l)] for l in ("ko", "en", "zh")]
            assert codes[0] == codes[1] == codes[2]

    def test_unknown_language_falls_back_to_korean(self):
        assert normalize_lang("fr") == "ko"
        assert normalize_lang(None) == "ko"
        assert get_screening_service().disease_options("fr") == \
            get_screening_service().disease_options("ko")

    def test_locale_tags_are_normalized(self):
        assert normalize_lang("en-US") == "en"
        assert normalize_lang("zh-CN") == "zh"
        assert normalize_lang("ZH_Hans") == "zh"


class TestTranslateObjLists:
    """회피 수칙·교차반응 음식은 값이 '문자열 리스트'라 예전 walk() 가 통째로 건너뛰었다."""

    def _svc(self):
        svc = TranslationService(api_key="")     # 키 없음 → LLM 호출 없이 캐시만 사용
        return svc

    def test_list_items_are_collected_as_targets(self):
        svc = self._svc()
        src = "침구는 주 1회 55~60℃ 물로 세탁하세요"
        svc._cache[svc._key(src, "en")] = "Wash bedding weekly at 55~60℃"
        obj = {"assessments": [{"avoidance_control_ko": [src], "category": "mite"}]}
        out = svc.translate_obj(obj, "en", {"avoidance_control_ko"})
        assert out["assessments"][0]["avoidance_control_ko"] == ["Wash bedding weekly at 55~60℃"]

    def test_non_target_keys_are_untouched(self):
        svc = self._svc()
        obj = {"assessments": [{"avoidance_control_ko": ["침구 세탁"], "category": "mite",
                                "allergen_name": "Dermatophagoides farinae"}]}
        out = svc.translate_obj(obj, "en", {"avoidance_control_ko"})
        a = out["assessments"][0]
        assert a["category"] == "mite"
        assert a["allergen_name"] == "Dermatophagoides farinae"

    def test_missing_cache_and_no_key_keeps_korean(self):
        """키가 없고 캐시에도 없으면 조용히 비우지 말고 원문을 남겨야 한다.

        영구 캐시에 절대 없을 문자열을 써야 한다 — 흔한 단어를 쓰면 이전 실행이 남긴
        캐시에 걸려서 테스트가 무의미해진다.
        """
        svc = self._svc()
        src = "캐시에없는문장-zz9"
        assert svc._cache.get(svc._key(src, "en")) is None
        obj = {"assessments": [{"oas_foods": [src]}]}
        out = svc.translate_obj(obj, "en", {"oas_foods"})
        assert out["assessments"][0]["oas_foods"] == [src]
