import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/mingyukang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.dirname(fileURLToPath(import.meta.url));
const coop=path.join(root,'../medical_information_cooperation_2026-10-29');
const out=path.join(root,'validation');fs.mkdirSync(out,{recursive:true});
const manifest=JSON.parse(fs.readFileSync(path.join(root,'document_manifest.json')));
const sources=manifest.documents.map(d=>({html:path.join(root,d.html),pdf:path.join(root,d.source.replace(/\.md$/,'.pdf'))}));
sources.push({html:path.join(coop,'index.html'),pdf:path.join(coop,'cooperation_request.pdf')});
const errors=[],external=[],checks=[];
function check(name,ok,detail=''){checks.push({name,pass:!!ok,detail});if(!ok)errors.push(name+': '+detail)}
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-component-update','--disable-features=OptimizationGuideModelDownloading,OptimizationHints,MediaRouter']});
const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
await context.route(/^https?:/,r=>{external.push(r.request().url());return r.abort()});
const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
try{
  const all=[{html:path.join(root,'../project_documentation_2026-10-29.html')},{html:path.join(root,'index.html')},...sources];
  for(const entry of all){
    await page.goto(pathToFileURL(entry.html).href,{waitUntil:'load'});
    const state=await page.evaluate(()=>({title:document.title,h1:document.querySelectorAll('h1').length,text:document.body.innerText.length,overflow:document.documentElement.scrollWidth-innerWidth,links:[...document.querySelectorAll('a[href]')].map(a=>a.getAttribute('href')),checked:document.querySelectorAll('input[type=checkbox]:checked').length}));
    check(path.basename(entry.html)+' title',state.title.includes('APAAACI'));
    check(path.basename(entry.html)+' single H1',state.h1===1,String(state.h1));
    check(path.basename(entry.html)+' desktop overflow',state.overflow<=2,String(state.overflow));
    check(path.basename(entry.html)+' no preselected consent',state.checked===0);
    for(const href of state.links){if(!href||/^(https?:|mailto:|tel:)/.test(href))continue;if(href.startsWith('#')){check('anchor '+href,await page.locator(`[id="${href.slice(1)}"]`).count()>0);continue}const p=path.resolve(path.dirname(entry.html),decodeURIComponent(href.split('#')[0]));check('file '+href,fs.existsSync(p),p);}
    if(entry.pdf && process.env.SKIP_PDF!=='1'){
      await page.emulateMedia({media:'print'});
      await page.pdf({path:entry.pdf,format:'A4',printBackground:true,preferCSSPageSize:true,tagged:true});
      const data=fs.readFileSync(entry.pdf);check('PDF '+path.basename(entry.pdf),data.subarray(0,4).toString()==='%PDF'&&data.length>5000);
      await page.emulateMedia({media:'screen'});
    }
    await page.setViewportSize({width:390,height:844});
    check(path.basename(entry.html)+' mobile overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
    await page.setViewportSize({width:1440,height:1000});
  }
  await page.goto(pathToFileURL(path.join(root,'index.html')).href);
  await page.locator('[data-filter-docs]').fill('소아');
  check('IRB filter',await page.locator('[data-doc]:not(.hidden)').count()>0&&await page.locator('[data-doc]:not(.hidden)').count()<12);
  await page.locator('[data-filter-docs]').fill('');
  check('IRB filter reset',await page.locator('[data-doc]:not(.hidden)').count()===12);
  await page.locator('.phase').nth(5).click();
  check('step interaction',await page.locator('#phase-note').innerText().then(s=>s.includes('동의와 수신자')));
  await page.screenshot({path:path.join(out,'irb_index.jpg'),type:'jpeg',quality:72});
  await page.goto(pathToFileURL(path.join(coop,'index.html')).href);
  await page.locator('[data-filter-toc]').fill('GPU');
  check('cooperation TOC search',await page.locator('.toc a:visible').count()>0&&await page.locator('.toc a:visible').count()<12);
  await page.locator('[data-filter-toc]').fill('');
  await page.screenshot({path:path.join(out,'cooperation_desktop.jpg'),type:'jpeg',quality:72});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(out,'cooperation_mobile.jpg'),type:'jpeg',quality:72});
  check('external network attempts',external.length===0,JSON.stringify(external));
}finally{await browser.close();}
const result={date:new Date().toISOString(),checks:checks.length,failures:errors.length,errors,external_requests:external,results:checks,pdf_source:'HTML print layout',word_direct_render:false};
fs.writeFileSync(path.join(out,'browser_validation.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify({checks:checks.length,failures:errors.length,errors,external_attempts:external.length}));
if(errors.length)process.exitCode=1;
