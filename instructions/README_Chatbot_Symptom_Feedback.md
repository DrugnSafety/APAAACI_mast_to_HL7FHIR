
# 🤖 Chatbot Prompt — Symptom Feedback from FHIR Observations

본 문서는 HL7 FHIR Observation Bundle을 기반으로 사용자 대화를 통해 **증상이 있었던 알레르겐**을 선별하는 챗봇 프롬프트 구성과 OpenAI Messages JSON 예시를 제공합니다.

## 1) 목표
- FHIR Observation에서 **양성(POSITIVE)** 알레르겐만 추출
- 사용자에게 목록을 제시하고 번호로 응답 받기
- 남은 항목을 **노출되었으나 증상 없음** / **아직 노출 경험 없음** 으로 구분
- 최종적으로 아래 스키마에 맞춘 JSON 결과 생성

```json
{
  "patient_id": "string",
  "test_date": "YYYY-MM-DD | null",
  "exposure_feedback": {
    "symptomatic": ["canonical allergen name", "..."],
    "asymptomatic": ["canonical allergen name", "..."],
    "unknown_exposure": ["canonical allergen name", "..."]
  }
}
```

## 2) 대화 규칙
1. 한국어 진행 + 알레르겐 한/영 병기
2. 단계적 질문 (증상 있었던 항목 → 나머지 분류)
3. 애매하면 간단히 재질문
4. 마지막에는 JSON만 출력(추가 문장 금지)

## 3) 시스템 프롬프트 핵심 지시
- Bundle에서 POS만 추출하여 목록화
- 사용자 선택 유도 및 재질문 로직
- 최종 JSON 스키마에 맞춰 출력
- 알레르겐은 Bundle에 있는 항목만 사용

## 4) OpenAI Messages JSON
`chatbot_prompt_messages.json` 파일을 참고하세요. 시스템 규칙, 스키마, 예시 대화가 포함되어 있습니다.

## 5) 파이프라인 통합
- 최종 JSON은 `allergyintolerance_builder.py` 입력으로 사용 → FHIR AllergyIntolerance Bundle 생성
