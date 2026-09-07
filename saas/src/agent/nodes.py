import logging
import uuid

from langgraph.graph import END
from langgraph.types import interrupt

from src.agent.config_models import AppConfig
from src.agent.cost import compute_cost_usd
from src.agent.llm import LLMFatalError, LLMOutputError, StructuredLLM
from src.agent.qa import check_forbidden, integrate_qa_verdict
from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.agent.state import OrgState
from src.core.db import async_session_factory
from src.rag.embeddings import EmbeddingProvider
from src.rag.prompt_safety import render_retrieved_context
from src.rag.search import hybrid_search

logger = logging.getLogger(__name__)

MAX_ROUTING_RETRY = 1
_LLM_ERRORS = (LLMOutputError, LLMFatalError)

# 注意: このモジュールの関数はキーワード引数名に "config" を使わない。
# LangGraphはノード呼び出し時にRunnableConfig(実行時の設定dict)を
# 予約キーワード "config" で渡すため、同名のキーワード引数を定義すると
# functools.partialで束縛したはずのAppConfigがRunnableConfigに上書きされる。
# そのため、業種設定(AppConfig)は "app_config" という名前で受け渡す。


class _CostTracker:
    """1回のノード呼び出し内で複数回LLMを呼ぶ場合の実測コストを積算する(4-1)。"""

    def __init__(self, app_config: AppConfig):
        self._pricing = app_config.pricing
        self.total = 0.0

    def add(self, model: str, usage: dict) -> None:
        self.total += compute_cost_usd(model, usage, self._pricing)


def _check_stoppers(state: OrgState, app_config: AppConfig) -> dict | None:
    """4-2 3つの安全弁。呼び出し直前にチェックし、超過していたらHALTEDで返す。"""
    limits = app_config.limits
    cost_usd = state.get("cost_usd", 0.0) or 0.0
    total_steps = state.get("total_steps", 0) or 0
    retry_counts = state.get("retry_counts", {}) or {}

    if cost_usd >= limits.max_budget_usd:
        return {
            "status": "HALTED",
            "error_message": f"予算上限に達したため中断しました(使用額 ${cost_usd:.4f} / 上限 ${limits.max_budget_usd:.2f})",
            "next_depts": [],
        }
    if total_steps >= limits.max_total_steps:
        return {
            "status": "HALTED",
            "error_message": f"ステップ数上限に達したため中断しました({total_steps} / {limits.max_total_steps})",
            "next_depts": [],
        }
    over_dept = next((d for d, c in retry_counts.items() if c > limits.max_retries_per_dept), None)
    if over_dept is not None:
        # 通常は2-7の緩やかな救済(警告付きで承認へ進める)で先に処理されるため、
        # ここに到達するのは想定外の経路のみ。最後の安全網としてHALTEDにする。
        return {
            "status": "HALTED",
            "error_message": (
                f"部署'{over_dept}'の差し戻し回数が上限を超えました"
                f"({retry_counts[over_dept]} / {limits.max_retries_per_dept})"
            ),
            "next_depts": [],
        }
    return None


def _dept_candidates(app_config: AppConfig, board: dict) -> list[str]:
    """依存関係が解決済みで、まだ成果物がない部署のIDを返す(ceo_office/qa_auditorは除外)。"""
    candidates = []
    for dept_id, dept in app_config.departments.items():
        if dept_id in ("ceo_office", "qa_auditor"):
            continue
        if board.get(dept_id):
            continue
        if all(dep in board and board[dep] for dep in dept.depends_on):
            candidates.append(dept_id)
    return candidates


def _build_routing_prompt(state: OrgState, *, app_config: AppConfig, goal: str) -> str:
    board = state.get("board", {}) or {}
    candidates = _dept_candidates(app_config, board)
    lines = [
        f"【会社情報】\n{app_config.company.context}",
        f"【依頼内容】\n{goal}",
    ]
    if state.get("clarifications"):
        clar = "\n".join(f"- {k}: {v}" for k, v in state["clarifications"].items())
        lines.append(f"【確認済みの追加情報】\n{clar}")
    done = {d: t for d, t in board.items() if t}
    if done:
        text = "\n\n".join(f"### {d}\n{t}" for d, t in done.items())
        lines.append(f"【これまでの成果物】\n{text}")
    if state.get("qa_findings"):
        lines.append(f"【QAの指摘】\n{state['qa_findings']}")
    if state.get("rejection_comment"):
        lines.append(f"【人間からの却下理由】\n{state['rejection_comment']}")
    lines.append(f"【選択可能な部署(依存関係が解決済み)】\n{candidates}")
    lines.append(
        f"【聞き返し済み回数】{state.get('clarify_count', 0)} / 上限{app_config.limits.max_clarify_rounds}"
    )
    return "\n\n".join(lines)


async def _build_approval_summary(
    state: OrgState, *, app_config: AppConfig, llm: StructuredLLM, cost: _CostTracker
) -> ApprovalSummary:
    dept = app_config.departments["ceo_office"]
    board = state.get("board", {}) or {}
    deliverables_text = "\n\n".join(f"### {d}\n{t}" for d, t in board.items() if t)
    qa_report = "合格" if state.get("qa_verdict") == "PASS" else "警告付きで進行(差し戻し上限到達)"
    user_message = (
        f"【成果物】\n{deliverables_text}\n\n"
        f"【QA結果】{qa_report}\n"
        f"【QAの警告】{state.get('qa_warnings', [])}"
    )
    summary, usage = await llm.call_structured(
        model=dept.model,
        system_prompt=dept.system_prompt,
        user_message=user_message,
        output_model=ApprovalSummary,
    )
    cost.add(dept.model, usage)
    return summary


async def _route(
    state: OrgState,
    *,
    app_config: AppConfig,
    llm: StructuredLLM,
    goal: str,
    cost: _CostTracker,
    retry: int = 0,
) -> dict:
    dept = app_config.departments["ceo_office"]
    user_message = _build_routing_prompt(state, app_config=app_config, goal=goal)

    try:
        decision, usage = await llm.call_structured(
            model=dept.model,
            system_prompt=dept.system_prompt,
            user_message=user_message,
            output_model=SupervisorDecision,
        )
    except _LLM_ERRORS as e:
        return {
            "status": "ERROR",
            "error_message": str(e),
            "goal": goal,
            "next_depts": [],
            "cost_usd": cost.total,
        }
    cost.add(dept.model, usage)

    valid_dept_ids = set(app_config.departments) - {"ceo_office"}
    invalid = [d for d in decision.target_depts if d not in valid_dept_ids]

    if decision.verdict in ("ASSIGN", "REVISE") and invalid:
        if retry >= MAX_ROUTING_RETRY:
            return {
                "status": "ERROR",
                "error_message": f"統括AIが存在しない部署IDを返しました: {invalid}",
                "goal": goal,
                "next_depts": [],
                "cost_usd": cost.total,
            }
        return await _route(state, app_config=app_config, llm=llm, goal=goal, cost=cost, retry=retry + 1)

    verdict = decision.verdict
    clarify_count = state.get("clarify_count", 0)
    board = state.get("board", {}) or {}

    # clarifyの往復上限に達していたら強制的にASSIGNへ倒す(無限ループ防止)
    if verdict == "CLARIFY" and clarify_count >= app_config.limits.max_clarify_rounds:
        verdict = "ASSIGN"
        if not decision.target_depts:
            decision.target_depts = _dept_candidates(app_config, board)

    qa_verdict = state.get("qa_verdict", "PENDING")
    # 社長AIはQAの判定を覆せない(コード側の防御)
    if verdict == "SUMMARIZE" and qa_verdict != "PASS":
        verdict = "REVISE"
        if not decision.target_depts:
            # SUMMARIZEのときはtarget_deptsが空なのが通常なので、
            # QAが検査した(=boardに成果物がある)部署全体を再指名する
            decision.target_depts = [d for d, t in board.items() if t]

    if verdict == "CLARIFY":
        return {
            "goal": goal,
            "status": "CLARIFYING",
            "pending_questions": decision.missing_info[:3],
            "next_depts": [],
            "step_history": ["ceo_office: CLARIFY"],
            "total_steps": 1,
            "cost_usd": cost.total,
        }

    if verdict in ("ASSIGN", "REVISE"):
        targets = decision.target_depts or _dept_candidates(app_config, board)

        if verdict == "REVISE" and targets:
            # 部署ごとに上限判定する(all()で判定すると、他の部署と同時指名された
            # ときに1部署だけ上限を超えてループし続けるバグになるため、個別に見る)
            retry_counts = state.get("retry_counts", {}) or {}
            max_retries = app_config.limits.max_retries_per_dept
            revisable = [d for d in targets if retry_counts.get(d, 0) < max_retries]
            exhausted_depts = [d for d in targets if d not in revisable]

            if not revisable:
                # 対象部署の全てが上限に達した: 警告を付けたまま承認へ進める(HALTEDにしない)
                summary = await _build_approval_summary(state, app_config=app_config, llm=llm, cost=cost)
                return {
                    "goal": goal,
                    "status": "AWAITING_APPROVAL",
                    "qa_verdict": "PASS",
                    "qa_warnings": (state.get("qa_findings", []) or [])
                    + (state.get("qa_warnings", []) or []),
                    "approval_summary": summary.model_dump(),
                    "next_depts": [],
                    "step_history": [f"ceo_office: 差し戻し上限到達({exhausted_depts}) -> 警告付きで承認へ"],
                    "total_steps": 1,
                    "cost_usd": cost.total,
                }

            targets = revisable

        if not targets:
            # 実際のClaude APIでの動作確認中に発見: QAがPASSを返した直後、統括AIが
            # SUMMARIZEではなくASSIGN/REVISE(対象部署は空)を誤って返すことがある。
            # 「SUMMARIZEなのにQA未PASS」は既にコード側で防御しているので(181行目)、
            # 対称的にこちらも防御する: QAが既にPASSしていて成果物があるなら、
            # ERRORにせずSUMMARIZE相当の承認要求として扱う(180行目と同じ思想)。
            if qa_verdict == "PASS" and any(t for t in board.values()):
                summary = await _build_approval_summary(state, app_config=app_config, llm=llm, cost=cost)
                return {
                    "goal": goal,
                    "status": "AWAITING_APPROVAL",
                    "approval_summary": summary.model_dump(),
                    "next_depts": [],
                    "step_history": [
                        f"ceo_office: {verdict}(対象部署なし)だがQA合格済み -> 承認へ"
                    ],
                    "total_steps": 1,
                    "cost_usd": cost.total,
                }
            return {
                "status": "ERROR",
                "error_message": "実行可能な部署がありません(全部署が依存未解決または完了済み)",
                "goal": goal,
                "next_depts": [],
                "cost_usd": cost.total,
            }

        # 同時実行数の上限(3-2 実装上の注意4): 超えた分は次のルーティングで改めて選ばせる
        max_parallel = app_config.limits.max_parallel_depts
        deferred_note = ""
        if len(targets) > max_parallel:
            deferred = targets[max_parallel:]
            targets = targets[:max_parallel]
            deferred_note = f"(同時実行数上限{max_parallel}のため、{deferred}は次回以降に見送り)"

        new_retry_counts = dict(state.get("retry_counts", {}) or {})
        if verdict == "REVISE":
            for d in targets:
                new_retry_counts[d] = new_retry_counts.get(d, 0) + 1

        return {
            "goal": goal,
            "status": "RUNNING",
            "retry_counts": new_retry_counts,
            "instruction": decision.instruction,
            "next_depts": targets,
            "step_history": [f"ceo_office: {verdict} -> {targets}{deferred_note}"],
            "total_steps": 1,
            "cost_usd": cost.total,
        }

    # SUMMARIZE (qa_verdict == PASS のときのみ到達する)
    summary = await _build_approval_summary(state, app_config=app_config, llm=llm, cost=cost)
    return {
        "goal": goal,
        "status": "AWAITING_APPROVAL",
        "approval_summary": summary.model_dump(),
        "next_depts": [],
        "step_history": ["ceo_office: SUMMARIZE -> 承認待ち"],
        "total_steps": 1,
        "cost_usd": cost.total,
    }


async def ceo_office_node(state: OrgState, *, app_config: AppConfig, llm: StructuredLLM) -> dict:
    stopper = _check_stoppers(state, app_config)
    if stopper is not None:
        return stopper

    dept = app_config.departments["ceo_office"]
    cost = _CostTracker(app_config)

    # 差し戻し・却下で戻ってきたときは goal が既にあるので分類をスキップする
    if not state.get("goal"):
        try:
            triage, usage = await llm.call_structured(
                model=dept.triage_model or dept.model,
                system_prompt=dept.triage_prompt or "",
                user_message=state["raw_message"],
                output_model=Triage,
            )
        except _LLM_ERRORS as e:
            return {"status": "ERROR", "error_message": str(e), "next_depts": []}
        cost.add(dept.triage_model or dept.model, usage)

        if triage.intent in ("GREETING", "FAQ"):
            return {
                "status": "COMPLETED",
                "approval_summary": {"reply": triage.reply or ""},
                "next_depts": [],
                "step_history": [f"ceo_office: {triage.intent} -> 即時回答"],
                "total_steps": 1,
                "cost_usd": cost.total,
            }

        goal = triage.goal or state["raw_message"]
    else:
        goal = state["goal"]

    return await _route(state, app_config=app_config, llm=llm, goal=goal, cost=cost)


async def _retrieve_rag_context(
    *, state: OrgState, embedding_provider: EmbeddingProvider, dept_id: str
) -> tuple[str, list[dict]]:
    """社内資料検索(RAG)を行い、(プロンプトに埋め込むテキスト, 出典citationのリスト)を返す。
    検索・埋め込みに失敗しても部署の成果物生成自体は止めない(空のRAG結果として続行する)。

    7-5-4: 社長AI(ceo_office)はこの関数を呼ばない設計にする(ルーティング判断に影響させない)。
    """
    query = f"{state.get('goal', '')}\n{state.get('instruction', '')}".strip()
    if not query:
        return "", []

    try:
        vectors = await embedding_provider.embed([query])
        async with async_session_factory() as db:
            results = await hybrid_search(
                db,
                tenant_id=uuid.UUID(state["tenant_id"]),
                query_text=query,
                query_embedding=vectors[0],
                top_k=5,
            )
    except Exception:
        logger.exception("RAG検索に失敗しました(dept_id=%s)。RAGなしで続行します", dept_id)
        return "", []

    if not results:
        return "", []

    context = render_retrieved_context(results)
    citations = [
        {
            "dept_id": dept_id,
            "document_id": str(r.document_id),
            "title": r.document_title,
            "valid_until": r.valid_until.isoformat() if r.valid_until else None,
        }
        for r in results
    ]
    return context, citations


def make_dept_node(
    dept_id: str, *, app_config: AppConfig, llm: StructuredLLM, embedding_provider: EmbeddingProvider | None = None
):
    """部署ノードを生成する。dept_id/dept はこの関数呼び出し時点の引数として束縛されるため、
    ループで複数生成しても全ノードが最後の部署になるバグは起きない。

    embedding_providerがNoneの顧客(OpenAI APIキー未設定)は、RAG検索を単にスキップする
    (RAGはオプション機能。Anthropicキーと同じ「顧客自身が使う分だけ契約する」方式)。
    """
    dept = app_config.departments[dept_id]

    async def _node(state: OrgState) -> dict:
        stopper = _check_stoppers(state, app_config)
        if stopper is not None:
            return stopper

        company = app_config.company
        parts = [
            f"【会社情報】\n{company.context}",
            f"【依頼内容】\n{state.get('goal', '')}",
        ]
        if state.get("instruction"):
            parts.append(f"【統括AIからの指示】\n{state['instruction']}")
        if state.get("clarifications"):
            clar = "\n".join(f"- {k}: {v}" for k, v in state["clarifications"].items())
            parts.append(f"【確認済みの追加情報】\n{clar}")
        if state.get("rejection_comment"):
            parts.append(f"【差し戻し・却下理由】\n{state['rejection_comment']}")

        citations: list[dict] = []
        if embedding_provider is not None:
            rag_context, citations = await _retrieve_rag_context(
                state=state, embedding_provider=embedding_provider, dept_id=dept_id
            )
            if rag_context:
                parts.append(rag_context)

        try:
            text, usage = await llm.generate_text(
                model=dept.model,
                system_prompt=dept.system_prompt,
                user_message="\n\n".join(parts),
            )
        except Exception as e:
            # 3-2 実装上の注意5: 並列実行中の1部署の失敗が他部署の成果物を消してはならない。
            # boardには書き込まない(=未完了のまま残し、次のルーティングで再指名の対象にする)。
            return {
                "step_history": [f"{dept_id}: 生成に失敗しました({e})"],
                "total_steps": 1,
            }

        return {
            "board": {dept_id: text},
            "citations": citations,
            "step_history": [f"{dept_id}: 成果物を生成"],
            "total_steps": 1,
            "cost_usd": compute_cost_usd(dept.model, usage, app_config.pricing),
        }

    return _node


async def join_node(state: OrgState) -> None:
    return None


async def qa_auditor_node(state: OrgState, *, app_config: AppConfig, llm: StructuredLLM) -> dict:
    stopper = _check_stoppers(state, app_config)
    if stopper is not None:
        return stopper

    dept = app_config.departments["qa_auditor"]
    board = state.get("board", {}) or {}
    done = {d: t for d, t in board.items() if t}

    forbidden_hits = {
        dept_id: check_forbidden(text, app_config.compliance.forbidden_words)
        for dept_id, text in done.items()
    }

    deliverables_text = "\n\n".join(f"### {d}\n{t}" for d, t in done.items())

    try:
        qa_result, usage = await llm.call_structured(
            model=dept.model,
            system_prompt=dept.system_prompt,
            user_message=f"【検査対象の成果物】\n{deliverables_text}",
            output_model=QaResult,
        )
    except _LLM_ERRORS as e:
        return {"status": "ERROR", "error_message": str(e)}

    citation_document_ids: dict[str, list[str]] = {}
    for c in state.get("citations", []) or []:
        citation_document_ids.setdefault(c["dept_id"], []).append(c["document_id"])

    threshold = dept.quality_fail_threshold or 2
    verdict, findings, warnings = integrate_qa_verdict(
        forbidden_hits=forbidden_hits,
        fact_check=qa_result.fact_check,
        quality_findings=qa_result.quality_findings,
        quality_fail_threshold=threshold,
        citation_document_ids=citation_document_ids,
        board=done,
    )

    return {
        "qa_verdict": verdict,
        "qa_findings": findings,
        "qa_warnings": warnings,
        "step_history": [f"qa_auditor: {verdict}"],
        "total_steps": 1,
        "cost_usd": compute_cost_usd(dept.model, usage, app_config.pricing),
    }


async def clarify_node(state: OrgState) -> dict:
    questions = state.get("pending_questions", []) or []
    answer = interrupt({"questions": questions})

    clarifications = dict(state.get("clarifications", {}) or {})
    if isinstance(answer, dict):
        clarifications.update(answer)
    else:
        clarifications[f"回答{len(clarifications) + 1}"] = str(answer)

    return {
        "clarifications": clarifications,
        "clarify_count": state.get("clarify_count", 0) + 1,
        "pending_questions": [],
        "status": "RUNNING",
        "step_history": ["clarify: 回答を受領"],
        "total_steps": 1,
    }


def on_approved(state: OrgState, approval_id: str | None = None) -> None:
    """承認確定後に呼ばれる(6-5)。Phase 12までは何もしない。
    ここに外部アクションの実行を差し込む。呼ばれていることだけテストで確認する。"""
    pass


async def approval_node(state: OrgState, *, app_config: AppConfig) -> dict:
    decision = interrupt({"approval_summary": state.get("approval_summary", {})})

    if isinstance(decision, dict) and decision.get("decision") == "approve":
        on_approved(state)
        return {
            "status": "APPROVED",
            "step_history": ["approval: 承認"],
        }

    if isinstance(decision, dict) and decision.get("decision") == "expire":
        # 6-3: 期限切れは人間の却下と違い、再実行せず即座に終了する("自動却下して終了")
        return {
            "status": "HALTED",
            "error_message": "承認期限を超過したため、このセッションは自動的に終了しました。",
            "step_history": ["approval: 期限切れ -> HALTED"],
        }

    comment = decision.get("comment", "") if isinstance(decision, dict) else str(decision)
    rejection_count = state.get("rejection_count", 0) + 1

    if rejection_count >= app_config.limits.max_rejections:
        # 6-3-2: 却下上限に達したら自動で再実行しない。HALTEDにして終了する
        return {
            "status": "HALTED",
            "rejection_count": rejection_count,
            "rejection_comment": comment,
            "rejection_history": [comment],
            "error_message": (
                f"{rejection_count}回の修正を行いましたが、ご要望に沿えていないようです。"
                "このセッションは一旦終了します。新しくご依頼ください。"
            ),
            "step_history": [f"approval: 却下上限({app_config.limits.max_rejections})到達 -> HALTED"],
        }

    return {
        "status": "RUNNING",
        "rejection_count": rejection_count,
        "rejection_comment": comment,
        "rejection_history": [comment],
        "next_depts": [],
        "step_history": [f"approval: 却下({rejection_count}回目) -> 社長AIへ差し戻し"],
    }


def route_from_ceo_office(state: OrgState):
    """ceo_officeノードの直後の条件付きエッジ。状態から次のノードを決める。"""
    status = state.get("status")
    if status == "CLARIFYING":
        return "clarify"
    if status == "AWAITING_APPROVAL":
        return "approval"
    if status == "RUNNING":
        return state.get("next_depts") or END
    return END


def route_from_approval(state: OrgState):
    """approvalノードの直後の条件付きエッジ。却下時はceo_officeに戻り、それ以外はENDへ。"""
    if state.get("status") == "RUNNING":
        return "ceo_office"
    return END
