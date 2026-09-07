"""Phase 11: ツール実行機構。骨格はdocs/reference_impl.md「1. ツールレジストリ」に従う
(自由に書かせると二重実行・引数インジェクション等で必ず事故る、と明記されているため)。

3段階分類(11-1)がこの機構全体の安全性を決める:
- READ: 外部状態を変えない。部署ノード内で即実行してよい
- REVERSIBLE_WRITE: 変えるが10秒で取り消せる。部署ノード内で実行してよい(冪等キー必須)
- IRREVERSIBLE: 取り消せない。部署ノードから直接は呼べない(Phase 12で承認後にのみ実行する)
"""

import asyncio
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel

ToolKind = Literal["READ", "REVERSIBLE_WRITE", "IRREVERSIBLE"]


class IrreversibleActionInNodeError(RuntimeError):
    """部署ノード内から不可逆アクションが呼ばれた。設計違反
    (Phase 12でActionPlanを作り、承認後にワーカーが実行する)。"""


class ToolPermissionError(PermissionError):
    """その部署に許可されていないツールを呼ぼうとした。"""


class ToolCallLimitError(RuntimeError):
    """1セッションあたりの呼び出し回数上限に達した(暴走防止、11-4)。"""


class UnknownToolError(KeyError):
    pass


class ToolSpec(BaseModel):
    name: str
    kind: ToolKind
    description: str
    args_schema: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[str]]
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
        raise UnknownToolError(f"unknown tool: {name}")
    return _REGISTRY[name]


def all_tool_names() -> set[str]:
    return set(_REGISTRY.keys())


def reset_registry_for_tests() -> None:
    """テスト専用。プロセス内グローバルレジストリを空に戻す(テスト間の汚染防止)。"""
    _REGISTRY.clear()


class SessionToolContext:
    """1部署ノードの1回の呼び出しの間、ツール呼び出し履歴の参照・記録に使う。

    call_history(過去のノード呼び出し・リトライを含む、そのセッション全体の記録)を
    OrgState側から受け取り、呼び出し回数の集計・冪等キーの重複判定に使う。
    このセッション中に新しく記録した分はnew_callsに溜め、呼び出し元(部署ノード)が
    OrgStateへ書き戻す(state.pyのreducerでtool_callsに追記される想定)。
    """

    def __init__(self, *, dept_id: str, allowed_tools: set[str], call_history: list[dict] | None = None):
        self.dept_id = dept_id
        self.allowed_tools = allowed_tools
        self._history = list(call_history or [])
        self.new_calls: list[dict] = []

    def all_calls(self) -> list[dict]:
        """これまでの履歴 + このコンテキストで新しく記録した分。次のノード呼び出し
        (再実行・リトライ)にcall_historyとして引き継ぐ際は、これを渡す。"""
        return self._history + self.new_calls

    def tool_call_count(self, name: str) -> int:
        return sum(1 for c in self.all_calls() if c["tool"] == name)

    def make_idempotency_key(self, name: str, args: BaseModel) -> str:
        import hashlib
        import json

        payload = json.dumps(args.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(f"{self.dept_id}:{name}:{payload}".encode()).hexdigest()

    def claim_idempotency_key(self, key: str) -> bool:
        """まだ実行されていなければTrue(呼び出し元は実行してよい)。
        既に実行済みならFalse(呼び出し元は前回の結果を使い回す)。"""
        return not any(c.get("idempotency_key") == key for c in self.all_calls())

    def previous_result(self, key: str) -> str | None:
        for c in self.all_calls():
            if c.get("idempotency_key") == key:
                return c["result"]
        return None

    def record_tool_call(self, name: str, args: BaseModel, result: str, idempotency_key: str | None) -> None:
        self.new_calls.append(
            {
                "tool": name,
                "args": args.model_dump(mode="json"),
                "result": result,
                "idempotency_key": idempotency_key,
            }
        )


def wrap_untrusted(result: str, *, source: str) -> str:
    """ツール返り値をプロンプトへ埋める際の包み(11-4: 返り値は信用できない入力として扱う。
    src/rag/prompt_safety.pyと同じ考え方)。半角<>を全角に変換し、tool_resultタグに
    限らずどんな偽タグも構造的に作れないようにする(区切りタグの文字列だけを個別に
    エスケープする方式だと、tool_result以外の偽タグ(<system>等)を見逃す)。"""
    safe = str(result).replace("<", "＜").replace(">", "＞")
    return f'<tool_result source="{source}">\n{safe}\n</tool_result>'


async def call_from_node(name: str, raw_args: dict, ctx: SessionToolContext) -> str:
    """部署ノードからのツール呼び出しは必ずここを通す。"""
    spec = get(name)

    if spec.kind == "IRREVERSIBLE":
        raise IrreversibleActionInNodeError(
            f"{name} はActionPlanを作成し、承認後に実行してください(部署ノードから直接は呼べません)"
        )

    if name not in ctx.allowed_tools:
        raise ToolPermissionError(f"{ctx.dept_id} に {name} は許可されていません")

    if ctx.tool_call_count(name) >= spec.max_calls_per_session:
        raise ToolCallLimitError(f"{name} の呼び出し上限({spec.max_calls_per_session}回)に達しました")

    # LLMの出力をそのまま渡さず、型で検証する(11-3)
    args = spec.args_schema.model_validate(raw_args)

    idempotency_key: str | None = None
    if spec.kind == "REVERSIBLE_WRITE":
        idempotency_key = ctx.make_idempotency_key(name, args)
        if not ctx.claim_idempotency_key(idempotency_key):
            return wrap_untrusted(ctx.previous_result(idempotency_key) or "", source=name)

    # 11-4: 失敗時は部署ノードを止めず、「取得できませんでした」として成果物に明記させる
    try:
        result = await asyncio.wait_for(spec.handler(args), timeout=spec.timeout_sec)
    except asyncio.TimeoutError:
        result = f"（ツール呼び出しがタイムアウトしました: {name}）"
    except Exception as e:
        result = f"（ツール呼び出しに失敗しました: {name}: {e}）"

    ctx.record_tool_call(name, args, result, idempotency_key)
    return wrap_untrusted(result, source=name)
