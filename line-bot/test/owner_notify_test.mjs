import assert from 'node:assert/strict';
import { notifyOwnerOfBooking } from '../src/index.js';

const calls = [];
const fakeFetch = async (url, init) => { calls.push({ url, init }); return { ok: true, status: 200 }; };
const env = { OWNER_LINE_USER_ID: 'U' + '0'.repeat(32), LINE_CHANNEL_ACCESS_TOKEN: 'test-token' };
const ok = await notifyOwnerOfBooking(env, { company: '架空不動産', contact_name: '山田', industry: 'real_estate',
  email: 'a@example.invalid', label: '10/1(水) 10:00' }, fakeFetch);
assert.equal(ok, true);
assert.equal(calls[0].url, 'https://api.line.me/v2/bot/message/push');
const body = JSON.parse(calls[0].init.body);
assert.equal(body.to, env.OWNER_LINE_USER_ID);
assert.match(body.messages[0].text, /面談の申し込み/);
assert.match(body.messages[0].text, /架空不動産/);
// 設定が無ければ送らない(予約処理は止めない)
assert.equal(await notifyOwnerOfBooking({}, {}, fakeFetch), false);
// 送信失敗でも例外にしない
assert.equal(await notifyOwnerOfBooking(env, {}, async () => { throw new Error('network'); }), false);
console.log('owner notify tests passed');
