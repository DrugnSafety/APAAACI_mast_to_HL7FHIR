#!/usr/bin/env python3
"""의료인용 데모 + 환자용 도구를 한 파일로 묶는 통합 페이지 빌드.

두 앱은 ID·클래스가 충돌하므로 srcdoc 내장 프레임으로 각자 격리해 싣고,
상단 탭으로 전환한다. 원본 두 파일을 수정하면 이 스크립트를 다시 실행해 재생성한다.
"""
import pathlib

BASE = pathlib.Path(__file__).parent
pro = (BASE / "prediction-tool-demo.html").read_text(encoding="utf-8")
pt = (BASE / "prediction-tool-patient.html").read_text(encoding="utf-8")

def srcdoc(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;")

wrapper = """<title>PACEN 치료 결정 지원</title>
<style>
  :root {
    --ground:#F7FAF9; --surface:#FFFFFF; --ink:#182A2D; --ink-soft:#47595C;
    --line:#D5E0DE; --accent:#0F6E73; --accent-soft:#E1EEED;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --ground:#0F1719; --surface:#162124; --ink:#D8E4E4; --ink-soft:#A8BBBC;
      --line:#2A3B3E; --accent:#58B7B4; --accent-soft:#143A3C;
    }
  }
  :root[data-theme="dark"] {
    --ground:#0F1719; --surface:#162124; --ink:#D8E4E4; --ink-soft:#A8BBBC;
    --line:#2A3B3E; --accent:#58B7B4; --accent-soft:#143A3C;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--ground); color:var(--ink);
    font-family:"Noto Sans KR",-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",Pretendard,"Malgun Gothic",sans-serif; }
  .topbar { position:sticky; top:0; z-index:10; display:flex; align-items:center; justify-content:space-between;
    gap:12px; flex-wrap:wrap; padding:10px 18px; background:var(--surface); border-bottom:1px solid var(--line); }
  .brand { font-size:14px; font-weight:800; letter-spacing:-.01em; }
  .brand small { font-weight:600; color:var(--ink-soft); margin-left:8px; font-size:11.5px; }
  .tabs { display:flex; gap:6px; }
  .tabs button { font:inherit; font-size:13px; font-weight:700; cursor:pointer; color:var(--ink-soft);
    background:var(--ground); border:1px solid var(--line); border-radius:999px; padding:7px 16px; min-height:36px; }
  .tabs button.on { background:var(--accent); border-color:var(--accent); color:var(--surface); }
  iframe { display:block; width:100%; height:calc(100vh - 57px); border:none; background:var(--ground); }
</style>
<div class="topbar">
  <div class="brand">PACEN 치료 결정 지원<small>약제 선택 지원 · 통합 데모</small></div>
  <div class="tabs" role="tablist" aria-label="이용자 유형">
    <button type="button" id="tab-pro" class="on" role="tab" aria-selected="true">의료인용</button>
    <button type="button" id="tab-pt" role="tab" aria-selected="false">환자용</button>
  </div>
</div>
<iframe id="fr-pro" title="의료인용 약제 선택 지원" srcdoc="__PRO__"></iframe>
<iframe id="fr-pt" title="환자용 나에게 맞는 치료 알아보기" style="display:none" srcdoc="__PT__"></iframe>
<script>
(function () {
  "use strict";
  var pro = document.getElementById("tab-pro"), pt = document.getElementById("tab-pt");
  var frPro = document.getElementById("fr-pro"), frPt = document.getElementById("fr-pt");
  function show(which) {
    var isPro = which === "pro";
    frPro.style.display = isPro ? "block" : "none";
    frPt.style.display = isPro ? "none" : "block";
    pro.classList.toggle("on", isPro); pt.classList.toggle("on", !isPro);
    pro.setAttribute("aria-selected", isPro ? "true" : "false");
    pt.setAttribute("aria-selected", isPro ? "false" : "true");
    try { localStorage.setItem("pacen-view", which); } catch (e) {}
    if (location.hash !== "#" + which) { try { history.replaceState(null, "", "#" + which); } catch (e) {} }
  }
  pro.addEventListener("click", function () { show("pro"); });
  pt.addEventListener("click", function () { show("pt"); });
  var init = "pro";
  if (location.hash === "#pt") init = "pt";
  else if (location.hash !== "#pro") { try { init = localStorage.getItem("pacen-view") === "pt" ? "pt" : "pro"; } catch (e) {} }
  show(init);
  // 뷰어의 명시적 라이트/다크 선택을 내장 프레임에 전파 (시스템 설정은 프레임이 자체 감지)
  function syncTheme() {
    var t = document.documentElement.getAttribute("data-theme");
    [frPro, frPt].forEach(function (fr) {
      try {
        var de = fr.contentDocument && fr.contentDocument.documentElement;
        if (!de) return;
        if (t) de.setAttribute("data-theme", t); else de.removeAttribute("data-theme");
      } catch (e) {}
    });
  }
  new MutationObserver(syncTheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  [frPro, frPt].forEach(function (fr) { fr.addEventListener("load", syncTheme); });
})();
</script>
"""

out = wrapper.replace("__PRO__", srcdoc(pro)).replace("__PT__", srcdoc(pt))
# 스크립트 닫힘 보정: 위 템플릿의 </script> 유지 확인용 마지막 라인
if "</script>" not in out[-200:]:
    out += "\n"
(BASE / "prediction-tool-unified.html").write_text(out, encoding="utf-8")
print("wrote prediction-tool-unified.html:", len(out), "bytes")
