# 2026-09-08 (part 2): SNOMED 147 antigens · mold vs mite · classic card news · result chatbot · server-content i18n

> 한국어: [`release_2026-09-08b_snomed_mold_chat_i18n.ko.md`](release_2026-09-08b_snomed_mold_chat_i18n.ko.md)
> Part 1 (FHIR Observation, dual UI, UI-string i18n): [`release_2026-09-08_fhir_dual_ui.en.md`](release_2026-09-08_fhir_dual_ui.en.md)

| Item | Value |
|---|---|
| Commits | `cb4c387` (SNOMED · mold · card news) · `574d404` (chatbot) · `63e0bc9` (server-content translation) |
| Follow-up commits | `58badf2` (three translation defects, scalar guard, slimmer image) · `138340f` (atomic cache save) · `80aa739` (mixture antigens coded) |
| Verification | **33** regression tests · 13 node tests (game + i18n) · full UI smoke (re-confirmed 2026-09-11) |

---

## 1. Real SNOMED CT SCTIDs: 22 → 147 antigens

### Method
`scripts/lookup_snomed_sctids.py` queries **tx.fhir.org** (SNOMED CT International) for each of the
148 antigens using `ValueSet/$expand` (substance and organism hierarchies) and `CodeSystem/$lookup`
(existence and active status). `scripts/refine_snomed_sctids.py` then applies category rules.

Errors the first automated pass produced, and how each was corrected:

| Problem | Example | Correction |
|---|---|---|
| Pollen antigens matched the **plant** concept | `Mugwort` → plant `Mugwort` (256340003) | For the pollen category, re-query as `"<name> pollen"` and accept only concepts containing `pollen` → `Mugwort pollen` (256293000). Same fix for `Oak`, `Willow`, `Birch` |
| **Component** codes matched as whole antigens | `Hazelnut` → `Cor a 8` (446073008, an nsLTP component) | Exclude the `^[A-Z][a-z]{2} [a-z] \d+` pattern → `Hazelnut` (256353000) |
| Assay and antibody concepts matched | `Alternaria` → `Alternaria serine proteinase` | Reject candidates containing `immunoglobulin/antibody/proteinase/…` → `Alternaria alternata` (36703000) |
| Animal antigens matched the **animal itself** | `Chicken` (animal category) → `Chicken - meat` | For the animal category prefer `dander/epithelium/feathers` → `Chicken feathers` (260165000) |
| Foods matched dishes or juices | `Grape` → `Grape juice` | Set manually, then re-verify with `$lookup` → `Grapes` (256317002) |

All 17 manual assignments were re-verified for existence and active status with `$lookup`.
Nine of my first guesses were **wrong and discarded**, including `Hornbeam`
(256271005 is actually Willow pollen), `Salmon`, `Potato` and `Pepper`. Each was replaced with an
actual search result.

### Result
- **147 of 148 antigens** resolve to a real SCTID, aliases and modifiers included
  (`Birch pollen` collapses onto `Birch`).
- Only the negative control falls back to OMOP. See `data/snomed_ct_unmapped.json`.

### How mixtures were handled
Mixture antigens (`Tree mixture 1·2`, `Indoor/Outdoor mold mixture`) **cannot be decomposed into
component species**. Which trees or molds each panel blends is stated only in the manufacturer's
package insert, not in the registry. Mixture 1 and mixture 2 in fact shared a single OMOP concept.

So instead of a single species they use a **broader concept that actually exists** in SNOMED. It is
less specific, not wrong, and a receiver validating against SNOMED accepts it.

| Antigen | Code | FSN |
|---|---|---|
| Tree mixture 1·2 | `782576004` | Tree pollen |
| Indoor/Outdoor mold mixture | `722071008` | Mold antigen |
| Histamine (SPT positive control reagent) | `54235008` | Histamine |
| 2-spotted spider mite | `106854009` | Family Tetranychidae |

Only the negative control gets no substance code. `Sodium chloride solution` (373757009) exists, but
putting a substance code on a control row invites a receiver to read it as a tested allergen.

### Side fix: stop disguising OMOP as SNOMED
The code previously emitted CDM (OMOP) `concept_id` values under
`system: http://snomed.info/sct`. A concept_id is not an SCTID, so a receiver validating against
SNOMED would reject it. The fallback now carries
`https://athena.ohdsi.org/search-terms/terms`.

```json
{ "system": "http://snomed.info/sct", "code": "256262001", "display": "European white birch pollen" }
{ "system": "https://athena.ohdsi.org/search-terms/terms", "code": "36684363", "display": "Tree mixture 1" }
```

---

## 2. Distinguishing mold from house dust mite (answering the clinical question)

### Why it is hard
Both are **perennial indoor allergens** and both worsen with humidity. The questionnaire used to ask
a single mold question, "Are symptoms worse in the rainy season or in damp places?" — but **a
mite-allergic patient answers yes to that too**, because mite populations also grow when humidity
rises. One shared cue cannot separate the two.

### What was added
Questions that are specific to mold and are not explained by mites.

| Question | Options | Points to |
|---|---|---|
| **Which space makes it worse** (`mold_space`, multi) | Bathroom or shower / Basement, storage, old building / Near a leak or condensation stain / When the air conditioner or humidifier runs | **Mold** |
| | Around the bed, bedding or mattress | **Mite** |
| **Outdoor situations** (`mold_outdoor`, multi) | Leaf piles or mowing / Compost, soil, potting / Just after rain or a thunderstorm / Farm or hay | **Outdoor mold** (Alternaria, Cladosporium) |
| **Response to bedding measures** (`mite_bedding_trial`) | Improved / No change / Never tried | **Mite** |
| **Response to dehumidifying or mold removal** (`mold_dehum_trial`) | Improved / No change / Never tried | **Mold** |

The bedroom option and both intervention questions appear **only when mite is also positive**,
because that is exactly when discrimination matters.

### Decision rules (`_classify_mold`)
1. Any mold-specific cue (persistently wet space, outdoor spores, improvement after dehumidifying)
   → **confirmed as a cause**
2. No such cue, mite also positive, and only the shared "worse when damp" cue → **judgment withheld**
   ("House dust mites also increase with humidity, so this cue alone cannot distinguish them")
   - If only bedding measures helped and dehumidifying was never tried, it adds that mite is the
     more likely explanation
3. If mite is not positive, "worse when damp" is accepted as a cause, as before
4. An explicit no (not related to damp places, no such space, no change after dehumidifying)
   → **sensitized only**, with the note that the same indoor symptoms are explained by house dust
   mite when mite is positive

### Indoor vs outdoor mold
Avoidance advice is nearly opposite, so `mold_habitat()` branches it.

| Habitat | Antigens | Advice |
|---|---|---|
| Outdoor | Alternaria, Cladosporium, Fusarium | Avoid leaf, lawn and compost work; keep windows closed after rain and in late summer to autumn |
| Indoor | Aspergillus, Penicillium, Mucor, Candida | Humidity under 50%, repair leaks and condensation, clean air conditioner and humidifier filters |

`test_mold_vs_mite_discrimination` pins six scenarios: cannot-distinguish hold, outdoor cue confirms,
wet-space cue confirms, bedding-only improvement, dehumidifier improvement, and explicit no.

---

## 3. Card news: the classic UI keeps its original design and copy

`services/cardnews_classic.py` preserves the implementation as of commit `d5eb5f4`.
`/api/classify` now takes a `ui` parameter and routes accordingly.

| UI | Request | Card news |
|---|---|---|
| `/` quest | `ui: "quest"` | Dex theme: verdict stamps, category stamps, expedition narrative |
| `/classic/` | `ui: "classic"` | Original design: purple cover "나의 알레르기 검사 결과 요약", tag-style labels |

Verdicts, report and FHIR output are identical across both UIs. Only the card news differs.

---

## 4. Result-consultation chatbot

`POST /api/chat` · `services/result_chat_service.py` · **Result consultation** tab in both UIs.

### Grounding
Answers are built only from **this patient's verdicts, the knowledge base, and their questionnaire
answers**. The system prompt forbids content outside that context and requires the model to say the
result cannot answer when it does not.

Observed behavior:

| Question | Answer |
|---|---|
| How often should I wash bedding? | The knowledge-base avoidance rule verbatim: weekly, 55-60°C |
| Can I keep a cat? (cat not on the panel) | "This result has no information about cats … please ask your treating clinician" |
| Should I stop my antihistamine? | "This result cannot tell. Please ask your treating clinician." |
| I'm having trouble breathing right now | Bypasses the model and returns **emergency guidance** immediately (119, emergency department, epinephrine) |

### Guardrails
- No diagnosis, no prescriptions, no dose changes
- Breathing-difficulty and anaphylaxis phrasing is caught by regex first, in Korean, English and
  Chinese, and emergency guidance takes priority
- Every answer is marked as educational reference material

### Works without an API key
Five suggested questions (biggest priority, why this verdict, what "sensitized only" means, what to
watch, foods to be careful with) are answered **deterministically** from verdict data, with no LLM.
Df and Dp are counted once as a clinical group, and duplicate advice that differs only in wording is
filtered out.

---

## 5. Server-generated content in three languages (questionnaire, knowledge base, report, card news)

### Why not a dictionary
Screen chrome has a fixed string set, so the `web/i18n.js` dictionary is enough there. Server content
is 148 antigens times knowledge-base fields plus sentences assembled per patient, which a dictionary
cannot cover. It uses **LLM translation with a persistent cache**
(`services/translation_service.py`).

### How it works
| Target | Approach |
|---|---|
| Questionnaire questions, options, sections | Translate only `title/help/subtitle/label/hint` values in the JSON; identifiers and codes stay fixed |
| Personalized report | Translate the markdown **block by block**, split on blank lines, which keeps cache reuse high |
| Report HTML document | The body is already translated above, so only fixed chrome such as the cover and tiles goes through HTML translation |
| Card news HTML | Text nodes only |
| Dex card rationales | `rationale_ko` in `assessments` plus knowledge-base fields |

### Safeguards
- `lang=ko` **never enters** the translation path, so existing behavior and tests are unchanged
- With no API key the original Korean is returned rather than silently blanked
- `<style>` and `<script>` blocks are excluded, since translating Korean inside CSS comments breaks styling
- `<b>` → `**bold**` → `<b>` round-trip preserves emphasis without fragmenting sentences
- `<br>` becomes a validated placeholder; if it is lost in translation, **that block keeps the original**
- The prompt fixes numbers, units, Latin names and **personal names** as untranslatable
- A UI banner states that the text is machine-translated

### Performance and cost
A 702-entry cache ships with the repository so Korean, English and Chinese work immediately after
deployment. A cache hit answers the questionnaire in **0.0 s**. A cold first pass through the full
Chinese flow took about 40 s, and 0 s thereafter.

---

## 6. Open items
- Validate the Dockerfile build on the first Render deploy (no local Docker)
- ~~Decompose the mixture antigens into per-component SCTIDs~~ → **done**: decomposition is impossible because the component species live only in the panel insert. They now carry broader real SNOMED concepts, so 147 of 148 antigens have an SCTID and only the negative control falls back.
- Decide a retention policy for chat transcripts (the server is stateless today; history lives only in browser memory)
- Have a clinician review the machine-translated medical terms before freezing the cache
