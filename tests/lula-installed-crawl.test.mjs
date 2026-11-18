// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

// Execute the installed command with its real Commander and Octokit modules.
// Only HTTP transport is replaced; every unexpected request fails locally.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { join } from 'node:path';
import test from 'node:test';

const runtime = process.env.LULA_RUNTIME_DIR;
assert.ok(runtime, 'LULA_RUNTIME_DIR must identify a prepared crawler runtime');

const fixture = String.raw`
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const mode = process.env.LULA_FIXTURE_MODE;
const events = [];
const bot = {id:41898282,login:'github-actions[bot]',type:'Bot'};
const signature = '<!-- LULA_SIGNATURE:v1 -->\n## Lula Compliance Overview\n';
const uuid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const block = '<!-- @lulaStart '+uuid+' -->\nVALUE\n<!-- @lulaEnd '+uuid+' -->';
const expected = {repository:'owner/repo',baseSHA:'b'.repeat(40),headSHA:'a'.repeat(40),changedFiles:1};
process.env.OWNER='owner';process.env.REPO='repo';process.env.PULL_NUMBER='1';
process.env.GITHUB_TOKEN='fixture-token';process.env.LULA_PREFLIGHT_JSON=JSON.stringify(expected);
const reply = (data,status=200) => new Response(status===204?null:JSON.stringify(data),{
  status,headers:{'content-type':'application/json','x-github-request-id':'fixture'}});
let reads=0;
globalThis.fetch = async (input, options={}) => {
  const url=new URL(typeof input==='string'?input:input.url);
  const method=options.method||input.method||'GET';
  assert.equal(url.origin,'https://api.github.com','unexpected network origin');
  const route=method+' '+url.pathname;
  if(route==='GET /repos/owner/repo/pulls/1') {
    events.push('pr');reads++;
    return reply({state:'open',draft:false,changed_files:1,
      head:{ref:'topic',sha:mode==='race'&&reads>1?'c'.repeat(40):expected.headSHA,repo:{full_name:'owner/repo'}},
      base:{ref:'main',sha:expected.baseSHA}});
  }
  if(route==='GET /repos/owner/repo/pulls/1/files') {
    events.push('files');return reply([{filename:'mapped.xml',status:'modified'}]);
  }
  if(route==='GET /repos/owner/repo/contents/mapped.xml') {
    const ref=url.searchParams.get('ref');
    assert.ok([expected.baseSHA,expected.headSHA].includes(ref),'content must use checked SHA');
    const head=ref===expected.headSHA;events.push(head?'read:head':'read:base');
    if(mode==='read-failure'&&head)return reply({message:'Not Found'},404);
    const content=block.replace('VALUE',head&&mode!=='none'?'NEW':'OLD');
    return reply({content:Buffer.from(content).toString('base64'),encoding:'base64'});
  }
  if(route==='POST /repos/owner/repo/issues/1/comments') {
    events.push('create');
    const body=JSON.parse(options.body);
    assert.ok(body.body.startsWith(signature),'official comment signature is retained');
    assert.ok(body.body.includes(uuid),'changed mapping is retained');
    return reply({id:99,user:mode==='wrong-author'?{...bot,id:7}:bot},201);
  }
  if(route==='GET /repos/owner/repo/issues/1/comments') {
    const page=url.searchParams.get('page');events.push('list:'+page);
    return reply(page==='1'?[
      {id:1,user:bot,body:signature+'old'},
      {id:2,user:{id:7,login:'human',type:'User'},body:signature+'quoted'},
      {id:100,user:bot,body:signature+'newer'},
    ]:[]);
  }
  if(route==='DELETE /repos/owner/repo/issues/comments/1') {
    events.push('delete:1');return reply(null,204);
  }
  throw Error('Unexpected fixture request: '+route);
};
let failed=false;
process.argv=['node',process.env.LULA_FIXTURE_ENTRY,'--post-mode='+ (mode==='review'?'review':'comment')];
try {await import(pathToFileURL(process.env.LULA_FIXTURE_ENTRY));} catch {failed=true;}
console.log('LULA_FIXTURE_RESULT='+JSON.stringify({failed,events}));
`;

function run(mode) {
  const result = spawnSync(process.execPath, ['--input-type=module', '--eval', fixture], {
    encoding: 'utf8', timeout: 15_000,
    env: { ...process.env, LULA_FIXTURE_MODE: mode, LULA_FIXTURE_ENTRY: join(runtime, 'crawl.mjs') },
  });
  assert.equal(result.status, 0, result.stderr || String(result.error));
  const line = result.stdout.split('\n').find(value => value.startsWith('LULA_FIXTURE_RESULT='));
  assert.ok(line, result.stdout + result.stderr);
  return JSON.parse(line.slice('LULA_FIXTURE_RESULT='.length));
}

test('installed command posts the changed mapping before cleaning only its old bot comment', () => {
  const result = run('changed');
  assert.equal(result.failed, false);
  assert.deepEqual(result.events, ['pr', 'files', 'read:base', 'read:head', 'pr', 'create', 'list:1', 'list:2', 'delete:1']);
});

test('unchanged mapped content makes no comment mutation', () => {
  assert.deepEqual(run('none'), {failed:false,events:['pr','files','read:base','read:head']});
});

for (const mode of ['read-failure', 'race', 'review']) {
  test(`installed command refuses ${mode} without comment mutation`, () => {
    const result = run(mode);
    assert.equal(result.failed, true);
    assert.equal(result.events.some(event => /^(create|delete|list)/.test(event)), false);
  });
}

test('unexpected authenticated comment author leaves prior comments intact', () => {
  const result = run('wrong-author');
  assert.equal(result.failed, true);
  assert.ok(result.events.includes('create'));
  assert.equal(result.events.some(event => /^(delete|list)/.test(event)), false);
});
