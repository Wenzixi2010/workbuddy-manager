/** Exported UI + native downloads, with mock APIs and no production data. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'web/out');
const shots = path.join(root, 'dev/.verify-sqlite-export');
await mkdir(shots, {recursive:true});
const require = createRequire(path.join(root, 'web/package.json'));
const {chromium} = require(process.env.WB_PLAYWRIGHT_MODULE || 'playwright');
const mime = {'.html':'text/html', '.js':'text/javascript', '.css':'text/css', '.svg':'image/svg+xml', '.ico':'image/x-icon'};
const server = createServer(async(req,res) => {
  try {
    let pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    if (pathname.startsWith('/api/migration/sqlite/export/')) {
      res.writeHead(200, {'Content-Type':'application/vnd.sqlite3', 'Content-Disposition':'attachment; filename="workbuddy-manager-test.sqlite3"'});
      res.end(Buffer.from('SQLite format 3\0mock-browser-download')); return;
    }
    if (!path.extname(pathname)) pathname = pathname.replace(/\/$/,'') + '/index.html';
    const target = path.resolve(out, '.'+pathname);
    if(!target.startsWith(out+path.sep)) {res.writeHead(403).end();return;}
    res.setHeader('Content-Type', mime[path.extname(target)]||'application/octet-stream');
    res.end(await readFile(target));
  } catch {res.writeHead(404).end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,...(process.env.WB_CHROME_PATH?{executablePath:process.env.WB_CHROME_PATH}:{})});
let failure=false, requests=0, restoreCalls=0;
const restoreResult={format:'native',requires_overwrite:true,preview:true,counts:{accounts:1,valid_accounts:1,new_accounts:1,keys:1,valid_keys:1,new_keys:1,logs:1},warnings:['native restore test'],added_accounts:0,updated_accounts:0,added_keys:0,skipped_accounts:0,skipped_keys:0,imported_logs:0,skipped_logs:0};
const pageErrors=[];
const sqlite=Buffer.from('SQLite format 3\0mock-browser-download');
async function prepare(role='admin',viewport={width:1440,height:1000}) {
  const context=await browser.newContext({viewport,acceptDownloads:true});
  await context.addInitScript(()=>localStorage.setItem('workbuddy-manager:locale','zh-CN'));
  await context.route('**/api/**',async route=>{
    const pathname=new URL(route.request().url()).pathname;
    if(pathname.startsWith('/api/migration/sqlite/export/')) {
      await route.continue(); return;
    }
    let data={},status=200;
    if(pathname==='/api/me') data={username:'test',role};
    else if(pathname==='/api/upstreams') data={items:[]};
    else if(pathname==='/api/system/changelog') data={version:'test',items:[]};
    else if(pathname==='/api/migration/sqlite') {
      const preview=new URL(route.request().url()).searchParams.get('preview')==='true';
      if(!preview)restoreCalls++;
      data={...restoreResult,preview};
    }
    else if(pathname==='/api/migration/sqlite/export') {
      requests++;
      await new Promise(resolve=>setTimeout(resolve,300));
      if(failure){status=503;data={detail:'无法生成 SQLite 备份，请检查数据库和临时磁盘空间'};}
      else data={ticket:'mock-ticket',filename:'workbuddy-manager-test.sqlite3',expires_in:300};
    }
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  });
  const page=await context.newPage();
  page.on('pageerror',error=>pageErrors.push(String(error)));
  await page.goto(`http://127.0.0.1:${server.address().port}/migration/`);
  if(role==='admin') await page.getByRole('button',{name:'导出 SQLite',exact:true}).waitFor();
  return {page,context};
}
try {
  const desktop=await prepare();
  const button=desktop.page.getByRole('button',{name:'导出 SQLite',exact:true});
  await button.click();
  const confirm=desktop.page.getByRole('alertdialog');
  await confirm.getByText('确定导出管理端数据库？',{exact:true}).waitFor();
  const downloadPromise=desktop.page.waitForEvent('download');
  await confirm.getByRole('button',{name:'导出 SQLite',exact:true}).click();
  const download=await downloadPromise;
  assert.equal(download.suggestedFilename(),'workbuddy-manager-test.sqlite3');
  const saved=path.join(shots,'browser-download.sqlite3');
  await download.saveAs(saved);
  assert.deepEqual(await readFile(saved),sqlite);
  assert.equal(requests,1);
  await desktop.page.getByRole('alertdialog').waitFor({state:'hidden'});
  await desktop.page.screenshot({path:path.join(shots,'export-desktop.png'),fullPage:true});
  await desktop.page.locator('input[type=file]').setInputFiles({name:'restore.sqlite3',mimeType:'application/vnd.sqlite3',buffer:sqlite});
  await desktop.page.getByRole('button',{name:'预览数据',exact:true}).click();
  await desktop.page.getByRole('status').filter({hasText:'已识别当前管理端恢复备份'}).waitFor();
  assert.ok(await desktop.page.getByRole('button',{name:'确认导入',exact:true}).isDisabled(),'restore requires overwrite');
  await desktop.page.getByRole('checkbox').nth(0).check();
  await desktop.page.getByRole('button',{name:'预览数据',exact:true}).click();
  await desktop.page.getByRole('button',{name:'确认导入',exact:true}).click();
  const restoreDialog=desktop.page.getByRole('alertdialog');
  await restoreDialog.getByText('确定用备份替换当前业务数据？',{exact:true}).waitFor();
  await restoreDialog.getByRole('button',{name:'确认导入',exact:true}).click();
  await desktop.page.getByRole('heading',{name:'恢复完成，请启动上游并检查账号与权限',exact:true}).waitFor();
  assert.equal(restoreCalls,1);
  failure=true;
  await button.click();
  await confirm.getByRole('button',{name:'导出 SQLite',exact:true}).click();
  await desktop.page.getByRole('alert').getByText('无法生成 SQLite 备份，请检查数据库和临时磁盘空间',{exact:true}).waitFor();
  assert.equal(requests,2);
  await desktop.context.close();
  failure=false;
  const mobile=await prepare('admin',{width:390,height:844});
  await mobile.page.screenshot({path:path.join(shots,'export-mobile.png'),fullPage:true});
  const bounds=await mobile.page.getByRole('heading',{name:'导出可恢复 SQLite 备份',exact:true}).boundingBox();
  assert.ok(bounds.x>=0&&bounds.x+bounds.width<=391);
  await mobile.context.close();
  const viewer=await prepare('viewer');
  await viewer.page.getByText('仅管理员可导出、迁移或恢复数据。',{exact:true}).waitFor();
  assert.equal(await viewer.page.getByRole('button',{name:'导出 SQLite',exact:true}).count(),0);
  await viewer.context.close();
  assert.deepEqual(pageErrors,[]);
  console.log('PASS: admin confirmation, native SQLite download, preparation failure, native restore detection/confirmation, mobile, viewer permissions and browser runtime.');
} finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
