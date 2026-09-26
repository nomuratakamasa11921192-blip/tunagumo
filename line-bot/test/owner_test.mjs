import assert from 'node:assert/strict';
import {createHmac} from 'node:crypto';
import {test} from 'node:test';
import worker from '../src/index.js';
import {forwardOwnerTest, OWNER_TEST_PREFIX} from '../src/owner-test.js';

const env = {SAAS_TEST_LINE_USER_ID:'U'+'1'.repeat(32),
  SAAS_TEST_TENANT_ID:'11111111-2222-3333-4444-555555555555',
  SAAS_BASE_URL:'https://saas.example.invalid', LINE_CHANNEL_SECRET:'test-secret'};
const event = {type:'message',source:{type:'user',userId:env.SAAS_TEST_LINE_USER_ID},
  webhookEventId:'event-1',replyToken:'test-reply',
  message:{type:'text',text:OWNER_TEST_PREFIX+' 内見できますか？'}};

test('本人のテストだけを抽出して署名し、SaaSへ渡す',async()=>{
  let sent;
  globalThis.fetch=async(url,options)=>{sent={url:String(url),...options};return new Response('{}');};
  assert.equal(await forwardOwnerTest(env,event),true);
  assert.equal(sent.url,`${env.SAAS_BASE_URL}/webhooks/line/${env.SAAS_TEST_TENANT_ID}`);
  assert.equal(sent.headers['X-Line-Signature'],createHmac('sha256',env.LINE_CHANNEL_SECRET).update(sent.body).digest('base64'));
  const events=JSON.parse(sent.body).events;
  assert.equal(events.length,1);
  assert.equal(events[0].message.text,'内見できますか？');
  assert.equal(events[0].webhookEventId,'event-1');
  assert.equal(event.message.text,OWNER_TEST_PREFIX+' 内見できますか？');
  assert.equal(sent.redirect,'error');
});

for (const [name,change] of [
  ['別人',{source:{type:'user',userId:'U'+'2'.repeat(32)}}],
  ['グループ',{source:{type:'group',userId:env.SAAS_TEST_LINE_USER_ID}}],
  ['通常の営業相談',{message:{type:'text',text:'予約したい'}}],
  ['途中にあるテスト文字',{message:{type:'text',text:'Re: '+OWNER_TEST_PREFIX+' 内容'}}],
  ['画像',{message:{type:'image'}}],
]) test(`${name}をテスト先に転送しない`,async()=>{
  globalThis.fetch=async()=>{throw new Error('Unexpected send');};
  assert.equal(await forwardOwnerTest(env,{...event,...change}),false);
});

test('未設定は無効、設定不備と転送失敗は営業にフォールバックしない',async()=>{
  assert.equal(await forwardOwnerTest({},event),false);
  await assert.rejects(forwardOwnerTest({...env,SAAS_TEST_TENANT_ID:''},event),/incomplete/);
  await assert.rejects(forwardOwnerTest({...env,SAAS_BASE_URL:'http://saas.example.invalid'},event),/HTTPS/);
  globalThis.fetch=async()=>new Response('',{status:503});
  await assert.rejects(forwardOwnerTest(env,event),/503/);
});

test('偽署名の外部Webhookは転送されない',async()=>{
  globalThis.fetch=async()=>{throw new Error('Unexpected send');};
  const response=await worker.fetch(new Request('https://worker.example.invalid/',{
    method:'POST',headers:{'x-line-signature':'invalid'},body:JSON.stringify({events:[event]})}),env,{});
  assert.equal(response.status,401);
});

test('正しい署名の混在Webhookでも、通常相談は営業処理を継続する',async()=>{
  const calls=[];
  globalThis.fetch=async(url,options)=>{
    const address=String(url);
    calls.push({url:address,...options});
    if(address.includes('api.openai.com')) return Response.json({choices:[{message:{role:'assistant',content:'営業の回答'}}]});
    return Response.json({});
  };
  const other={...event,webhookEventId:'event-2',replyToken:'other-reply',
    source:{type:'user',userId:'U'+'2'.repeat(32)},message:{type:'text',text:'予約したい'}};
  const body=JSON.stringify({events:[event,other]});
  const jobs=[];
  const response=await worker.fetch(new Request('https://worker.example.invalid/',{
    method:'POST',headers:{'x-line-signature':createHmac('sha256',env.LINE_CHANNEL_SECRET).update(body).digest('base64')},body}),
    {...env,OPENAI_MODEL:'test-model',CHAT_HISTORY:{get:async()=>null,put:async()=>{}}},
    {waitUntil:job=>jobs.push(job)});
  assert.equal(response.status,200);
  await Promise.all(jobs);
  const forwarded=calls.filter(c=>c.url.includes('/webhooks/line/'));
  assert.equal(forwarded.length,1);
  assert.equal(JSON.parse(forwarded[0].body).events[0].source.userId,env.SAAS_TEST_LINE_USER_ID);
  assert.equal(calls.filter(c=>c.url.includes('api.openai.com')).length,1);
  const replies=calls.filter(c=>c.url.includes('api.line.me'));
  assert.equal(replies.length,1);
  assert.equal(JSON.parse(replies[0].body).replyToken,'other-reply');
});
