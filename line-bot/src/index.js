const ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages";
const LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply";
const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";
const GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3";
const STRIPE_API = "https://api.stripe.com/v1";
const TRIAL_DAYS = 14;
const BOOKING_TTL_SECONDS = 60 * 60 * 24 * 60;
const MAX_HISTORY_MESSAGES = 20;
const HISTORY_TTL_SECONDS = 60 * 60 * 24 * 30;
const MAX_TOOL_TURNS = 4;
const SLOT_MINUTES = 30;
// 2026-08-29変更: 野村さんがまだ高校生で平日は学校があるため、曜日ごとに営業時間を分ける。
const BUSINESS_HOUR_START_WEEKDAY = 16;
const BUSINESS_HOUR_START_WEEKEND = 14;
const BUSINESS_HOUR_END = 19;

// 2026年の祝日(振替休日含む)。今後の年も必要になったら追記する。
// 秋分の日等、天体の運行で決まる祝日は前年2月の官報公示が正式だが、ここでは通例の日付を採用。
const JP_HOLIDAYS_2026 = new Set([
  "2026-01-01", // 元日
  "2026-01-12", // 成人の日
  "2026-02-11", // 建国記念の日
  "2026-02-23", // 天皇誕生日
  "2026-03-20", // 春分の日
  "2026-04-29", // 昭和の日
  "2026-05-03", // 憲法記念日
  "2026-05-04", // みどりの日
  "2026-05-05", // こどもの日
  "2026-05-06", // 振替休日(5/3が日曜のため)
  "2026-07-20", // 海の日
  "2026-08-11", // 山の日
  "2026-09-21", // 敬老の日
  "2026-09-22", // 秋分の日
  "2026-10-12", // スポーツの日
  "2026-11-03", // 文化の日
  "2026-11-23", // 勤労感謝の日
]);

function isJpHoliday(y, m, dd) {
  const key = `${y}-${String(m + 1).padStart(2, "0")}-${String(dd).padStart(2, "0")}`;
  return JP_HOLIDAYS_2026.has(key);
}
const LOOKAHEAD_DAYS_CHAT = 11;
const LOOKAHEAD_DAYS_PAGE = 21;
const MAX_SLOTS_SHOWN_CHAT = 5;
// 2026-08-31: 不動産に一本化。SaaS本体(saas/)側もsrc/api/deps.pyのINDUSTRIESが
// ["real_estate"]のみになっている。他業種のマッピングは削除せず、横展開時に
// 復元しやすいようgit履歴に残す形にした。
const SAAS_INDUSTRIES = ["real_estate"];
const SAAS_INDUSTRY_LABELS_JP = {
  real_estate: "不動産",
};

const SYSTEM_PROMPT = `あなたは「ツナグモ」というAI SaaSの相談窓口として、LINEでお客様に応答するアシスタントです。

【ツナグモとは】
不動産会社向けに、物件紹介文・チラシ・オーナー様への報告文などの資料作成を支援するAI SaaSです。資料作成にかかる時間を、月20〜40時間の削減が見込めます。運営は野村隆真(埼玉県春日部市)。

【料金】
・月5万円の一律プラン(作成本数の制限なし)
・文章生成に使うAI(Anthropic社)の利用料は、月額費用には含まれません。お客様ご自身でAnthropic社と契約し、その利用料をお客様が直接お支払いいただきます(初期設定はこちらで代行するので、お客様ご自身で複雑な設定をする必要はありません)
・初期費用なし。最低契約期間なし(解約は1ヶ月前にご連絡)
・初期設定代行(30分)、業界の禁止表現チェック、承認フローと履歴、月2〜3回までの調整依頼が含まれます

【トライアル】
・オンライン相談(初期設定)の後から2週間無料。トライアル開始にはクレジットカードのご登録が必要です(トライアル期間中の課金はありません。期間終了後、継続する場合はそのまま自動でお支払いが始まります)
・流れ：LINEで相談日程を予約→30分オンラインで初期設定→その日から2週間トライアル開始
・初期設定では、サービス内容・価格帯・普段の文章のトーンを伺います

【話し方のルール】
・「絶対」「必ず」「100%」「国内完結」は使わない
・AIを主語にしない。何ができるようになるか(時間・手間)を主語にする
・「誰でも使えます」ではなく「AIの知識は不要です」と言う
・法令・禁止表現チェックについて聞かれたら、「検出は補助機能であり、法令適合を保証するものではなく、最終確認は貴社にお願いしている」旨を必ず伝える
・文章生成にはAnthropic社の海外APIを使っている旨を聞かれたら伝える。個人情報を含む内容の入力は控えてもらうよう伝える
・断定しすぎず、丁寧で落ち着いた敬語で話す。売り込みすぎない

【オンライン相談の予約対応】
・打ち合わせ・オンライン相談・Zoom相談を希望されたら、まず「カレンダーから、ご都合の良い日時をお選びいただけます」と伝えて予約ページのURLを案内してください: ${"{{RESERVE_URL}}"}
・チャットの中で日時を決めたいと言われた場合のみ、get_available_slots ツールで候補を確認し、番号付きで提示してください(最大5件)
・その場合、book_slot を呼ぶ前に、会社名・お名前・メールアドレスを必ず伺ってください(電話番号・現在お困りのことは任意)。これらが揃っていないまま book_slot を呼ばないこと
・start_iso と end_iso は get_available_slots が返した値をそのまま使うこと。自分で時刻を作らないこと
・book_slot が成功したら、確定した日時とZoomのURLをそのまま案内してください。日程はGoogleカレンダーに登録されます
・候補が1件も無い場合は、その旨を伝え、予約ページから改めて確認してもらうよう案内してください
・ツールがエラーを返した場合は、正直に「うまく予約できませんでした。野村が確認します」と伝えてください

【相談対応】
・料金や機能についての質問には自由に答えてください
・わからないこと、即答できない込み入った内容は、正直に「その点は野村が直接お答えします」と伝えてください
・返信は短く、LINEのチャットらしい自然な長さにしてください(長文は避ける)`;

const TOOLS = [
  {
    name: "get_available_slots",
    description:
      "Googleカレンダーの空き時間を確認し、直近の相談可能な候補日時(最大5件)を返す。ユーザーがチャットの中で日時を決めたいと言ったときに呼ぶ。",
    input_schema: { type: "object", properties: {}, required: [] },
  },
  {
    name: "book_slot",
    description:
      "指定した日時でオンライン相談の予定をGoogleカレンダーに作成し、Zoomリンクを確定する。会社名・お名前・メールアドレスを必ず伺った後に呼ぶ。",
    input_schema: {
      type: "object",
      properties: {
        start_iso: { type: "string", description: "予定開始時刻(ISO8601、get_available_slotsの値をそのまま使う)" },
        end_iso: { type: "string", description: "予定終了時刻(ISO8601、get_available_slotsの値をそのまま使う)" },
        label: { type: "string", description: "確認用のラベル。例:「8/14(金) 14:00〜」" },
        company: { type: "string", description: "会社名" },
        contact_name: { type: "string", description: "お名前" },
        email: { type: "string", description: "メールアドレス" },
        tel: { type: "string", description: "電話番号(任意、無ければ空文字)" },
        trouble: { type: "string", description: "現在お困りのこと(任意、無ければ空文字)" },
      },
      required: ["start_iso", "end_iso", "label", "company", "contact_name", "email"],
    },
  },
];

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}

async function verifyLineSignature(rawBody, signature, channelSecret) {
  if (!signature) return false;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(channelSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sigBuffer = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(rawBody));
  const expected = btoa(String.fromCharCode(...new Uint8Array(sigBuffer)));
  return timingSafeEqual(expected, signature);
}

async function loadHistory(env, userId) {
  const raw = await env.CHAT_HISTORY.get(userId);
  if (!raw) return [];
  try {
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

async function saveHistory(env, userId, history) {
  const trimmed = history.slice(-MAX_HISTORY_MESSAGES);
  await env.CHAT_HISTORY.put(userId, JSON.stringify(trimmed), {
    expirationTtl: HISTORY_TTL_SECONDS,
  });
}

async function getGoogleAccessToken(env) {
  const res = await fetch(GOOGLE_TOKEN_URL, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: env.GOOGLE_OAUTH_CLIENT_ID,
      client_secret: env.GOOGLE_OAUTH_CLIENT_SECRET,
      refresh_token: env.GOOGLE_CALENDAR_REFRESH_TOKEN,
      grant_type: "refresh_token",
    }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error("Google token error: " + JSON.stringify(data));
  }
  return data.access_token;
}

// JSTはUTC+9固定(サマータイムなし)として扱う。
function candidateSlotsJst(days) {
  const slots = [];
  const now = new Date();
  for (let d = 1; d <= days; d++) {
    const shifted = new Date(now.getTime() + d * 24 * 60 * 60 * 1000 + 9 * 60 * 60 * 1000);
    const weekday = shifted.getUTCDay();
    const y = shifted.getUTCFullYear();
    const m = shifted.getUTCMonth();
    const dd = shifted.getUTCDate();
    const isWeekendOrHoliday = weekday === 0 || weekday === 6 || isJpHoliday(y, m, dd);
    const hourStart = isWeekendOrHoliday ? BUSINESS_HOUR_START_WEEKEND : BUSINESS_HOUR_START_WEEKDAY;
    for (let hour = hourStart; hour < BUSINESS_HOUR_END; hour++) {
      const startUtcMs = Date.UTC(y, m, dd, hour - 9, 0, 0);
      slots.push({
        start: new Date(startUtcMs),
        end: new Date(startUtcMs + SLOT_MINUTES * 60 * 1000),
      });
    }
  }
  return slots;
}

async function getBusyRanges(env, days) {
  const accessToken = await getGoogleAccessToken(env);
  const now = new Date();
  const timeMin = now.toISOString();
  const timeMax = new Date(now.getTime() + (days + 1) * 24 * 60 * 60 * 1000).toISOString();
  const calendarId = env.GOOGLE_CALENDAR_ID || "primary";

  const res = await fetch(`${GOOGLE_CALENDAR_API}/freeBusy`, {
    method: "POST",
    headers: { "content-type": "application/json", Authorization: `Bearer ${accessToken}` },
    body: JSON.stringify({ timeMin, timeMax, items: [{ id: calendarId }] }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error("freeBusy error: " + JSON.stringify(data));
  }
  const busy = data.calendars?.[calendarId]?.busy || [];
  return busy.map((b) => ({ start: new Date(b.start).getTime(), end: new Date(b.end).getTime() }));
}

function filterFreeSlots(candidates, busyRanges, now) {
  return candidates
    .filter((slot) => slot.start.getTime() > now.getTime())
    .filter((slot) => !busyRanges.some((b) => slot.start.getTime() < b.end && slot.end.getTime() > b.start));
}

async function getAvailableSlots(env) {
  const now = new Date();
  const busyRanges = await getBusyRanges(env, LOOKAHEAD_DAYS_CHAT);
  const formatter = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    month: "numeric",
    day: "numeric",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  });

  const free = filterFreeSlots(candidateSlotsJst(LOOKAHEAD_DAYS_CHAT), busyRanges, now).slice(0, MAX_SLOTS_SHOWN_CHAT);

  return free.map((slot) => ({
    label: formatter.format(slot.start) + "〜",
    start_iso: slot.start.toISOString(),
    end_iso: slot.end.toISOString(),
  }));
}

async function getOpenSlotsForPage(env) {
  const now = new Date();
  const busyRanges = await getBusyRanges(env, LOOKAHEAD_DAYS_PAGE);
  const free = filterFreeSlots(candidateSlotsJst(LOOKAHEAD_DAYS_PAGE), busyRanges, now);

  const dateFormatter = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    month: "numeric",
    day: "numeric",
    weekday: "short",
  });
  const timeFormatter = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    hour: "2-digit",
    minute: "2-digit",
  });

  return free.map((slot) => ({
    date_label: dateFormatter.format(slot.start),
    time_label: timeFormatter.format(slot.start),
    start_iso: slot.start.toISOString(),
    end_iso: slot.end.toISOString(),
  }));
}

async function bookCalendarEvent(env, { start_iso, end_iso, label, contact }) {
  const accessToken = await getGoogleAccessToken(env);
  const calendarId = env.GOOGLE_CALENDAR_ID || "primary";

  const c = contact || {};
  const descLines = [
    "オンライン相談の予約です。",
    `Zoomリンク: ${env.ZOOM_PERSONAL_ROOM_URL}`,
    "",
    `会社名: ${c.company || "(未記入)"}`,
    `お名前: ${c.contact_name || "(未記入)"}`,
    `メール: ${c.email || "(未記入)"}`,
    `電話番号: ${c.tel || "(未記入)"}`,
    `業種: ${c.industry || "(未記入)"}`,
    `現在お困りのこと: ${c.trouble || "(未記入)"}`,
  ];

  const event = {
    summary: `ツナグモ オンライン相談${c.company ? "（" + c.company + "）" : ""}`,
    description: descLines.join("\n"),
    start: { dateTime: start_iso, timeZone: "Asia/Tokyo" },
    end: { dateTime: end_iso, timeZone: "Asia/Tokyo" },
    // 2026-08-29追加: 予約画面の一度きりの表示だけだと、顧客がZoomリンクを忘れたときに
    // 確認する手段がなかった。顧客をゲストとして招待し、Googleカレンダーから招待メールが
    // 自動送信されるようにする(本文=descriptionにZoomリンクを含むため、メールにも載る)。
    attendees: c.email ? [{ email: c.email, displayName: c.contact_name || undefined }] : [],
  };

  const res = await fetch(
    `${GOOGLE_CALENDAR_API}/calendars/${encodeURIComponent(calendarId)}/events?sendUpdates=all`,
    {
      method: "POST",
      headers: { "content-type": "application/json", Authorization: `Bearer ${accessToken}` },
      body: JSON.stringify(event),
    }
  );
  const data = await res.json();
  if (!res.ok) {
    throw new Error("calendar insert error: " + JSON.stringify(data));
  }

  return { booked: true, label, zoom_url: env.ZOOM_PERSONAL_ROOM_URL, event_link: data.htmlLink };
}

async function stripeRequest(env, method, path, params) {
  let url = `${STRIPE_API}/${path}`;
  let body;
  if (method === "GET") {
    if (params) url += "?" + new URLSearchParams(params).toString();
  } else if (params) {
    body = new URLSearchParams(params).toString();
  }
  const res = await fetch(url, {
    method,
    headers: {
      Authorization: "Basic " + btoa(`${env.STRIPE_SECRET_KEY}:`),
      "content-type": "application/x-www-form-urlencoded",
    },
    body,
  });
  const json = await res.json();
  if (!res.ok) {
    throw new Error("Stripe error: " + JSON.stringify(json));
  }
  return json;
}

// 2026-08-29追加: オンライン相談中に、その場で顧客に案内するカード登録リンクを発行する。
// 予約時点ではなく、管理画面(/admin/checkout-link)から野村さんが任意のタイミングで呼ぶ。
async function createStripeCheckoutSession(env, customerId, originUrl) {
  const session = await stripeRequest(env, "POST", "checkout/sessions", {
    mode: "setup",
    currency: "jpy",
    customer: customerId,
    success_url: `${originUrl}/reserve/done`,
    cancel_url: `${originUrl}/reserve`,
  });
  return session.url;
}

async function saveBooking(env, customerId, record) {
  await env.CHAT_HISTORY.put(`booking:${customerId}`, JSON.stringify(record), {
    expirationTtl: BOOKING_TTL_SECONDS,
  });
}

async function listPendingBookings(env) {
  const list = await env.CHAT_HISTORY.list({ prefix: "booking:" });
  const bookings = [];
  for (const key of list.keys) {
    const raw = await env.CHAT_HISTORY.get(key.name);
    if (!raw) continue;
    const record = JSON.parse(raw);
    if (record.status === "awaiting_trial") {
      bookings.push({ customerId: key.name.replace("booking:", ""), ...record });
    }
  }
  return bookings;
}

async function activateTrial(env, customerId) {
  const pms = await stripeRequest(env, "GET", "payment_methods", { customer: customerId, type: "card" });
  const pm = pms.data && pms.data[0];
  if (!pm) {
    throw new Error("この顧客にはまだカードが登録されていません");
  }
  const subscription = await stripeRequest(env, "POST", "subscriptions", {
    customer: customerId,
    "items[0][price]": env.STRIPE_PRICE_TSUNAGUMO,
    trial_period_days: String(TRIAL_DAYS),
    default_payment_method: pm.id,
  });

  const raw = await env.CHAT_HISTORY.get(`booking:${customerId}`);
  if (raw) {
    const record = JSON.parse(raw);
    record.status = "trial_active";
    record.subscription_id = subscription.id;
    record.trial_started_at = new Date().toISOString();
    await saveBooking(env, customerId, record);
  }
  return subscription;
}

// トライアル開始後、SaaS本体(saas/)側にテナントを発行する。SaaSは別サービスとして
// デプロイされる想定(env.SAAS_BASE_URL未設定の間は呼び出せない。その場合は例外を投げ、
// 呼び出し元でStripeトライアル自体は開始済みのまま、SaaS発行だけ失敗として扱う)。
async function createSaasTenant(env, { name, industry, anthropicApiKey, email, stripeCustomerId }) {
  if (!env.SAAS_BASE_URL) {
    throw new Error("SAAS_BASE_URLが未設定です(SaaS本体がまだデプロイされていない可能性があります)");
  }
  const res = await fetch(`${env.SAAS_BASE_URL}/admin/tenants`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      Authorization: `Bearer ${env.SAAS_ADMIN_API_KEY}`,
    },
    // emailを渡すと、SaaS側(Resend経由)がAPIキー入りのウェルカムメールを自動送信する
    // (SaaS側のRESEND_API_KEY未設定の間はレスポンスのemail_sent=falseで分かる)。
    // stripe_customer_idを渡しておくと、解約・失効時にStripe Webhook経由で自動的に
    // アクセスが止まる(未指定だと解約後もAPIキーが使え続けてしまう、2026-09-01対応)。
    body: JSON.stringify({
      name,
      industry,
      anthropic_api_key: anthropicApiKey,
      email: email || null,
      stripe_customer_id: stripeCustomerId || null,
    }),
  });
  const json = await res.json();
  if (!res.ok) {
    throw new Error("SaaSテナント発行エラー: " + JSON.stringify(json));
  }
  return json; // { tenant_id, name, industry, api_key }
}

async function runTool(env, name, input) {
  if (name === "get_available_slots") {
    const slots = await getAvailableSlots(env);
    return JSON.stringify({ slots });
  }
  if (name === "book_slot") {
    const { start_iso, end_iso, label, ...contact } = input;
    // 不動産一本化により、業種はLLMに聞かせず固定値にする
    const result = await bookCalendarEvent(env, { start_iso, end_iso, label, contact: { ...contact, industry: "不動産" } });
    return JSON.stringify(result);
  }
  return JSON.stringify({ error: "unknown tool: " + name });
}

async function callClaude(env, history, reserveUrl) {
  let messages = history;
  const systemPrompt = SYSTEM_PROMPT.replace("{{RESERVE_URL}}", reserveUrl);

  for (let turn = 0; turn < MAX_TOOL_TURNS; turn++) {
    const res = await fetch(ANTHROPIC_API_URL, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": env.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: env.ANTHROPIC_MODEL,
        max_tokens: 800,
        system: systemPrompt,
        tools: TOOLS,
        messages,
      }),
    });

    if (!res.ok) {
      const errText = await res.text();
      console.error("Anthropic API error", res.status, errText);
      return {
        replyText: "申し訳ございません、只今お答えできませんでした。少し時間をおいて、もう一度お送りください。",
        messages,
      };
    }

    const data = await res.json();
    messages = [...messages, { role: "assistant", content: data.content }];

    if (data.stop_reason !== "tool_use") {
      const textBlock = (data.content || []).find((b) => b.type === "text");
      return {
        replyText: textBlock?.text?.trim() || "申し訳ございません、うまくお答えできませんでした。",
        messages,
      };
    }

    const toolResults = [];
    for (const block of data.content) {
      if (block.type !== "tool_use") continue;
      let content;
      try {
        content = await runTool(env, block.name, block.input);
      } catch (err) {
        console.error("tool execution failed", block.name, err);
        content = JSON.stringify({ error: String(err.message || err) });
      }
      toolResults.push({ type: "tool_result", tool_use_id: block.id, content });
    }
    messages = [...messages, { role: "user", content: toolResults }];
  }

  return {
    replyText: "申し訳ございません、処理に時間がかかっています。少ししてから、もう一度お試しください。",
    messages,
  };
}

async function replyToLine(env, replyToken, text) {
  const truncated = text.length > 4900 ? text.slice(0, 4900) + "…" : text;
  const res = await fetch(LINE_REPLY_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      Authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`,
    },
    body: JSON.stringify({
      replyToken,
      messages: [{ type: "text", text: truncated }],
    }),
  });
  if (!res.ok) {
    console.error("LINE reply error", res.status, await res.text());
  }
}

async function handleEvent(env, event, reserveUrl) {
  if (event.type !== "message" || event.message?.type !== "text") {
    if (event.replyToken) {
      await replyToLine(env, event.replyToken, "テキストメッセージでお送りください。");
    }
    return;
  }

  const userId = event.source?.userId;
  const userText = event.message.text;
  const replyToken = event.replyToken;
  if (!userId || !replyToken) return;

  const history = await loadHistory(env, userId);
  history.push({ role: "user", content: userText });

  const { replyText, messages } = await callClaude(env, history, reserveUrl);

  await saveHistory(env, userId, messages);
  await replyToLine(env, replyToken, replyText);
}

function reservePageHtml() {
  return `<!doctype html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>オンライン相談を予約｜ツナグモ</title>
<style>
  :root { --bg:#F6F4EF; --page-bg:#FFFFFF; --ink:#22283A; --ink-soft:#4B5165; --ink-faint:#7A8093; --accent:#B8792F; --accent-soft:#EFE3D1; --line:#D9D3C7; }
  @media (prefers-color-scheme: dark) { :root { --bg:#14161C; --page-bg:#1B1E27; --ink:#ECE7DC; --ink-soft:#B7BAC4; --ink-faint:#7E8494; --accent:#D79A52; --accent-soft:rgba(183,121,47,0.16); --line:#333A48; } }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font-family:"游ゴシック体","Yu Gothic","Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif; line-height:1.75; padding-bottom:60px; }
  h1,h2 { font-family:"游明朝","Yu Mincho","Hiragino Mincho ProN","Noto Serif JP",serif; }
  .wrap { max-width: 560px; margin: 0 auto; padding: 32px 20px; }
  h1 { font-size: 22px; margin: 0 0 8px; }
  p.lead { color: var(--ink-soft); font-size: 14px; margin: 0 0 28px; }
  .panel { background: var(--page-bg); border:1px solid var(--line); border-radius: 10px; padding: 20px; margin-bottom: 16px; }
  .panel h2 { font-size: 15px; margin: 0 0 12px; }
  .dates { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 8px; }
  .date-btn { flex: none; padding: 10px 14px; border-radius: 8px; border: 1px solid var(--line); background: var(--page-bg); color: var(--ink); font-size: 13px; cursor: pointer; white-space: nowrap; }
  .date-btn.active { background: var(--ink); color: var(--bg); border-color: var(--ink); }
  .times { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 14px; }
  .time-btn { padding: 10px 6px; border-radius: 8px; border: 1px solid var(--line); background: var(--page-bg); color: var(--ink); font-size: 13px; cursor: pointer; }
  .time-btn.selected { background: var(--accent); color: #fff; border-color: var(--accent); }
  .field { display: grid; gap: 6px; margin-bottom: 14px; }
  .field label { font-size: 12.5px; color: var(--ink-soft); }
  .field .req { color: var(--accent); }
  .field input, .field select, .field textarea { width:100%; padding: 10px 12px; border-radius: 6px; border: 1px solid var(--line); background: var(--page-bg); color: var(--ink); font-size: 14.5px; font-family: inherit; }
  .field textarea { min-height: 70px; resize: vertical; }
  .btn { display:block; width:100%; padding: 14px; border-radius: 6px; border:none; background: var(--ink); color: var(--bg); font-size: 15px; font-weight: 700; cursor: pointer; }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .note { font-size: 11.5px; color: var(--ink-faint); margin-top: 16px; line-height: 1.7; }
  .status { text-align:center; font-size: 13.5px; color: var(--accent); min-height: 20px; margin-top: 10px; }
  .empty { color: var(--ink-faint); font-size: 13px; padding: 12px 0; }
  .confirm { text-align:center; padding: 40px 20px; }
  .confirm .big { font-size: 24px; margin: 16px 0; }
  .confirm a { color: var(--accent); }
  .hidden { display: none; }
</style>
</head>
<body>
<div class="wrap">
  <h1>オンライン相談を予約</h1>
  <p class="lead">カレンダーからご都合の良い日時をお選びください。30分ほどオンラインでお話しします。</p>

  <div id="form-view">
    <div class="panel">
      <h2>① 日時を選ぶ</h2>
      <div id="dates" class="dates"></div>
      <div id="times" class="times"></div>
      <div id="empty" class="empty hidden">現在、表示できる空き時間がありません。少し時間を置いてから再度お試しください。</div>
    </div>

    <div class="panel">
      <h2>② ご連絡先</h2>
      <div class="field">
        <label>会社名 <span class="req">*</span></label>
        <input id="f-company" type="text" placeholder="株式会社サンプル">
      </div>
      <div class="field">
        <label>お名前 <span class="req">*</span></label>
        <input id="f-name" type="text" placeholder="山田 太郎">
      </div>
      <div class="field">
        <label>メールアドレス <span class="req">*</span></label>
        <input id="f-email" type="email" placeholder="you@example.com">
      </div>
      <div class="field">
        <label>電話番号（任意）</label>
        <input id="f-tel" type="tel" placeholder="090-0000-0000">
      </div>
      <div class="field">
        <label>現在お困りのこと（任意）</label>
        <textarea id="f-trouble" placeholder="例：物件紹介文の作成に毎月20時間ほどかかっている"></textarea>
      </div>
    </div>

    <button id="submit-btn" class="btn" disabled>この内容で予約する</button>
    <div id="status" class="status"></div>

    <p class="note">
      ※ トライアルは、この相談（初期設定）の後から2週間です。<br>
      ※ トライアル開始にはクレジットカードのご登録が必要です。トライアル期間中の課金はありません。<br>
      ※ 生成された文章は、必ず内容をご確認のうえご利用ください。<br>
      ※ 文章の生成には海外のAIサービス（Anthropic社）を利用しています。個人情報を含む内容の入力はお控えください。
    </p>
  </div>

  <div id="confirm-view" class="confirm hidden">
    <h2>予約が完了しました</h2>
    <div class="big" id="confirm-when"></div>
    <p>当日は、以下のZoomリンクからご参加ください。</p>
    <p><a id="confirm-zoom" href="#" target="_blank"></a></p>
  </div>
</div>

<script>
(function () {
  var slotsByDate = {};
  var dateOrder = [];
  var selectedDate = null;
  var selectedSlot = null;

  var datesEl = document.getElementById('dates');
  var timesEl = document.getElementById('times');
  var emptyEl = document.getElementById('empty');
  var submitBtn = document.getElementById('submit-btn');
  var statusEl = document.getElementById('status');

  function checkFormValid() {
    var ok = selectedSlot &&
      document.getElementById('f-company').value.trim() &&
      document.getElementById('f-name').value.trim() &&
      document.getElementById('f-email').value.trim();
    submitBtn.disabled = !ok;
  }

  ['f-company', 'f-name', 'f-email'].forEach(function (id) {
    document.getElementById(id).addEventListener('input', checkFormValid);
    document.getElementById(id).addEventListener('change', checkFormValid);
  });

  function renderDates() {
    datesEl.innerHTML = '';
    dateOrder.forEach(function (d) {
      var btn = document.createElement('button');
      btn.className = 'date-btn' + (d === selectedDate ? ' active' : '');
      btn.textContent = d;
      btn.onclick = function () { selectedDate = d; selectedSlot = null; renderDates(); renderTimes(); checkFormValid(); };
      datesEl.appendChild(btn);
    });
  }

  function renderTimes() {
    timesEl.innerHTML = '';
    if (!selectedDate) return;
    slotsByDate[selectedDate].forEach(function (slot) {
      var btn = document.createElement('button');
      btn.className = 'time-btn' + (selectedSlot === slot ? ' selected' : '');
      btn.textContent = slot.time_label;
      btn.onclick = function () { selectedSlot = slot; renderTimes(); checkFormValid(); };
      timesEl.appendChild(btn);
    });
  }

  fetch('/api/slots').then(function (r) { return r.json(); }).then(function (data) {
    (data.slots || []).forEach(function (slot) {
      if (!slotsByDate[slot.date_label]) {
        slotsByDate[slot.date_label] = [];
        dateOrder.push(slot.date_label);
      }
      slotsByDate[slot.date_label].push(slot);
    });
    if (dateOrder.length === 0) {
      emptyEl.classList.remove('hidden');
      return;
    }
    selectedDate = dateOrder[0];
    renderDates();
    renderTimes();
  }).catch(function () {
    emptyEl.classList.remove('hidden');
  });

  submitBtn.addEventListener('click', function () {
    submitBtn.disabled = true;
    statusEl.textContent = '予約しています…';
    fetch('/api/book', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        start_iso: selectedSlot.start_iso,
        end_iso: selectedSlot.end_iso,
        label: selectedDate + ' ' + selectedSlot.time_label,
        company: document.getElementById('f-company').value.trim(),
        contact_name: document.getElementById('f-name').value.trim(),
        email: document.getElementById('f-email').value.trim(),
        tel: document.getElementById('f-tel').value.trim(),
        industry: "不動産",
        trouble: document.getElementById('f-trouble').value.trim(),
      }),
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (!data.ok) throw new Error(data.error || 'unknown');
      if (data.checkout_url) {
        statusEl.textContent = '日程の確保ができました。続けてカード登録へ進みます…';
        window.location.href = data.checkout_url;
        return;
      }
      document.getElementById('form-view').classList.add('hidden');
      document.getElementById('confirm-view').classList.remove('hidden');
      document.getElementById('confirm-when').textContent = data.label;
      var zoomLink = document.getElementById('confirm-zoom');
      zoomLink.href = data.zoom_url;
      zoomLink.textContent = data.zoom_url;
    }).catch(function () {
      statusEl.textContent = '予約に失敗しました。少し時間をおいて、もう一度お試しください。';
      submitBtn.disabled = false;
    });
  });
})();
</script>
</body>
</html>`;
}

async function handleReserveApi(request, env, pathname, originUrl) {
  if (pathname === "/api/slots" && request.method === "GET") {
    try {
      const slots = await getOpenSlotsForPage(env);
      return new Response(JSON.stringify({ slots }), {
        headers: { "content-type": "application/json" },
      });
    } catch (err) {
      console.error("slots api failed", err);
      return new Response(JSON.stringify({ slots: [], error: String(err.message || err) }), {
        status: 500,
        headers: { "content-type": "application/json" },
      });
    }
  }

  if (pathname === "/api/book" && request.method === "POST") {
    try {
      const body = await request.json();
      const { start_iso, end_iso, label, ...contact } = body;
      if (!start_iso || !end_iso || !contact.company || !contact.contact_name || !contact.email || !contact.industry) {
        return new Response(JSON.stringify({ ok: false, error: "missing required fields" }), {
          status: 400,
          headers: { "content-type": "application/json" },
        });
      }
      const result = await bookCalendarEvent(env, { start_iso, end_iso, label, contact });
      // 2026-08-29変更: カード登録は予約時ではなく、オンライン相談中に管理画面から
      // リンクを発行して野村さんがその場で案内する運用に変更。ここではStripe顧客の
      // 作成のみ行い(カード不要)、Checkout Session(カード入力)は作らない。
      const customer = await stripeRequest(env, "POST", "customers", {
        email: contact.email,
        name: contact.contact_name,
        "metadata[company]": contact.company || "",
      });
      await saveBooking(env, customer.id, {
        ...contact,
        label,
        event_link: result.event_link,
        status: "awaiting_trial",
        apikey_token: crypto.randomUUID(),
        created_at: new Date().toISOString(),
      });
      return new Response(JSON.stringify({ ok: true, ...result }), {
        headers: { "content-type": "application/json" },
      });
    } catch (err) {
      console.error("book api failed", err);
      return new Response(JSON.stringify({ ok: false, error: String(err.message || err) }), {
        status: 500,
        headers: { "content-type": "application/json" },
      });
    }
  }

  // 2026-08-29追加: AnthropicのAPIキーは、野村さんが口頭で聞いて打ち込むのではなく、
  // クレカ登録と同じ考え方で顧客自身がこのリンクから直接入力する。
  if (pathname === "/api/apikey" && request.method === "POST") {
    try {
      const body = await request.json();
      const { customer_id, token, anthropic_api_key } = body;
      if (!customer_id || !token || !anthropic_api_key) {
        return new Response(JSON.stringify({ ok: false, error: "missing required fields" }), {
          status: 400,
          headers: { "content-type": "application/json" },
        });
      }
      const raw = await env.CHAT_HISTORY.get(`booking:${customer_id}`);
      if (!raw) {
        return new Response(JSON.stringify({ ok: false, error: "invalid customer_id" }), {
          status: 404,
          headers: { "content-type": "application/json" },
        });
      }
      const record = JSON.parse(raw);
      if (record.apikey_token !== token) {
        return new Response(JSON.stringify({ ok: false, error: "invalid token" }), {
          status: 401,
          headers: { "content-type": "application/json" },
        });
      }
      record.anthropic_api_key = anthropic_api_key;
      await saveBooking(env, customer_id, record);
      return new Response(JSON.stringify({ ok: true }), {
        headers: { "content-type": "application/json" },
      });
    } catch (err) {
      console.error("apikey api failed", err);
      return new Response(JSON.stringify({ ok: false, error: String(err.message || err) }), {
        status: 500,
        headers: { "content-type": "application/json" },
      });
    }
  }

  return new Response("Not found", { status: 404 });
}

function reserveDoneHtml() {
  return `<!doctype html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>登録完了｜ツナグモ</title>
<style>
  body { margin:0; background:#F6F4EF; color:#22283A; font-family:"游ゴシック体","Yu Gothic",sans-serif; line-height:1.8; }
  .wrap { max-width:480px; margin:0 auto; padding:60px 24px; text-align:center; }
  h1 { font-family:"游明朝","Yu Mincho",serif; font-size:22px; }
</style></head>
<body><div class="wrap">
  <h1>カード登録が完了しました</h1>
  <p>ありがとうございます。この後、担当者がトライアル開始の手続きを行います。</p>
  <p style="font-size:13px;color:#7A8093;margin-top:24px;">トライアルは相談完了後から2週間です。ここまでの課金は発生しません。</p>
</div></body></html>`;
}

// 2026-08-29追加: AnthropicのAPIキーは、カード登録と同じ考え方で顧客自身がこのページから
// 直接入力する(野村さんが口頭で聞いて代理入力しない)。
function apikeyFormHtml(customerId, token) {
  return `<!doctype html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>APIキーのご登録｜ツナグモ</title>
<style>
  body { margin:0; background:#F6F4EF; color:#22283A; font-family:"游ゴシック体","Yu Gothic",sans-serif; line-height:1.8; }
  .wrap { max-width:480px; margin:0 auto; padding:60px 24px; }
  h1 { font-family:"游明朝","Yu Mincho",serif; font-size:20px; }
  .hint { font-size:13px; color:#7A8093; }
  a { color:#B8792F; }
  input[type="password"] { width:100%; box-sizing:border-box; padding:12px; margin-top:16px; border:1px solid #D9D3C7; border-radius:6px; font-size:14px; font-family:monospace; }
  .submit-btn { display:block; width:100%; padding:12px; border:none; border-radius:6px; background:#22283A; color:#fff; font-size:14px; cursor:pointer; margin-top:16px; }
  .submit-btn:disabled { opacity:0.6; }
  #msg { margin-top:16px; font-size:13.5px; }
  #msg.error { color:#8A3B2E; }
  #msg.ok { color:#4d7a4e; }
  #form-block.hidden { display:none; }
</style></head>
<body><div class="wrap">
  <h1>Anthropic APIキーのご登録</h1>
  <p class="hint">2026年9月1日より、ツナグモのAI利用料は運営側で負担する方式に変更されたため、
  お客様ご自身でのAPIキーのご登録は不要になりました。このページでの操作は必要ございません。</p>
  <div id="form-block">
    <input type="password" id="key-input" placeholder="sk-ant-..." autocomplete="off">
    <button class="submit-btn" id="submit-btn" type="button">登録する</button>
  </div>
  <div id="msg"></div>
  <script>
  (function () {
    var customerId = ${JSON.stringify(customerId)};
    var token = ${JSON.stringify(token)};
    var input = document.getElementById('key-input');
    var btn = document.getElementById('submit-btn');
    var msg = document.getElementById('msg');
    var block = document.getElementById('form-block');
    btn.addEventListener('click', function () {
      var key = input.value.trim();
      if (!key) { return; }
      btn.disabled = true;
      msg.className = ''; msg.textContent = '';
      fetch('/api/apikey', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ customer_id: customerId, token: token, anthropic_api_key: key }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            block.classList.add('hidden');
            msg.className = 'ok';
            msg.textContent = '登録が完了しました。ありがとうございます。';
          } else {
            btn.disabled = false;
            msg.className = 'error';
            msg.textContent = 'エラー: ' + (data.error || '登録に失敗しました');
          }
        })
        .catch(function () {
          btn.disabled = false;
          msg.className = 'error';
          msg.textContent = '通信エラーが発生しました。もう一度お試しください。';
        });
    });
  })();
  </script>
</div></body></html>`;
}

function adminBookingsHtml(bookings) {
  const industryOptions = () => {
    return SAAS_INDUSTRIES.map(
      (i) => `<option value="${i}" selected>${SAAS_INDUSTRY_LABELS_JP[i]}</option>`
    ).join("");
  };
  const rows = bookings
    .map(
      (b) => `<div style="border:1px solid #D9D3C7;border-radius:8px;padding:16px;margin-bottom:12px;">
        <div><b>${b.company || ""}</b>（${b.contact_name || ""} / ${b.industry || ""}）</div>
        <div style="font-size:13px;color:#4B5165;">${b.email || ""} ${b.tel || ""}</div>
        <div style="font-size:13px;color:#4B5165;">予約日時: ${b.label || ""}</div>
        <div style="font-size:13px;color:#4B5165;">お困りのこと: ${b.trouble || "(未記入)"}</div>
        <a href="/admin/checkout-link?customer_id=${b.customerId}&key=__ADMIN_KEY__" target="_blank"
           style="display:inline-block;margin-top:10px;padding:8px 16px;border-radius:6px;background:#EFE3D1;color:#22283A;text-decoration:none;font-size:13px;">
           カード登録リンクを発行(相談中に案内)
        </a>
        <div style="font-size:12px;color:#4B5165;margin-top:6px;">
          2026-09-01よりAPIキー登録は不要です(運営側のキーでAI利用料を負担する方式に変更)。
        </div>
        <form method="POST" action="/admin/activate" style="margin-top:10px;">
          <input type="hidden" name="customer_id" value="${b.customerId}">
          <input type="hidden" name="key" value="__ADMIN_KEY__">
          <div style="margin-top:8px;">
            <label style="font-size:12px;color:#4B5165;">SaaS側の業種</label><br>
            <select name="saas_industry">${industryOptions()}</select>
          </div>
          <button type="submit" style="margin-top:10px;padding:8px 16px;border:none;border-radius:6px;background:#22283A;color:#fff;cursor:pointer;">③ 相談完了・トライアル開始(①②の完了後)</button>
        </form>
      </div>`
    )
    .join("");
  return `<!doctype html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>予約管理｜ツナグモ</title>
<style>body{font-family:"游ゴシック体","Yu Gothic",sans-serif;background:#F6F4EF;color:#22283A;margin:0;padding:24px;}</style>
</head><body>
<h2>相談待ち・トライアル未開始の予約</h2>
${rows || "<p>現在、該当する予約はありません。</p>"}
</body></html>`;
}

function saasTenantResultHtml(originUrl, adminKey, tenant, saasError) {
  const emailNote = tenant && tenant.email_sent
    ? `<p style="color:#4d7a4e;">顧客のメールアドレスにウェルカムメール(APIキー入り)を自動送信しました。</p>`
    : `<p><b>顧客のメールにはまだ送っていません。</b>以下を顧客に送ってください(この画面にしか表示されません)。</p>`;
  const copyBtn = (id) =>
    `<button type="button" id="${id}" class="copy-btn-small">コピー</button>`;
  const tenantBlock = tenant
    ? `<div style="border:1px solid #D9D3C7;border-radius:8px;padding:16px;margin-top:16px;">
        <p><b>SaaS側のテナントを発行しました。</b></p>
        ${emailNote}
        <p>ログインURL:<br><a href="${tenant.saasUrl}" target="_blank">${tenant.saasUrl}</a> ${copyBtn("copy-url-btn")}</p>
        <p>APIキー:<br><code style="background:#F1E6DA;padding:4px 8px;border-radius:4px;">${tenant.api_key}</code> ${copyBtn("copy-key-btn")}</p>
      </div>
      <script>
      (function () {
        var values = { "copy-url-btn": ${JSON.stringify(tenant.saasUrl)}, "copy-key-btn": ${JSON.stringify(tenant.api_key)} };
        Object.keys(values).forEach(function (id) {
          var btn = document.getElementById(id);
          if (!btn) return;
          btn.addEventListener("click", function () {
            var value = values[id];
            function done() {
              var original = btn.textContent;
              btn.textContent = "コピーしました";
              setTimeout(function () { btn.textContent = original; }, 2000);
            }
            if (navigator.clipboard && navigator.clipboard.writeText) {
              navigator.clipboard.writeText(value).then(done).catch(function () {
                var ta = document.createElement("textarea");
                ta.value = value; document.body.appendChild(ta); ta.select();
                document.execCommand("copy"); document.body.removeChild(ta); done();
              });
            } else {
              var ta = document.createElement("textarea");
              ta.value = value; document.body.appendChild(ta); ta.select();
              document.execCommand("copy"); document.body.removeChild(ta); done();
            }
          });
        });
      })();
      </script>`
    : `<div style="border:1px solid #D9847A;border-radius:8px;padding:16px;margin-top:16px;color:#8A3B2E;">
        <p><b>Stripeトライアルは開始しましたが、SaaS側のテナント発行に失敗しました。</b></p>
        <p>${saasError || ""}</p>
        <p>SaaSが未デプロイの場合はデプロイ後、管理画面(/admin.html)から手動で発行してください。</p>
      </div>`;
  return `<!doctype html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>トライアル開始完了｜ツナグモ</title>
<style>
  body{font-family:"游ゴシック体","Yu Gothic",sans-serif;background:#F6F4EF;color:#22283A;margin:0;padding:24px;}
  .copy-btn-small{padding:4px 12px;border:none;border-radius:6px;background:#22283A;color:#fff;font-size:12.5px;cursor:pointer;}
</style>
</head><body>
<h2>トライアルを開始しました</h2>
${tenantBlock}
<p style="margin-top:20px;"><a href="${originUrl}/admin?key=${adminKey}">予約一覧に戻る</a></p>
</body></html>`;
}

function checkoutLinkHtml(checkoutUrl, originUrl, adminKey) {
  return `<!doctype html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>カード登録リンク｜ツナグモ</title>
<style>
  body { margin:0; background:#F6F4EF; color:#22283A; font-family:"游ゴシック体","Yu Gothic",sans-serif; line-height:1.8; }
  .wrap { max-width:480px; margin:0 auto; padding:40px 24px; }
  h1 { font-family:"游明朝","Yu Mincho",serif; font-size:18px; }
  .box { background:#fff; border:1px solid #D9D3C7; border-radius:8px; padding:16px; word-break:break-all; font-size:13px; margin:16px 0; }
  a { color:#B8792F; }
  .hint { font-size:12.5px; color:#7A8093; }
  .copy-btn { display:block; width:100%; padding:12px; border:none; border-radius:6px; background:#22283A; color:#fff; font-size:14px; cursor:pointer; margin-top:8px; }
  .copy-btn.copied { background:#4C7A5B; }
</style></head>
<body><div class="wrap">
  <h1>相談中にこのリンクを顧客へ案内してください</h1>
  <p class="hint">Zoomのチャット・LINE・メール等、相談中に使っているツールでそのまま貼って送ってください。
  顧客がこのリンクでカードを登録すると、続けて下の「相談完了・トライアル開始」が使えるようになります。</p>
  <div class="box"><a href="${checkoutUrl}" target="_blank" id="link-text">${checkoutUrl}</a></div>
  <button class="copy-btn" id="copy-btn" type="button">リンクをコピー</button>
  <p style="margin-top:20px;"><a href="${originUrl}/admin?key=${adminKey}">予約一覧に戻る</a></p>
  <script>
  (function () {
    var btn = document.getElementById('copy-btn');
    var url = ${JSON.stringify(checkoutUrl)};
    btn.addEventListener('click', function () {
      function done() {
        btn.textContent = 'コピーしました';
        btn.classList.add('copied');
        setTimeout(function () { btn.textContent = 'リンクをコピー'; btn.classList.remove('copied'); }, 2000);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(done).catch(function () {
          var ta = document.createElement('textarea');
          ta.value = url; document.body.appendChild(ta); ta.select();
          document.execCommand('copy'); document.body.removeChild(ta); done();
        });
      } else {
        var ta = document.createElement('textarea');
        ta.value = url; document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta); done();
      }
    });
  })();
  </script>
</div></body></html>`;
}

function apikeyLinkAdminHtml(apikeyUrl, originUrl, adminKey) {
  return `<!doctype html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>APIキー登録リンク｜ツナグモ</title>
<style>
  body { margin:0; background:#F6F4EF; color:#22283A; font-family:"游ゴシック体","Yu Gothic",sans-serif; line-height:1.8; }
  .wrap { max-width:480px; margin:0 auto; padding:40px 24px; }
  h1 { font-family:"游明朝","Yu Mincho",serif; font-size:18px; }
  .box { background:#fff; border:1px solid #D9D3C7; border-radius:8px; padding:16px; word-break:break-all; font-size:13px; margin:16px 0; }
  a { color:#B8792F; }
  .hint { font-size:12.5px; color:#7A8093; }
  .copy-btn { display:block; width:100%; padding:12px; border:none; border-radius:6px; background:#22283A; color:#fff; font-size:14px; cursor:pointer; margin-top:8px; }
  .copy-btn.copied { background:#4C7A5B; }
</style></head>
<body><div class="wrap">
  <h1>相談中にこのリンクを顧客へ案内してください</h1>
  <p class="hint">Zoomのチャット・LINE・メール等、相談中に使っているツールでそのまま貼って送ってください。
  顧客がこのリンクで自身のAnthropic APIキーを直接入力できます(こちらで代理入力する必要はありません)。</p>
  <div class="box"><a href="${apikeyUrl}" target="_blank" id="link-text">${apikeyUrl}</a></div>
  <button class="copy-btn" id="copy-btn" type="button">リンクをコピー</button>
  <p style="margin-top:20px;"><a href="${originUrl}/admin?key=${adminKey}">予約一覧に戻る</a></p>
  <script>
  (function () {
    var btn = document.getElementById('copy-btn');
    var url = ${JSON.stringify(apikeyUrl)};
    btn.addEventListener('click', function () {
      function done() {
        btn.textContent = 'コピーしました';
        btn.classList.add('copied');
        setTimeout(function () { btn.textContent = 'リンクをコピー'; btn.classList.remove('copied'); }, 2000);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(done).catch(function () {
          var ta = document.createElement('textarea');
          ta.value = url; document.body.appendChild(ta); ta.select();
          document.execCommand('copy'); document.body.removeChild(ta); done();
        });
      } else {
        var ta = document.createElement('textarea');
        ta.value = url; document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta); done();
      }
    });
  })();
  </script>
</div></body></html>`;
}

async function handleAdminApi(request, env, pathname, url) {
  if (pathname === "/admin" && request.method === "GET") {
    if (url.searchParams.get("key") !== env.ADMIN_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }
    const bookings = await listPendingBookings(env);
    const html = adminBookingsHtml(bookings).replaceAll("__ADMIN_KEY__", env.ADMIN_SECRET);
    return new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } });
  }

  if (pathname === "/admin/checkout-link" && request.method === "GET") {
    if (url.searchParams.get("key") !== env.ADMIN_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }
    const customerId = url.searchParams.get("customer_id");
    if (!customerId) {
      return new Response("customer_id が必要です", { status: 400 });
    }
    try {
      const checkoutUrl = await createStripeCheckoutSession(env, customerId, url.origin);
      return new Response(checkoutLinkHtml(checkoutUrl, url.origin, env.ADMIN_SECRET), {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    } catch (err) {
      console.error("checkout-link failed", err);
      return new Response(`エラー: ${String(err.message || err)}`, { status: 500 });
    }
  }

  if (pathname === "/admin/apikey-link" && request.method === "GET") {
    if (url.searchParams.get("key") !== env.ADMIN_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }
    const customerId = url.searchParams.get("customer_id");
    if (!customerId) {
      return new Response("customer_id が必要です", { status: 400 });
    }
    const raw = await env.CHAT_HISTORY.get(`booking:${customerId}`);
    const record = raw ? JSON.parse(raw) : null;
    if (!record) {
      return new Response("該当する予約が見つかりません", { status: 404 });
    }
    if (!record.apikey_token) {
      // 2026-08-29以前に作成された予約にはトークンが無いので、ここで発行して保存する。
      record.apikey_token = crypto.randomUUID();
      await saveBooking(env, customerId, record);
    }
    const apikeyUrl = `${url.origin}/apikey?customer_id=${customerId}&token=${record.apikey_token}`;
    return new Response(apikeyLinkAdminHtml(apikeyUrl, url.origin, env.ADMIN_SECRET), {
      headers: { "content-type": "text/html; charset=utf-8" },
    });
  }

  if (pathname === "/admin/activate" && request.method === "POST") {
    const form = await request.formData();
    if (form.get("key") !== env.ADMIN_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }
    const customerId = form.get("customer_id");
    let bookingRecord;
    try {
      await activateTrial(env, customerId);
      const raw = await env.CHAT_HISTORY.get(`booking:${customerId}`);
      bookingRecord = raw ? JSON.parse(raw) : null;
    } catch (err) {
      console.error("activateTrial failed", err);
      return new Response(`エラー: ${String(err.message || err)}`, { status: 500 });
    }

    // Stripeトライアルはここまでで確定済み。SaaS側のテナント発行が失敗しても
    // トライアル自体は失敗させない(お金が絡む処理と、その後工程を分離する)。
    let saasTenant = null;
    let saasError = null;
    try {
      const industry = form.get("saas_industry") || "real_estate";
      // 2026-09-01: BYOK廃止(SaaS側が運営自身のAnthropicキーでAI利用料を負担する方式に
      // 全面移行)に伴い、顧客自身のAPIキー登録はもう必須ではない。②の登録リンクの
      // 案内は不要になった(bookingRecordに残っていれば後方互換として渡すだけ)。
      const anthropicApiKey = bookingRecord && bookingRecord.anthropic_api_key;
      const result = await createSaasTenant(env, {
        name: (bookingRecord && bookingRecord.company) || "顧客",
        industry,
        anthropicApiKey,
        email: bookingRecord && bookingRecord.email,
        stripeCustomerId: customerId,
      });
      saasTenant = { ...result, saasUrl: env.SAAS_BASE_URL || "" };

      if (bookingRecord) {
        bookingRecord.saas_tenant_id = result.tenant_id;
        bookingRecord.saas_industry = industry;
        await saveBooking(env, customerId, bookingRecord);
      }
    } catch (err) {
      console.error("createSaasTenant failed", err);
      saasError = String(err.message || err);
    }

    const html = saasTenantResultHtml(url.origin, env.ADMIN_SECRET, saasTenant, saasError);
    return new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } });
  }

  return new Response("Not found", { status: 404 });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const pathname = url.pathname;

    if (pathname === "/reserve" && request.method === "GET") {
      return new Response(reservePageHtml(), {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }

    if (pathname === "/reserve/done" && request.method === "GET") {
      return new Response(reserveDoneHtml(), {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }

    if (pathname === "/apikey" && request.method === "GET") {
      const customerId = url.searchParams.get("customer_id");
      const token = url.searchParams.get("token");
      if (!customerId || !token) {
        return new Response("リンクが正しくありません", { status: 400 });
      }
      const raw = await env.CHAT_HISTORY.get(`booking:${customerId}`);
      const record = raw ? JSON.parse(raw) : null;
      if (!record || record.apikey_token !== token) {
        return new Response("リンクが無効です。担当者にお問い合わせください。", { status: 401 });
      }
      return new Response(apikeyFormHtml(customerId, token), {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }

    if (pathname.startsWith("/api/")) {
      return handleReserveApi(request, env, pathname, url.origin);
    }

    if (pathname.startsWith("/admin")) {
      return handleAdminApi(request, env, pathname, url);
    }

    if (request.method !== "POST") {
      return new Response("ツナグモ LINE Bot is running.", { status: 200 });
    }

    const rawBody = await request.text();
    const signature = request.headers.get("x-line-signature");

    const isValid = await verifyLineSignature(rawBody, signature, env.LINE_CHANNEL_SECRET);
    if (!isValid) {
      return new Response("Invalid signature", { status: 401 });
    }

    let payload;
    try {
      payload = JSON.parse(rawBody);
    } catch {
      return new Response("Bad request", { status: 400 });
    }

    const events = payload.events || [];
    const reserveUrl = `${url.origin}/reserve`;

    // LINEはWebhookに即時の200 OKを期待する。Claude呼び出し+返信はレスポンス確定後にバックグラウンドで続ける。
    ctx.waitUntil(
      Promise.all(
        events.map((event) =>
          handleEvent(env, event, reserveUrl).catch((err) => {
            console.error("handleEvent failed", err);
          })
        )
      )
    );

    return new Response("OK", { status: 200 });
  },
};
