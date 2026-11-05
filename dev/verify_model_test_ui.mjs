/** Verify exported model tests with mocked API responses; no real credentials/credits. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'web', 'out');
const shots = path.join(root, 'dev', '.verify-model-test');
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
    else if (pathname === '/api/model-catalog') data = catalog;
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
  await page.goto(url + '/models');
  await page.getByText('GLM Test', {exact: true}).waitFor();
  return {context, page};
}
try {
  const {context, page} = await prepare();
  const testButton = page.getByRole('button', {name: '测试 GLM Test', exact: true});
  await testButton.click();
  const dialog = page.getByRole('dialog');
  await dialog.getByText('测试成功', {exact: true}).waitFor();
  assert.deepEqual(requests.at(-1), {model: 'glm-test', realm: 'cn', stream: false, messages: [{role: 'user', content: 'Ciallo'}]});
  await dialog.getByText('Ciallo', {exact: true}).waitFor();
  await dialog.getByText('积分：0', {exact: true}).waitFor();
  await page.waitForTimeout(500); // allow the dialog's opening animation to settle
  await page.screenshot({path: path.join(shots, 'model-test-desktop.png'), fullPage: true});
  await dialog.getByRole('button', {name: '关闭', exact: true}).click();
  for (const [nextMode, message] of [['error200', 'Model quota exceeded'], ['httpError', 'No healthy public accounts'], ['empty', '上游未返回模型文本，请检查账号和模型可用性。']]) {
    mode = nextMode;
    await testButton.click();
    await dialog.getByRole('alert').getByText(message, {exact: true}).waitFor();
    await dialog.getByRole('button', {name: '关闭', exact: true}).click();
  }
  await page.getByRole('tab', {name: '国际版', exact: true}).click();
  mode = 'success';
  await testButton.click();
  await dialog.getByText('测试成功', {exact: true}).waitFor();
  assert.equal(requests.at(-1).realm, 'global');
  await dialog.getByRole('button', {name: '关闭', exact: true}).click();
  mode = 'slow';
  const before = requests.length;
  await testButton.evaluate(el => { el.click(); el.click(); });
  await dialog.getByText('正在发送 Ciallo，请稍候…', {exact: true}).waitFor();
  await dialog.getByRole('button', {name: '取消等待', exact: true}).click();
  await page.waitForTimeout(1100);
  assert.equal(requests.length, before + 1, 'double click must consume only one request');
  assert.equal(await page.getByRole('dialog').count(), 0, 'cancelled late reply must stay closed');
  await context.close();

  mode = 'success';
  const mobile = await prepare('admin', {width: 390, height: 844});
  await mobile.page.getByRole('button', {name: '测试 GLM Test', exact: true}).click();
  const mobileDialog = mobile.page.getByRole('dialog');
  await mobileDialog.getByText('测试成功', {exact: true}).waitFor();
  await mobile.page.waitForTimeout(500);
  const bounds = await mobileDialog.boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 391, 'dialog must fit mobile viewport: ' + JSON.stringify(bounds));
  assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 845, 'dialog must fit mobile height');
  await mobile.page.screenshot({path: path.join(shots, 'model-test-mobile.png'), fullPage: true});
  await mobile.context.close();

  const viewer = await prepare('viewer');
  assert.equal(await viewer.page.getByRole('button', {name: '测试 GLM Test', exact: true}).count(), 0);
  await viewer.context.close();
  assert.deepEqual(pageErrors, [], 'no browser runtime errors');
  console.log('PASS: fixed prompt, replies/credits, HTTP/200/empty failures, global realm, double-click, cancellation, mobile and viewer.');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
