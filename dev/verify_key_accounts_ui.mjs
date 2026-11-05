/** Verify exported model tests with mocked API responses; no real credentials/credits. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'web', 'out');
const shots = path.join(root, 'dev', '.verify-key-accounts');
const require = createRequire(path.join(root, 'web', 'package.json'));
const {chromium} = require(process.env.WB_PLAYWRIGHT_MODULE || 'playwright');
await mkdir(shots, {recursive: true});
const mime = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon'};
const server = createServer(async (req, res) => {
  try {
    let pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    if (pathname === '/') pathname = '/index.html';
    if (!path.extname(pathname)) pathname = pathname.replace(/\/$/, '') + '/index.html';
    const target = path.resolve(out, '.' + pathname);
    if (!target.startsWith(out + path.sep)) { res.writeHead(403).end(); return; }
    res.setHeader('Content-Type', mime[path.extname(target)] || 'application/octet-stream');
    res.end(await readFile(target));
  } catch { res.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const url = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({headless: true, ...(process.env.WB_CHROME_PATH ? {executablePath: process.env.WB_CHROME_PATH} : {})});
const pageErrors = [];
const requests = [];
let mode = 'success';
const model = {id: 'glm-test', name: 'GLM Test', context_length: 131072, max_output_tokens: 32768, efforts: [], default_effort: '', supports_images: false, series: '智谱 GLM'};
const catalog = {models: [model, {...model, id: 'reasoning-test', name: 'Reasoning Test'}], source: 'tencent', source_label: '腾讯模型接口', via: 'mock', errors: [], cached: false, summary: {total: 2, reasoning: 0, large_context: 2, max_context: 131072, series: ['智谱 GLM'], unique_ids: 2}};
const entry=(id,name,flags={})=>({id,uid:id.split(':')[1],name,realm:'cn',exists:true,disabled:false,expired:false,invalid:false,...flags});
const keyBase={prefix:'wbk_example',enabled:true,expires_at:null,max_ips:0,ip_allowlist:[],models:[],realm:'cn',quota:0,used_tokens:0,quota_credit:0,used_credit:0,upstream_id:null,packet_id:null,created_at:1700000000,last_used_at:null};
const alice=entry('cn:alice','Alice note');
const blocked=entry('cn:blocked','Blocked account');
const missing=entry('cn:gone','gone',{exists:false});
const keyRows=[
 {...keyBase,id:1,name:'Scoped key',account_ids:['cn:alice','cn:blocked','cn:gone'],bind_mode:'exclusive',account_access:{status:'ok',bound:[alice,blocked,missing],usable:[alice],upstream_enabled:true}},
 {...keyBase,id:2,name:'Public key',account_ids:[],bind_mode:'whitelist',account_access:{status:'ok',bound:[],usable:[entry('cn:public','Public account')],upstream_enabled:true}},
 {...keyBase,id:3,name:'Unreadable key',account_ids:[],account_access:{status:'unreadable',bound:[],usable:[],upstream_enabled:true}},
];
async function prepare(role='admin', viewport={width: 1440, height: 1000}) {
  const context = await browser.newContext({viewport});
  await context.addInitScript(() => {
    localStorage.setItem('workbuddy-manager:locale', 'zh-CN');
    localStorage.setItem('wb.whats-new.seen', 'test');
  });
  await context.route('**/api/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    let data = {};
    let status = 200;
    if (pathname === '/api/me') data = {username: role === 'admin' ? 'admin' : 'guest', role};
    else if (pathname === '/api/keys') data = keyRows;
    else if (pathname === '/api/upstreams') data = {items:[]};
    else if (pathname === '/api/model-catalog') data = catalog;
    else if (pathname === '/api/accounts') data = {manageable:true,accounts:[{account_id:'cn:alice',uid:'alice',nickname:'Alice account',realm:'cn',file:'workbuddy-alice.json',is_expired:false,disabled_by_panel:false,expires_at:4102444800},{account_id:'global:bob',uid:'bob',nickname:'Bob global',realm:'global',file:'workbuddy-bob.json',is_expired:false,disabled_by_panel:false,expires_at:4102444800}]};
    else if (pathname === '/api/system/changelog') data = {version: 'test', items: []};
    else if (pathname === '/api/playground/chat') {
      requests.push(route.request().postDataJSON());
      const requestMode = mode;
      if (requestMode === 'slow') await new Promise(resolve => setTimeout(resolve, 800));
      if (requestMode === 'httpError') { status = 503; data = {error: {message: 'No healthy public accounts'}}; }
      else if (requestMode === 'error200') data = {error: {message: 'Model quota exceeded'}};
      else if (requestMode === 'empty') data = {choices: [{message: {content: ''}}]};
      else data = {choices: [{message: {content: 'Ciallo～ Hello from the tested model!'}}], usage: {total_tokens: 12, credit: 0}};
    }
    try { await route.fulfill({status, contentType: 'application/json', body: JSON.stringify(data)}); } catch { /* closed request */ }
  });
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(String(error)));
  await page.goto(url + '/keys');
  await page.getByText('Scoped key', {exact: true}).waitFor();
  return {context, page};
}
try {
 const {page,context}=await prepare();
 await page.getByRole('columnheader',{name:'绑定账号',exact:true}).waitFor();
 await page.getByRole('columnheader',{name:'可使用账号',exact:true}).waitFor();
 const row=page.getByRole('row').filter({hasText:'Scoped key'});
 const cells=row.getByRole('cell');
 assert.ok((await cells.nth(4).innerText()).includes('Alice note'));
 assert.ok((await cells.nth(4).innerText()).includes('Blocked account'));
 await cells.nth(4).getByText('展开其余 1 个',{exact:true}).click();
 await cells.nth(4).getByText('账号已删除或未读取',{exact:true}).waitFor();
 assert.ok((await cells.nth(5).innerText()).includes('Alice note'));
 assert.ok(!(await cells.nth(5).innerText()).includes('Blocked account'));
 await page.getByRole('row').filter({hasText:'Public key'}).getByText('未绑定 · 公共账号池',{exact:true}).waitFor();
 await page.getByRole('row').filter({hasText:'Unreadable key'}).getByText('账号目录读取失败，请检查后刷新',{exact:true}).waitFor();
 await page.screenshot({path:path.join(shots,'keys-desktop.png'),fullPage:true});
 await context.close();
 const mobile=await prepare('viewer',{width:390,height:844});
 assert.equal(await mobile.page.getByRole('row').filter({hasText:'Scoped key'}).getByRole('cell').count(),10);
 await mobile.page.screenshot({path:path.join(shots,'keys-mobile.png'),fullPage:true});
 await mobile.context.close();
 assert.deepEqual(pageErrors,[]);
 console.log('PASS: bound/usable columns, account names, missing expansion, public pool, unreadable state, viewer/mobile and no runtime errors.');
} finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
