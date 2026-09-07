# 拡張機能の実装骨格（Phase 11〜14）

> **【使用時期】MVPでは不要。** v2〜v3で該当機能を作るときに開く。
> Phase 11（ツール実行）／12（外部トリガー）／13（多段承認）／14（定期実行）用。

Claude Codeに実装させる際、**この4つは自由に書かせると必ず事故る**ので、骨格を指定する。
以下をそのまま渡して「この構造に従って実装してください」と指示すること。

---

# 1. ツールレジストリ（Phase 11）

```python
# src/tools/registry.py
from typing import Callable, Literal
from pydantic import BaseModel

ToolKind = Literal["READ", "REVERSIBLE_WRITE", "IRREVERSIBLE"]


class IrreversibleActionInNodeError(RuntimeError):
    """部署ノード内から不可逆アクションが呼ばれた。設計違反。"""


class ToolSpec(BaseModel):
    name: str
    kind: ToolKind
    description: str
    args_schema: type[BaseModel]
    handler: Callable
    timeout_sec: int = 30
    max_calls_per_session: int = 5

    model_config = {"arbitrary_types_allowed": True}


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> None:
    if spec.name in _REGISTRY:
        raise ValueError(f"tool already registered: {spec.name}")
    _REGISTRY[spec.name] = spec


def get(name: str) -> ToolSpec:
    if name not in _REGISTRY:
        raise KeyError(f"unknown tool: {name}")
    return _REGISTRY[name]


def call_from_node(name: str, raw_args: dict, ctx: "SessionContext"):
    """部署ノードからのツール呼び出しは必ずここを通す。"""
    spec = get(name)

    # ① 不可逆アクションはノードから実行できない
    if spec.kind == "IRREVERSIBLE":
        raise IrreversibleActionInNodeError(
            f"{name} は ActionPlan を作成し、承認後に実行してください"
        )

    # ② 部署に許可されたツールか
    if name not in ctx.allowed_tools:
        raise PermissionError(f"{ctx.dept_id} に {name} は許可されていません")

    # ③ 呼び出し回数上限
    if ctx.tool_call_count(name) >= spec.max_calls_per_session:
        raise RuntimeError(f"{name} の呼び出し上限に達しました")

    # ④ 引数を型で検証（LLMの出力をそのまま渡さない）
    args = spec.args_schema.model_validate(raw_args)

    # ⑤ 可逆書き込みは冪等キーで重複実行を防ぐ
    if spec.kind == "REVERSIBLE_WRITE":
        key = ctx.make_idempotency_key(name, args)
        if not ctx.claim_idempotency_key(key):
            return ctx.previous_result(key)

    result = _run_with_timeout(spec.handler, args, spec.timeout_sec)
    ctx.record_tool_call(name, args, result)

    # ⑥ 返り値は信用できない入力として扱う
    return wrap_untrusted(result, source=name)


def wrap_untrusted(result: str, source: str) -> str:
    """ツール返り値をプロンプトへ埋める際の包み。
    区切りタグと同じ文字列が含まれていたらエスケープする。"""
    safe = result.replace("<tool_result", "&lt;tool_result")
    return f'<tool_result source="{source}">\n{safe}\n</tool_result>'
```

**allowlist照合の例（IDをLLMに自由に出させない）**

```python
class SheetReadArgs(BaseModel):
    sheet_id: str
    range: str

def handle_sheet_read(args: SheetReadArgs, config):
    if args.sheet_id not in config.allowed_sheet_ids:
        raise PermissionError("許可されていないシートIDです")
    # URL・SQL・コマンドをLLMの文字列から組み立てない
    return sheets_client.values_get(args.sheet_id, args.range)
```

---

# 2. 冪等な外部アクション実行（Phase 12）

**このシステムで最も事故りやすい箇所。** 順序を間違えると二重送信する。

```sql
CREATE TABLE executed_actions (
    idempotency_key TEXT PRIMARY KEY,      -- UNIQUE制約が本体
    session_id      TEXT NOT NULL,
    approval_id     TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    params          JSONB NOT NULL,
    state           TEXT NOT NULL,          -- CLAIMED / SUCCEEDED / FAILED
    result          JSONB,
    claimed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);
```

```python
# src/actions/executor.py

def execute_action(plan: ActionPlan, approval_id: str, session_id: str):
    spec = action_registry.get(plan.action_type)   # allowlist照合を兼ねる

    # ── ① 先に「実行する」と記録する（実行してから記録しない）──
    with db.transaction() as tx:
        row = tx.execute(
            """INSERT INTO executed_actions
                 (idempotency_key, session_id, approval_id, action_type, params, state)
               VALUES (%s, %s, %s, %s, %s, 'CLAIMED')
               ON CONFLICT (idempotency_key) DO NOTHING
               RETURNING idempotency_key""",
            (plan.idempotency_key, session_id, approval_id,
             plan.action_type, Json(plan.params)),
        ).fetchone()

    if row is None:
        # 既に誰かが実行を確保している = 二重実行を防いだ
        log.info("duplicate action suppressed", key=plan.idempotency_key)
        return ActionResult.duplicate()

    # ── ② ドライラン ──
    if settings.ACTION_DRY_RUN:
        _finish(plan.idempotency_key, "SUCCEEDED", {"dry_run": True})
        return ActionResult.dry_run(plan.preview)

    # ── ③ 緊急停止フラグ（再デプロイなしで止められること）──
    if not runtime_flags.actions_enabled():
        _finish(plan.idempotency_key, "FAILED", {"reason": "actions_disabled"})
        return ActionResult.blocked()

    # ── ④ 実行 ──
    try:
        result = spec.handler(plan.params)
        _finish(plan.idempotency_key, "SUCCEEDED", result)
        audit.log("action_executed", key=plan.idempotency_key, approval_id=approval_id)
        return ActionResult.ok(result)
    except Exception as e:
        # 送信済みか不明な失敗は絶対にリトライしない
        _finish(plan.idempotency_key, "FAILED", {"error": str(e)})
        audit.log("action_failed", key=plan.idempotency_key, error=str(e))
        notify_admin(plan, e)
        return ActionResult.failed(e)
```

**順序が命：** `INSERT（CLAIMED）` → `実行` → `state更新`。
実行してから記録すると、実行直後にプロセスが落ちたとき記録が残らず、再起動後にもう一度送信される。

---

# 3. 多段承認（Phase 13）

```sql
CREATE TABLE approvals (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    chain_id        TEXT NOT NULL,
    stage_index     INT  NOT NULL,
    stage_name      TEXT NOT NULL,
    required_count  INT  NOT NULL,
    status          TEXT NOT NULL,          -- PENDING / APPROVED / REJECTED / EXPIRED
    deadline_at     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, chain_id, stage_index)
);

CREATE TABLE approval_decisions (
    id          BIGSERIAL PRIMARY KEY,
    approval_id TEXT NOT NULL REFERENCES approvals(id),
    decided_by  TEXT NOT NULL,
    decision    TEXT NOT NULL,              -- APPROVE / REJECT
    comment     TEXT,
    decided_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (approval_id, decided_by)        -- 同一人物の二重投票を防ぐ
);
```

```python
def record_decision(approval_id: str, user_id: str, decision: str, comment: str = ""):
    with db.transaction() as tx:
        appr = tx.select_for_update("approvals", id=approval_id)

        # ① 既に決着済みなら弾く（古いボタンの再押下・二度押し）
        if appr.status != "PENDING":
            return DecisionResult.already_closed(appr.status)

        # ② 権限確認（この段の承認者か）
        stage = config.chain(appr.chain_id).stages[appr.stage_index]
        if user_id not in stage.approvers:
            return DecisionResult.forbidden()

        # ③ 同一人物の二重投票はUNIQUE制約で弾かれる
        try:
            tx.insert("approval_decisions", approval_id=approval_id,
                      decided_by=user_id, decision=decision, comment=comment)
        except UniqueViolation:
            return DecisionResult.already_voted()

        # ④ 却下は即座に全体終了。後段へ進めない
        if decision == "REJECT":
            tx.update("approvals", id=approval_id, status="REJECTED")
            return DecisionResult.rejected(comment)

        # ⑤ 必要数に達したら次段へ
        count = tx.count("approval_decisions",
                         approval_id=approval_id, decision="APPROVE")
        if count >= appr.required_count:
            tx.update("approvals", id=approval_id, status="APPROVED")
            next_stage = appr.stage_index + 1
            if next_stage < len(config.chain(appr.chain_id).stages):
                create_stage(tx, appr.session_id, appr.chain_id, next_stage)
                return DecisionResult.advanced(next_stage)
            return DecisionResult.completed()   # → on_approved フックへ

        return DecisionResult.waiting(count, appr.required_count)
```

**注意点**
- 段数は最大3。それ以上は承認待ちで詰まる
- Slack通知は**その段の承認者だけにメンション**する
- 各段のタイムアウトは独立。超過したら**自動却下**（自動承認は作らない）

---

# 4. スケジュール起動（Phase 14）

```sql
CREATE TABLE schedule_runs (
    schedule_id     TEXT NOT NULL,
    scheduled_for   TIMESTAMPTZ NOT NULL,
    session_id      TEXT,
    status          TEXT NOT NULL,          -- CLAIMED / SUCCEEDED / FAILED
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (schedule_id, scheduled_for)   -- 二重起動をDBで防ぐ
);
```

```python
# src/worker/scheduler.py  （5分間隔で起動）

def tick(now: datetime):
    for sch in config.schedules:
        if not runtime_flags.schedule_enabled(sch.id):
            continue

        due = last_due_time(sch.cron, sch.timezone, now)
        if due is None:
            continue

        # ① 溜まった分をまとめて実行しない（直近1回だけ）
        #    プロセス停止中に過ぎた時刻の分を全部流すと、起動時に大量課金される
        if now - due > timedelta(hours=sch.max_delay_hours or 2):
            log.warning("schedule skipped (too late)", id=sch.id, due=due)
            record_skip(sch.id, due)
            continue

        # ② 二重起動をDBで防ぐ
        if not claim_run(sch.id, due):
            continue

        # ③ 同時実行数の上限
        if running_count() >= settings.MAX_CONCURRENT_SCHEDULES:
            release_run(sch.id, due)
            continue

        try:
            session_id = start_graph(
                goal=sch.goal,
                channel=sch.channel,
                requester_id=sch.requester_id,
                budget_override=sch.max_budget_usd,   # スケジュール専用の上限
                source="SCHEDULE",
            )
            mark_run(sch.id, due, "SUCCEEDED", session_id)
            reset_failure_count(sch.id)
        except Exception as e:
            mark_run(sch.id, due, "FAILED")
            n = increment_failure_count(sch.id)
            # ④ 3回連続失敗で自動無効化
            #    壊れたまま毎日課金され続けるのを防ぐ
            if n >= 3:
                runtime_flags.disable_schedule(sch.id)
                notify_admin(f"スケジュール {sch.id} を3回連続失敗のため停止しました")
```

**スケジュール実行でも承認は必須。**
起動 → 生成 → 承認待ち → 人間が確認 → 確定。承認されなければ期限切れで自動却下。
「無人で走って無人で外に出る」経路を作らない。

---

# Claude Codeへの渡し方

```
拡張機能を実装します。reference_impl.md の「1. ツールレジストリ」の構造に従って、
src/tools/registry.py を実装してください。

守ること：
- 3段階分類（READ / REVERSIBLE_WRITE / IRREVERSIBLE）を変えない
- IRREVERSIBLE がノードから呼ばれたら例外を投げる
- LLMの出力から URL・SQL・シェルコマンドを組み立てない
- ID類は必ず設定のallowlistと照合する

実装後、以下のテストを書いて実行してください：
（仕様書 Phase 11-5 のテスト項目）
```

1ファイルずつ、テストを通しながら進めること。4つ同時に実装させない。
