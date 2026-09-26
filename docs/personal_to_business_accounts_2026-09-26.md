# 個人アカウントから業務用管理への切替

2026-09-26 Codex。本人は現在のLINE「ツナグモ」と本人Gmailで限定テストを進める方針を選択。
実際の新規業務用アカウント作成、登録先変更、所有権移転はまだ行っていない。

## 推奨する順序

1. まず個人Gmailは専用件名だけ、LINEは本人の1対1テストだけに限定する。
2. 業務用の受信できるメールアカウントを用意する。将来は保有ドメインの `info@tunagumo.com` などを候補とするが、現在その受信箱が存在するとは確認していない。契約・発行は未実施。
3. 業務用Googleアカウントを用意し、現在のSNSの連絡先または管理権限を順番に切り替える。
4. 新しい管理者でログイン・通知受信・投稿管理・既存の自動連携を確認してから、旧管理権限や古いアプリパスワードを外す。個人Googleアカウント自体は削除しない。

## 何が変えられるか

| 対象 | 推奨する切替 | 確認点 |
|---|---|---|
| SaaSの受信・返信メール | 設定のメールアドレス、IMAP/SMTP、ユーザー名、アプリパスワードを新しい業務用へ更新 | 接続先変更後は次の確認時点以前のメールへ返信しない。旧メールの移行・転送はこの機能に含まれない |
| 問い合わせ通知先 | SaaSの「通知先メールアドレス」を更新 | 受信監視アドレスとは別設定。社員ログイン先の変更とも別 |
| X | 今のアカウントの登録メールを新しい業務用へ変更 | 新アドレスで確認を完了し、連携アプリと2段階認証を確認する |
| Instagram | 今のアカウントの連絡先メールを更新 | Accounts Centerで他の個人プロフィールまで選択しない。公開プロフィールの連絡先も別に確認する |
| YouTube | 同じチャンネルに業務用Googleアカウントの管理権限を付与 | 所有権の移行はブランドアカウントかどうかで異なる。ブランドのメインオーナー変更は所有者追加から7日以上の条件あり。実際の構成は切替時に確認する |
| LINE公式 | 同じ公式アカウントへ業務用の管理者を追加 | Official Account ManagerとMessaging APIのLINE Developers権限をそれぞれ確認。Webhookの置換や新しい公式アカウントの作成は不要 |

「同じSNSアカウントの登録メールや管理者を変更する」方針で、現在の投稿・フォロワー・チャンネルを保持する。別のSNSアカウントを作った際に投稿やフォロワーを自動的に移せるとは扱わない。
GoogleアカウントそのもののGmailアドレス変更可否と、SaaSが別メールボックスへ接続することも区別する。
Stripe等の契約名義・請求情報の変更はSNS連絡先とは別で、サービスごとの確認が必要。

## 公式資料（2026-09-26確認）

- [Google アカウントのメールアドレス変更](https://support.google.com/accounts/answer/19870?hl=ja)
- [Google アプリパスワード](https://support.google.com/accounts/answer/185833?hl=ja)
- [X 登録メールの変更](https://help.x.com/en/managing-your-account/how-to-update-your-email-address)
- [Instagram 登録先メールの更新](https://www.facebook.com/help/instagram/358911864194456?locale=en_GB)
- [YouTube ブランドアカウントの所有者・管理者](https://support.google.com/youtube/answer/4628007?hl=ja)
- [LINE公式アカウントの権限設定](https://www.lycbiz.com/jp/manual/OfficialAccountManager/account-settings_permission/)
- [LINE Developersのプロバイダー・チャネル権限](https://developers.line.biz/ja/docs/line-developers-console/managing-roles/)
