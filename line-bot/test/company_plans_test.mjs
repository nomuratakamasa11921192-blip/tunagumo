import assert from 'node:assert/strict';
import {test} from 'node:test';
import worker from '../src/index.js';

async function activate({plan='basic', amount=39800, tax='inclusive', status='awaiting_trial', savedPlan='basic'} = {}) {
  let record = {company:'架空不動産', email:'test@example.invalid', status, plan:savedPlan};
  const calls = [];
  const env = {ADMIN_SECRET:'test', STRIPE_SECRET_KEY:'test', STRIPE_PRICE_BASIC:'price_basic',
    STRIPE_PRICE_BUSINESS:'price_business', STRIPE_PRICE_LIGHT:'price_legacy',
    SAAS_BASE_URL:'https://saas.example.invalid', SAAS_ADMIN_API_KEY:'test',
    CHAT_HISTORY:{get:async()=>JSON.stringify(record), put:async(_,s)=>{record=JSON.parse(s);}}};
  globalThis.fetch = async (url, options={}) => {
    calls.push({url:String(url), ...options});
    let data;
    if (String(url).includes('/prices/')) data={active:true, currency:'jpy', unit_amount:amount,
      tax_behavior:tax, recurring:{interval:'month', interval_count:1}};
    else if (String(url).includes('/payment_methods')) data={data:[{id:'pm_test'}]};
    else if (String(url).endsWith('/subscriptions')) data={id:'sub_test'};
    else if (String(url).endsWith('/admin/tenants')) data={tenant_id:'tenant_test', api_key:'test', email_sent:false};
    else throw new Error('Unexpected request');
    return new Response(JSON.stringify(data),{status:200,headers:{'content-type':'application/json'}});
  };
  const response = await worker.fetch(new Request('https://worker.example.invalid/admin/activate', {
    method:'POST',body:new URLSearchParams({key:'test',customer_id:'cus_test',plan})}),env,{});
  return {response,calls,record};
}

for (const [plan,amount] of [['basic',39800],['business',59800]]) {
  test(`${plan}:税込価格・14日・請求と会社枠が一致する`, async()=>{
    const {response,calls,record}=await activate({plan,amount});
    assert.equal(response.status,200);
    const payment=calls.find(c=>c.url.endsWith('/subscriptions'));
    const params=new URLSearchParams(payment.body);
    assert.equal(params.get('items[0][price]'),`price_${plan}`);
    assert.equal(params.get('trial_period_days'),'14');
    assert.equal(payment.headers['Idempotency-Key'],'tsunagumo-trial-cus_test');
    assert.equal(JSON.parse(calls.find(c=>c.url.endsWith('/admin/tenants')).body).plan,plan);
    assert.equal(record.plan,plan);
  });
}
for (const overrides of [{amount:98000},{tax:'exclusive'},{plan:'unknown'},{status:'trial_active'}]) {
  test(`不整合を請求前に拒否 ${JSON.stringify(overrides)}`,async()=>{
    const {response,calls}=await activate(overrides);
    assert.equal(response.status,500);
    assert.ok(!calls.some(c=>c.url.endsWith('/subscriptions')));
  });
}
test('プラン未記録の旧予約はライトを維持する',async()=>{
  const {response,calls}=await activate({plan:'',savedPlan:null});
  assert.equal(response.status,200);
  assert.equal(JSON.parse(calls.find(c=>c.url.endsWith('/admin/tenants')).body).plan,'light');
});
