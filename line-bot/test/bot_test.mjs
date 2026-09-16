// 営業用LINEボットの応答ロジックの検証(2026-09-16、AnthropicからOpenAIへ移行した際に追加)。
// 実際のOpenAI・Googleカレンダーには接続せず、fetchを差し替えて確認する。
// 実行: cd line-bot/test && node bot_test.mjs
import { callAssistant, TOOLS } from "../src/index.js";

let calls = [];
function stubOpenAI(responses) {
  let i = 0;
  globalThis.fetch = async (url, options) => {
    if (String(url).includes("openai.com")) {
      calls.push(JSON.parse(options.body));
      return { ok: true, json: async () => responses[i++] };
    }
    // Googleカレンダー等は失敗させる(ツール実行のエラー処理を通す)
    return { ok: false, status: 500, text: async () => "stub" };
  };
}
const env = { OPENAI_API_KEY: "sk-test", OPENAI_MODEL: "gpt-5.6-terra" };

// 1) ツールを使わない通常応答
calls = [];
stubOpenAI([{ choices: [{ message: { role: "assistant", content: "  ご相談ありがとうございます。  " } }] }]);
let r = await callAssistant(env, [{ role: "user", content: "料金は？" }], "https://example.com/reserve");
console.assert(r.replyText === "ご相談ありがとうございます。", "通常応答の本文", r.replyText);
console.assert(calls[0].messages[0].role === "system", "systemプロンプトが先頭");
console.assert(calls[0].messages[1].content === "料金は？", "ユーザー発言が渡る");
console.assert(calls[0].tools[0].type === "function", "ツール定義の形式");
console.assert(r.messages.length === 2, "履歴に応答が積まれる", r.messages.length);
console.log("OK: 通常応答");

// 2) ツール呼び出し → 結果を渡して2回目の応答
calls = [];
stubOpenAI([
  { choices: [{ message: { role: "assistant", content: null, tool_calls: [{ id: "call_1", type: "function", function: { name: "get_available_slots", arguments: "{}" } }] } }] },
  { choices: [{ message: { role: "assistant", content: "候補をご案内します。" } }] },
]);
r = await callAssistant(env, [{ role: "user", content: "日程を決めたい" }], "https://example.com/reserve");
console.assert(r.replyText === "候補をご案内します。", "ツール後の応答", r.replyText);
const second = calls[1].messages;
const toolMsg = second.find((m) => m.role === "tool");
console.assert(toolMsg && toolMsg.tool_call_id === "call_1", "ツール結果がtool_call_idつきで返る", JSON.stringify(toolMsg));
console.log("OK: ツール呼び出しの往復");

// 3) APIエラー時は謝罪文で終わる(履歴は壊さない)
calls = [];
globalThis.fetch = async () => ({ ok: false, status: 429, text: async () => "rate limited" });
r = await callAssistant(env, [{ role: "user", content: "こんにちは" }], "https://example.com/reserve");
console.assert(r.replyText.includes("申し訳ございません"), "エラー時の文面", r.replyText);
console.log("OK: APIエラー時");
console.log("ALL_BOT_TESTS_PASSED");
