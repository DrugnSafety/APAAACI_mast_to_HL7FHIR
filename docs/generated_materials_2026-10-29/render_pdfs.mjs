// Render saved local HTML without calling an inference or remote service.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/mingyukang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.dirname(fileURLToPath(import.meta.url));
const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-component-update','--disable-features=OptimizationGuideModelDownloading,OptimizationHints,MediaRouter']});
const context=await browser.newContext({viewport:{width:1100,height:900}});
const blocked=[];
await context.route(/^https?:/,route=>{blocked.push(route.request().url());return route.abort()});
const jobs=manifest.records.flatMap(r=>['report','cardnews'].map(type=>({record:r,type})));
const results=[];
let cursor=0,finished=0;
async function worker(){
  const page=await context.newPage();
  while(cursor<jobs.length){
    const {record:r,type}=jobs[cursor++];
    const rel=`materials/${r.id}/${r.source}/${type}.pdf`;
    const html=path.join(root,`materials/${r.id}/${r.source}/${type}.html`);
    await page.goto(pathToFileURL(html).href,{waitUntil:'load'});
    await page.emulateMedia({media:'print',reducedMotion:'reduce'});
    await page.evaluate(()=>document.fonts.ready);
    if(type==='cardnews'){
      await page.addStyleTag({content:'@page{size:A4;margin:12mm}.card,.js .card{break-after:page;page-break-after:always}.card:last-child{break-after:auto;page-break-after:auto}body{padding:0!important}#research-preview-banner{position:static!important;font-size:10px!important}'});
    }
    await page.pdf({path:path.join(root,rel),format:'A4',printBackground:true,preferCSSPageSize:true,margin:{top:'12mm',bottom:'12mm',left:'12mm',right:'12mm'},tagged:true});
    const buffer=fs.readFileSync(path.join(root,rel));
    results.push({key:r.key,kind:type+'_pdf',label:type==='report'?'리포트 PDF':'카드뉴스 PDF',path:rel,mime:'application/pdf',bytes:buffer.length,sha256:crypto.createHash('sha256').update(buffer).digest('hex'),html_sha256:crypto.createHash('sha256').update(fs.readFileSync(html)).digest('hex')});
    finished++;
    if(finished%20===0)process.stdout.write(`Rendered ${finished}/${jobs.length} PDFs\n`);
  }
  await page.close();
}
try{
  await Promise.all([worker(),worker()]);
  fs.writeFileSync(path.join(root,'pdf_manifest.json'),JSON.stringify({generated_at:new Date().toISOString(),renderer:'Google Chrome / Playwright PDF',network_requests_blocked:blocked,files:results.sort((a,b)=>(a.key+a.kind).localeCompare(b.key+b.kind))},null,2)+'\n');
  process.stdout.write(`PDFs: ${results.length}, bytes: ${results.reduce((n,r)=>n+r.bytes,0)}, external attempts: ${blocked.length}\n`);
}finally{await browser.close()}
