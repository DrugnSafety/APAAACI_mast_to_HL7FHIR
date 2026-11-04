"""연구실 Ollama 요청 크기 예산 — nginx 413(Request Entity Too Large) 회피.

2026-10-09 실제 결과지 29장 평가에서 두 번째 판독(절반 확대본 2장)이 전부 413 으로 실패했다.
fit_payload 는 한 요청의 이미지 base64 총량을 한도 아래로 줄이되, 한도 안이면 손대지 않는다.
"""
import base64
import io
import random

from PIL import Image

from utils.image_preprocess import fit_payload


def _b64_noise(w: int, h: int, quality: int = 95) -> str:
    """압축이 잘 안 되는(노이즈) 이미지를 만들어 크기를 키운다."""
    rnd = random.Random(0)
    img = Image.effect_noise((w, h), 80).convert("RGB")
    px = img.load()
    for _ in range(2000):
        px[rnd.randrange(w), rnd.randrange(h)] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_small_payload_untouched():
    imgs = [_b64_noise(200, 200)]
    assert fit_payload(imgs, 10 * 1024 * 1024) == imgs


def test_budget_zero_disables():
    imgs = [_b64_noise(200, 200)]
    assert fit_payload(imgs, 0) == imgs


def test_two_halves_fit_under_budget():
    halves = [_b64_noise(1600, 1300), _b64_noise(1600, 1300)]
    budget = 900 * 1024
    assert sum(len(b) for b in halves) > budget, "픽스처가 한도를 넘어야 의미가 있다"
    out = fit_payload(halves, budget)
    assert len(out) == 2
    assert sum(len(b) for b in out) <= budget
    for b in out:  # 줄인 결과도 여전히 열리는 JPEG 여야 한다
        Image.open(io.BytesIO(base64.b64decode(b))).verify()


class _FakeClient:
    """chat.completions.create 만 흉내 — 빈 결과 JSON 을 돌려준다."""
    class chat:
        class completions:
            @staticmethod
            def create(**kw):
                class M:
                    content = '{"test_type":"MAST","patient":{},"results":[]}'

                class C:
                    message = M()

                class R:
                    choices = [C()]
                return R()


def test_ocr_service_applies_budget_only_for_ollama(monkeypatch):
    """OpenAI 경로에는 손대지 않고, Ollama 경로에서만 fit_payload 를 거친다."""
    from config.settings import Settings
    from services import llm_backend
    from services import ocr_service as mod
    import utils.image_preprocess as prep

    calls = []
    monkeypatch.setattr(prep, "fit_payload", lambda images, max_bytes: calls.append(max_bytes) or images)
    monkeypatch.setattr(llm_backend, "client", lambda backend=None: _FakeClient())
    monkeypatch.setattr(llm_backend, "normalize", lambda b: b)

    s = Settings()
    s.ollama_max_payload_kb = 900
    svc = mod.OCRService.__new__(mod.OCRService)
    svc.settings = s
    svc.client = _FakeClient()

    svc._call_vision_model("qwen3.8:27b", "p", ["aGVsbG8="], backend="ollama")
    assert calls == [900 * 1024]
    svc._call_vision_model("gpt-5.4", "p", ["aGVsbG8="], backend="openai")
    assert calls == [900 * 1024], "OpenAI 경로에서는 예산을 적용하지 않는다"
