// 既存の営業Webhookを維持し、本人の明示的なテストだけをSaaS一次受けへ渡す。
export const OWNER_TEST_PREFIX = "[TSUNAGUMO-TEST]";

export async function forwardOwnerTest(env, event) {
  if (!env.SAAS_TEST_LINE_USER_ID || event.source?.type !== "user" ||
      event.source.userId !== env.SAAS_TEST_LINE_USER_ID ||
      event.type !== "message" || event.message?.type !== "text" ||
      !event.message.text?.startsWith(OWNER_TEST_PREFIX)) return false;

  // 本人のテストは設定不備でも営業AIに流さない。
  if (!/^U[0-9a-f]{32}$/.test(env.SAAS_TEST_LINE_USER_ID) ||
      !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(env.SAAS_TEST_TENANT_ID || "") ||
      !env.LINE_CHANNEL_SECRET || !event.webhookEventId || !event.replyToken) {
    throw new Error("Owner test configuration is incomplete");
  }
  const url = new URL(`/webhooks/line/${env.SAAS_TEST_TENANT_ID}`, env.SAAS_BASE_URL);
  if (url.protocol !== "https:") throw new Error("Owner test requires HTTPS");
  const text = event.message.text.slice(OWNER_TEST_PREFIX.length).trim();
  if (!text) return true;
  // 他の人の同梱イベント・会話履歴はSaaSに送らない。抽出した本文を改めて署名する。
  const body = JSON.stringify({events: [{...event, message: {...event.message, text}}]});
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", encoder.encode(env.LINE_CHANNEL_SECRET),
    {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
  const signature = btoa(String.fromCharCode(...new Uint8Array(
    await crypto.subtle.sign("HMAC", key, encoder.encode(body)))));
  const response = await fetch(url, {
    method: "POST", headers: {"Content-Type": "application/json", "X-Line-Signature": signature},
    body, redirect: "error", signal: AbortSignal.timeout(8000),
  });
  if (!response.ok) throw new Error(`Owner test forwarding failed: ${response.status}`);
  return true;
}
