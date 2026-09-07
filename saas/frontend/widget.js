/*!
 * ツナグモ Web埋め込みチャットウィジェット(Phase 16)。
 * 顧客サイトへの埋め込み方法: <script src="https://.../widget.js" data-key="wpk_..."></script>
 *
 * 実装メモ: 仕様書はUI分離の方式として「iframe方式」を挙げているが、iframeの中で
 * fetch()すると、そのリクエストのOriginはiframe自身のオリジン(このSaaS自身のドメイン)
 * になってしまい、サーバ側で「顧客サイトのOriginを検証する」(16-3-1)という設計と
 * 両立しない。そのため、このスクリプトは顧客ページのトップレベルスクリプトとして
 * そのまま実行し(fetch()のOriginが正しく顧客サイトになる)、CSS干渉の回避には
 * Shadow DOMを使う(iframeと同じ「顧客サイトのCSSと干渉しない」という目的を満たす)。
 *
 * Anthropic/OpenAIキーはこのファイルにも、通信にも一切含まれない。data-keyは
 * ブラウザに出しても問題ない公開鍵(tenant.web_widget_public_key)。
 */
(function () {
  'use strict';

  var currentScript = document.currentScript;
  var publicKey = currentScript && currentScript.getAttribute('data-key');
  if (!publicKey) {
    console.error('[tsunagumo-widget] data-key属性が指定されていません');
    return;
  }

  var API_BASE = (function () {
    // widget.js自身の読み込み元と同じオリジンをAPI先にする
    var src = currentScript.src;
    var a = document.createElement('a');
    a.href = src;
    return a.protocol + '//' + a.host;
  })();

  var STORAGE_KEY = 'tsunagumo_widget_session_' + publicKey;
  // sessionStorageを使う(localStorageは使わない): 永続化すると、同じ端末を使う別人が
  // 前の会話(個人情報を含みうる)を見てしまう(16-4)
  function getSessionId() {
    try { return sessionStorage.getItem(STORAGE_KEY); } catch (e) { return null; }
  }
  function setSessionId(id) {
    try { sessionStorage.setItem(STORAGE_KEY, id); } catch (e) { /* noop */ }
  }

  var host = document.createElement('div');
  host.id = 'tsunagumo-chat-widget-host';
  host.style.position = 'fixed';
  host.style.bottom = '20px';
  host.style.right = '20px';
  host.style.zIndex = '2147483647';
  document.body.appendChild(host);

  // Shadow DOMで顧客サイトのCSSから隔離する
  var root = host.attachShadow({ mode: 'open' });
  root.innerHTML =
    '<style>\n' +
    '  * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif; }\n' +
    '  .bubble { width: 56px; height: 56px; border-radius: 50%; background: #b5652c; color: #fff;\n' +
    '    display: flex; align-items: center; justify-content: center; cursor: pointer; font-size: 24px;\n' +
    '    box-shadow: 0 2px 10px rgba(0,0,0,.2); border: none; }\n' +
    '  .panel { display: none; flex-direction: column; width: 320px; height: 440px; background: #fff;\n' +
    '    border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,.2); overflow: hidden; margin-bottom: 12px; }\n' +
    '  .panel.open { display: flex; }\n' +
    '  .header { background: #b5652c; color: #fff; padding: 12px 14px; font-size: 14px; }\n' +
    '  .notice { background: #fdf1dc; color: #6b5215; font-size: 11px; padding: 6px 10px; line-height: 1.5; }\n' +
    '  .messages { flex: 1; overflow-y: auto; padding: 10px; font-size: 13px; }\n' +
    '  .msg { margin-bottom: 8px; padding: 8px 10px; border-radius: 8px; max-width: 85%; line-height: 1.5; white-space: pre-wrap; }\n' +
    '  .msg.user { background: #f1e6da; margin-left: auto; }\n' +
    '  .msg.bot { background: #f2f2f0; }\n' +
    '  .inputRow { display: flex; border-top: 1px solid #e2e0db; padding: 8px; }\n' +
    '  .inputRow input { flex: 1; border: 1px solid #e2e0db; border-radius: 6px; padding: 8px; font-size: 13px; }\n' +
    '  .inputRow button { margin-left: 6px; background: #b5652c; color: #fff; border: none; border-radius: 6px; padding: 8px 12px; cursor: pointer; }\n' +
    '  .inputHint { font-size: 10px; color: #999; padding: 2px 10px 6px; }\n' +
    '  .wrap { display: flex; flex-direction: column; align-items: flex-end; }\n' +
    '</style>\n' +
    '<div class="wrap">\n' +
    '  <div class="panel" id="panel">\n' +
    '    <div class="header">お問い合わせ</div>\n' +
    '    <div class="notice">AIが自動応答しています。金額・納期・契約に関することは、担当者が改めて回答いたします。</div>\n' +
    '    <div class="messages" id="messages"></div>\n' +
    '    <div class="inputRow">\n' +
    '      <input id="input" type="text" maxlength="500" placeholder="メッセージを入力">\n' +
    '      <button id="send">送信</button>\n' +
    '    </div>\n' +
    '    <div class="inputHint">個人情報(氏名・電話番号・住所等)は入力しないでください</div>\n' +
    '  </div>\n' +
    '  <button class="bubble" id="bubble" aria-label="チャットを開く">💬</button>\n' +
    '</div>';

  var panel = root.getElementById('panel');
  var bubble = root.getElementById('bubble');
  var messagesEl = root.getElementById('messages');
  var input = root.getElementById('input');
  var sendBtn = root.getElementById('send');

  bubble.addEventListener('click', function () {
    panel.classList.toggle('open');
  });

  function addMessage(role, text) {
    var div = document.createElement('div');
    div.className = 'msg ' + (role === 'user' ? 'user' : 'bot');
    div.textContent = text;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function setSending(isSending) {
    sendBtn.disabled = isSending;
    input.disabled = isSending;
  }

  function send() {
    var text = input.value.trim();
    if (!text) return;
    input.value = '';
    addMessage('user', text);
    setSending(true);

    fetch(API_BASE + '/api/chat/' + encodeURIComponent(publicKey), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: getSessionId(), message: text }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error('request failed: ' + res.status);
        return res.json();
      })
      .then(function (data) {
        setSessionId(data.session_id);
        addMessage('bot', data.reply);
      })
      .catch(function () {
        addMessage('bot', 'ただいま接続できません。しばらくしてから再度お試しください。');
      })
      .then(function () {
        setSending(false);
      });
  }

  sendBtn.addEventListener('click', send);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') send();
  });
})();
