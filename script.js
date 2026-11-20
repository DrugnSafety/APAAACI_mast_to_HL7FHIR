const flowSteps = [
  {
    kicker: "입력 · 합성 예시",
    title: "MAST 또는 피부단자검사 결과지를 입력합니다.",
    copy: "공개 전시에서는 실제 환자 자료 대신 합성 MAST 결과지를 사용합니다. 운영 환경에서는 식별정보 처리와 접근 통제가 별도로 필요합니다.",
    items: ["여러 검사기관 양식 지원", "이미지 원본과 추출값 대조", "공개 예시는 합성 자료만 사용"],
    href: "examples/01-synthetic-mast-report.png",
    label: "이 단계의 예시 받기"
  },
  {
    kicker: "추출 · 이중 판독",
    title: "같은 결과를 두 번 읽고 불일치를 표시합니다.",
    copy: "연구 시스템은 두 번의 판독을 대조해 확인할 항목을 표시합니다. 아래 공개 예시는 합성 검사지를 Tesseract로 읽은 결과와 검사 기준값을 나란히 보여줍니다.",
    items: ["독립적인 이중 판독", "양성 누락과 값 충돌 표시", "원본 위치와 구조화 값 연결"],
    href: "examples/02-ocr-comparison.html",
    label: "OCR 비교 화면 열기"
  },
  {
    kicker: "문진 · 항원별 질문",
    title: "양성 항원마다 임상 관련 질문을 구성합니다.",
    copy: "검사 양성은 알레르기 진단과 같지 않습니다. 증상, 노출, 반복성, 계절성 정보를 모아 의료진이 함께 검토할 맥락을 만듭니다.",
    items: ["증상과 노출의 시간 관계", "계절·주거·직업 환경", "교차반응 가능성 확인"],
    href: "examples/03-simulated-questionnaire.html",
    label: "합성 문진 열기"
  },
  {
    kicker: "근거 · 지식 그래프",
    title: "설명에 사용할 근거를 지식 그래프에서 찾습니다.",
    copy: "검증 대상 지식 구조가 항원, 동반질환, 교차반응, 예방 정보를 연결합니다. 생성 모델이 근거 없이 내용을 확장하지 않도록 범위를 제한합니다.",
    items: ["12,030개 지식 트리플", "159개 항원 레지스트리", "출처와 개념 관계를 검토 가능"],
    href: "examples/04-patient-guidance.html",
    label: "환자 안내 예시 열기"
  },
  {
    kicker: "설명 · 환자용 결과",
    title: "환자가 읽을 수 있는 안내와 카드뉴스를 만듭니다.",
    copy: "검사 수치, 문진 응답, 지식 근거를 분리해 보여줍니다. 환자가 다음 진료에서 확인할 질문과 생활 속 주의점을 짧게 설명합니다.",
    items: ["감작과 임상 증상을 구분", "짧고 읽기 쉬운 교육 카드", "진단 대신 확인할 질문 제시"],
    href: "examples/05-card-news.html",
    label: "카드뉴스 14장 보기"
  },
  {
    kicker: "표준화 · HL7 FHIR R4",
    title: "검사와 증상 정보를 표준 구조로 내보냅니다.",
    copy: "검사 결과는 Observation, 검토할 증상 정보는 AllergyIntolerance 등 FHIR R4 리소스 초안으로 구성합니다. 임상 확정에는 의료진 검토가 필요합니다.",
    items: ["공개 예시의 검사 항목 69개", "공개 예시의 내부 참조 139개", "연구에서 공식 검증한 번들 174개"],
    href: "examples/06-fhir-preview.html",
    label: "합성 FHIR 번들 구조 보기"
  }
];

const tabs = [...document.querySelectorAll(".flow-tab")];
const panel = document.querySelector("#flow-panel");
const kicker = panel?.querySelector("[data-flow-kicker]");
const title = panel?.querySelector("[data-flow-title]");
const copy = panel?.querySelector("[data-flow-copy]");
const list = panel?.querySelector("[data-flow-list]");
const link = panel?.querySelector("[data-flow-link]");

function selectStep(index, moveFocus = false) {
  const step = flowSteps[index];
  if (!step || !panel) return;

  tabs.forEach((tab, tabIndex) => {
    const selected = tabIndex === index;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });

  panel.setAttribute("aria-labelledby", tabs[index].id);
  kicker.textContent = step.kicker;
  title.textContent = step.title;
  copy.textContent = step.copy;
  list.replaceChildren(...step.items.map((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    return li;
  }));
  link.href = step.href;
  link.toggleAttribute("download", step.href.endsWith(".png"));
  link.innerHTML = `${step.label} <span aria-hidden="true">→</span>`;

  if (moveFocus) tabs[index].focus();
}

tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectStep(index));
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    let next = index;
    if (["ArrowRight", "ArrowDown"].includes(event.key)) next = (index + 1) % tabs.length;
    if (["ArrowLeft", "ArrowUp"].includes(event.key)) next = (index - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    selectStep(next, true);
  });
});

const header = document.querySelector("[data-header]");
function updateHeader() {
  header?.classList.toggle("scrolled", window.scrollY > 28);
}
updateHeader();
window.addEventListener("scroll", updateHeader, { passive: true });
