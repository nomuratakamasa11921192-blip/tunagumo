import pytest
from langgraph.types import Command

from src.agent.graph import build_graph
from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.agent.state import new_state
from tests.fakes import FakeLLM

pytestmark = pytest.mark.asyncio(loop_scope="session")


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def test_greeting_does_not_start_departments(config):
    llm = FakeLLM()
    llm.queue_structured(Triage(intent="GREETING", reply="こんにちは！", reason="挨拶"))
    graph = build_graph(config, llm=llm)

    state = new_state("tenant-a", "s1", "user-1", "おはようございます")
    result = await graph.ainvoke(state, config=thread_config("tenant-a:s1"))

    assert result["status"] == "COMPLETED"
    assert llm.text_calls == []  # 部署は一度も呼ばれない


async def test_ambiguous_goal_stops_for_clarification(config):
    llm = FakeLLM()
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="販促をやりたい", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(
            verdict="CLARIFY",
            missing_info=["対象クライアント", "予算規模", "納期"],
            reason="情報不足",
        )
    )
    graph = build_graph(config, llm=llm)

    cfg = thread_config("tenant-a:s2")
    state = new_state("tenant-a", "s2", "user-1", "販促やって")
    result = await graph.ainvoke(state, config=cfg)

    assert result["status"] == "CLARIFYING"
    snapshot = await graph.aget_state(cfg)
    assert snapshot.next == ("clarify",)
    assert snapshot.tasks[0].interrupts[0].value["questions"] == [
        "対象クライアント",
        "予算規模",
        "納期",
    ]


async def test_invalid_department_id_causes_error(config):
    llm = FakeLLM()
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="謎の依頼", reason="業務依頼"))
    # 2回とも存在しない部署IDを返す(1回リトライ後もダメならERROR)
    llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["nonexistent_dept"], instruction="x", reason="x")
    )
    llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["nonexistent_dept"], instruction="x", reason="x")
    )
    graph = build_graph(config, llm=llm)

    state = new_state("tenant-a", "s3", "user-1", "何かやって")
    result = await graph.ainvoke(state, config=thread_config("tenant-a:s3"))

    assert result["status"] == "ERROR"


async def test_single_department_request_starts_only_that_department(config):
    llm = FakeLLM()
    llm.queue_structured(
        Triage(intent="WORK_REQUEST", goal="契約書のリスク確認をしてほしい", reason="業務依頼")
    )
    llm.queue_structured(
        SupervisorDecision(
            verdict="ASSIGN",
            target_depts=["planning_dept"],
            instruction="契約書のリスクを確認してください",
            reason="単一部署で足りる",
        )
    )
    llm.queue_text("## クライアントの状況(推定)\n- 検証済み")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(
        SupervisorDecision(verdict="SUMMARIZE", reason="QA合格")
    )
    llm.queue_structured(
        ApprovalSummary(headline="リスク確認完了", qa_result="合格")
    )
    graph = build_graph(config, llm=llm)

    state = new_state("tenant-a", "s4", "user-1", "契約書のリスク確認をお願いします")
    result = await graph.ainvoke(state, config=thread_config("tenant-a:s4"))

    assert result["status"] == "AWAITING_APPROVAL"
    assert set(result["board"].keys()) == {"planning_dept"}
    assert len(llm.text_calls) == 1  # planning_dept以外は起動していない


async def test_forbidden_word_causes_revision(config):
    llm = FakeLLM()
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="LPの見出しを作ってほしい", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(
            verdict="ASSIGN", target_depts=["copy_dept"], instruction="LPの見出しを3案", reason="x"
        )
    )
    llm.queue_text("業界最安値のLPです")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    # 禁止ワードで機械チェックFAIL -> ceo_officeに戻り、copy_deptだけをREVISE指名
    llm.queue_structured(
        SupervisorDecision(
            verdict="REVISE", target_depts=["copy_dept"], instruction="禁止ワードを削除して", reason="機械チェックFAIL"
        )
    )
    llm.queue_text("通常のLPコピーです")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="LPコピー完了", qa_result="合格"))

    graph = build_graph(config, llm=llm)
    state = new_state("tenant-a", "s5", "user-1", "LPの見出しを作って")
    result = await graph.ainvoke(state, config=thread_config("tenant-a:s5"))

    # 1回目は禁止ワードでFAILし、copy_deptが再実行されたこと(差し戻し)を確認する
    assert result["retry_counts"]["copy_dept"] == 1
    assert len(llm.text_calls) == 2
    assert result["status"] == "AWAITING_APPROVAL"
    assert result["qa_verdict"] == "PASS"


async def test_summarize_rejected_when_qa_not_pass(config):
    llm = FakeLLM()
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="コピーを作って", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["copy_dept"], instruction="x", reason="x")
    )
    llm.queue_text("普通のコピーです")
    llm.queue_structured(
        QaResult(
            fact_check=[
                {
                    "dept_id": "copy_dept",
                    "verdict": "FAIL",
                    "issue": "架空の実績",
                    "evidence": "売上10億円達成",
                }
            ],
            quality_findings=[],
        )
    )
    # 社長AIがQA不合格にもかかわらずSUMMARIZEを返す(不正な判断)
    # -> Python側でREVISEに強制変換され、boardにある部署(copy_dept)が再指名される
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="(誤った判断)"))
    llm.queue_text("実績の記載を削除したコピーです")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="コピー完了", qa_result="合格"))

    graph = build_graph(config, llm=llm)
    state = new_state("tenant-a", "s6", "user-1", "コピーを作って")
    result = await graph.ainvoke(state, config=thread_config("tenant-a:s6"))

    # SUMMARIZEは一度もそのまま承認へ進まず、必ずREVISEを経由したことを確認する
    assert result["retry_counts"]["copy_dept"] == 1
    assert len(llm.text_calls) == 2
    assert result["status"] == "AWAITING_APPROVAL"


async def test_assign_with_no_targets_after_qa_pass_proceeds_to_approval_instead_of_error(config):
    """実際のClaude API利用時に発見した挙動の再現(2026-08-19、real_estate.yamlの本番相当テスト中)。

    統括AIが複数部署(依存関係のない全部署)を一度に割り当て、QAがPASSを返した後、
    統括AIがSUMMARIZEではなくASSIGN(対象部署は空)を誤って返すことがある。
    このとき候補部署も既に0件(全部署が完了済み)なので、以前はERRORになっていた。
    成果物は既にあるのでERRORで顧客に見せず、承認要求として扱う
    (「SUMMARIZEなのにQA未PASS」を強制REVISEにする既存の防御と対になる)。
    """
    llm = FakeLLM()
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="コピーを作って", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(
            verdict="ASSIGN",
            target_depts=["planning_dept", "copy_dept", "client_comm_dept"],
            instruction="x",
            reason="x",
        )
    )
    llm.queue_text("企画骨子です")
    llm.queue_text("問題のないコピーです")
    llm.queue_text("クライアント向け文面です")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    # QAはPASSなのに、統括AIが誤ってASSIGN(対象部署なし)を返す。候補も既に0件。
    llm.queue_structured(SupervisorDecision(verdict="ASSIGN", target_depts=[], reason="(誤った判断)"))
    llm.queue_structured(ApprovalSummary(headline="コピー完了", qa_result="合格"))

    graph = build_graph(config, llm=llm)
    state = new_state("tenant-a", "s6b", "user-1", "コピーを作って")
    result = await graph.ainvoke(state, config=thread_config("tenant-a:s6b"))

    assert result["status"] == "AWAITING_APPROVAL"
    assert result["approval_summary"]["headline"] == "コピー完了"


async def test_retry_cap_proceeds_to_approval_with_warnings_instead_of_halted(config):
    config.limits.max_retries_per_dept = 1

    llm = FakeLLM()
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="コピーを作って", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["copy_dept"], instruction="x", reason="x")
    )
    llm.queue_text("1回目のコピー")
    llm.queue_structured(
        QaResult(
            quality_findings=[
                {"criterion": "SPECIFICITY", "verdict": "FAIL", "evidence": "空文です", "reason": "a"},
                {"criterion": "ACTIONABILITY", "verdict": "FAIL", "evidence": "空文2です", "reason": "b"},
            ]
        )
    )
    llm.queue_structured(
        SupervisorDecision(verdict="REVISE", target_depts=["copy_dept"], instruction="直して", reason="品質不足")
    )
    llm.queue_text("2回目のコピー")
    llm.queue_structured(
        QaResult(
            quality_findings=[
                {"criterion": "SPECIFICITY", "verdict": "FAIL", "evidence": "まだ空文です", "reason": "a"},
                {"criterion": "ACTIONABILITY", "verdict": "FAIL", "evidence": "まだ空文2です", "reason": "b"},
            ]
        )
    )
    # 2回目もFAILし、max_retries_per_dept(=1)に達しているので、次のREVISE判断時に
    # Python側で「差し戻し上限到達」として強制的に承認へ進める
    llm.queue_structured(
        SupervisorDecision(verdict="REVISE", target_depts=["copy_dept"], instruction="直して", reason="品質不足")
    )
    llm.queue_structured(ApprovalSummary(headline="警告付きで完了", qa_result="警告付き"))

    graph = build_graph(config, llm=llm)
    state = new_state("tenant-a", "s7", "user-1", "コピーを作って")
    result = await graph.ainvoke(state, config=thread_config("tenant-a:s7"))

    assert result["status"] == "AWAITING_APPROVAL"
    assert result["status"] != "HALTED"
    assert len(result["qa_warnings"]) > 0


async def test_retry_cap_is_checked_per_department_not_all_or_nothing(config):
    """回帰テスト: 複数部署を同時にREVISE指名したとき、片方だけが上限に達している場合
    (all()で判定すると検出されず、上限に達した部署がさらに差し戻されて超過してしまうバグがあった)。"""
    config.limits.max_retries_per_dept = 1

    llm = FakeLLM()
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="提案とコピーを作って", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(
            verdict="ASSIGN", target_depts=["planning_dept", "copy_dept"], instruction="x", reason="x"
        )
    )
    llm.queue_text("## 提案骨子")
    llm.queue_text("## コピー案")
    llm.queue_structured(
        QaResult(
            quality_findings=[
                {"criterion": "SPECIFICITY", "verdict": "FAIL", "evidence": "空文です", "reason": "a"},
                {"criterion": "ACTIONABILITY", "verdict": "FAIL", "evidence": "空文2です", "reason": "b"},
            ]
        )
    )
    # copy_dept を1回REVISE(上限1に到達)
    llm.queue_structured(
        SupervisorDecision(verdict="REVISE", target_depts=["copy_dept"], instruction="x", reason="x")
    )
    llm.queue_text("## コピー案(修正1回目)")
    llm.queue_structured(
        QaResult(
            quality_findings=[
                {"criterion": "SPECIFICITY", "verdict": "FAIL", "evidence": "空文です", "reason": "a"},
                {"criterion": "ACTIONABILITY", "verdict": "FAIL", "evidence": "空文2です", "reason": "b"},
            ]
        )
    )
    # 3回目の判断: copy_dept(上限到達済み)とplanning_dept(未到達)を同時にREVISE指名
    llm.queue_structured(
        SupervisorDecision(
            verdict="REVISE", target_depts=["copy_dept", "planning_dept"], instruction="x", reason="x"
        )
    )
    llm.queue_text("## 提案骨子(修正1回目)")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="完了", qa_result="合格"))

    graph = build_graph(config, llm=llm)
    state = new_state("tenant-a", "s-retry-per-dept", "user-1", "提案とコピーを作って")
    result = await graph.ainvoke(state, config=thread_config("tenant-a:s-retry-per-dept"))

    # copy_deptは上限(1)を超えて差し戻されていないこと。planning_deptだけが再実行されたこと
    assert result["retry_counts"]["copy_dept"] == 1
    assert result["retry_counts"]["planning_dept"] == 1
    assert result["board"]["copy_dept"] == "## コピー案(修正1回目)"  # 3回目は再実行されていない
    assert result["board"]["planning_dept"] == "## 提案骨子(修正1回目)"
    assert result["status"] == "AWAITING_APPROVAL"


async def test_independent_departments_run_in_parallel(config):
    import time

    llm = FakeLLM(delay=0.2)
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="提案とコピーを作って", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(
            verdict="ASSIGN",
            target_depts=["planning_dept", "copy_dept"],
            instruction="提案骨子とコピーを別々に",
            reason="独立して作れる",
        )
    )
    llm.queue_text("## 提案骨子")
    llm.queue_text("## コピー案")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="完了", qa_result="合格"))

    graph = build_graph(config, llm=llm)
    state = new_state("tenant-a", "s8", "user-1", "提案とコピーを作って")

    started = time.monotonic()
    result = await graph.ainvoke(state, config=thread_config("tenant-a:s8"))
    elapsed = time.monotonic() - started

    assert set(result["board"].keys()) == {"planning_dept", "copy_dept"}
    # 2部署が並列実行されていれば約0.2秒、順次実行なら約0.4秒以上かかる
    assert elapsed < 0.3


async def test_join_waits_for_all_assigned_departments_before_qa(config):
    llm = FakeLLM()
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="提案とコピーを作って", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(
            verdict="ASSIGN",
            target_depts=["planning_dept", "copy_dept"],
            instruction="x",
            reason="x",
        )
    )
    llm.queue_text("## 提案骨子の内容")
    llm.queue_text("## コピー案の内容")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="完了", qa_result="合格"))

    graph = build_graph(config, llm=llm)
    state = new_state("tenant-a", "s9", "user-1", "提案とコピーを作って")
    await graph.ainvoke(state, config=thread_config("tenant-a:s9"))

    qa_call = next(c for c in llm.structured_calls if c["output_model"] is QaResult)
    assert "提案骨子の内容" in qa_call["user_message"]
    assert "コピー案の内容" in qa_call["user_message"]


async def test_partial_department_failure_preserves_other_results(config):
    llm = FakeLLM()
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="提案とコピーを作って", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(
            verdict="ASSIGN",
            target_depts=["planning_dept", "copy_dept"],
            instruction="x",
            reason="x",
        )
    )
    llm.queue_text("## 提案骨子は成功した")
    llm.queue_text(RuntimeError("Anthropic APIが一時的にダウン"))
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(
        SupervisorDecision(
            verdict="REVISE", target_depts=["copy_dept"], instruction="再試行して", reason="前回失敗"
        )
    )
    llm.queue_text("## コピー案(再試行成功)")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="完了", qa_result="合格"))

    graph = build_graph(config, llm=llm)
    state = new_state("tenant-a", "s10", "user-1", "提案とコピーを作って")
    result = await graph.ainvoke(state, config=thread_config("tenant-a:s10"))

    # 1部署が失敗しても、成功した部署の成果物は消えていないこと
    assert result["board"]["planning_dept"] == "## 提案骨子は成功した"
    assert result["board"]["copy_dept"] == "## コピー案(再試行成功)"
    assert any("生成に失敗" in s for s in result["step_history"])


async def test_max_parallel_depts_caps_concurrent_execution(config):
    config.limits.max_parallel_depts = 1

    llm = FakeLLM()
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="全部作って", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(
            verdict="ASSIGN",
            target_depts=["planning_dept", "copy_dept", "client_comm_dept"],
            instruction="x",
            reason="x",
        )
    )
    llm.queue_text("## 提案骨子")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="完了", qa_result="合格"))

    graph = build_graph(config, llm=llm)
    state = new_state("tenant-a", "s11", "user-1", "全部作って")
    result = await graph.ainvoke(state, config=thread_config("tenant-a:s11"))

    # 上限1のため、3部署指名されても1部署だけが実行され、残りは見送られる
    assert len(llm.text_calls) == 1
    assert set(result["board"].keys()) == {"planning_dept"}


async def _run_to_approval(config, llm, tenant_id, session_id, goal_text):
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal=goal_text, reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(
            verdict="ASSIGN", target_depts=["copy_dept"], instruction="x", reason="x"
        )
    )
    llm.queue_text("## コピー案")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="コピー完了", qa_result="合格"))

    graph = build_graph(config, llm=llm)
    cfg = thread_config(f"tenant-a:{session_id}")
    state = new_state("tenant-a", session_id, "user-1", goal_text)
    result = await graph.ainvoke(state, config=cfg)
    return graph, cfg, result


async def test_approval_approve_completes_session(config):
    llm = FakeLLM()
    graph, cfg, result = await _run_to_approval(config, llm, "tenant-a", "s12", "コピーを作って")

    assert result["status"] == "AWAITING_APPROVAL"

    result2 = await graph.ainvoke(Command(resume={"decision": "approve"}), config=cfg)
    assert result2["status"] == "APPROVED"


async def test_approval_reject_routes_back_for_revision(config):
    llm = FakeLLM()
    graph, cfg, result = await _run_to_approval(config, llm, "tenant-a", "s13", "コピーを作って")
    assert result["status"] == "AWAITING_APPROVAL"

    # 却下 -> ceo_officeに戻り、却下理由を踏まえてcopy_deptをREVISEし、再度承認待ちになる
    llm.queue_structured(
        SupervisorDecision(
            verdict="REVISE", target_depts=["copy_dept"], instruction="トーンを直す", reason="却下理由に対応"
        )
    )
    llm.queue_text("## コピー案(修正後)")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="修正後の完了", qa_result="合格"))

    result2 = await graph.ainvoke(
        Command(resume={"decision": "reject", "comment": "トーンが硬すぎる"}), config=cfg
    )

    assert result2["status"] == "AWAITING_APPROVAL"
    assert result2["rejection_count"] == 1
    assert result2["rejection_history"] == ["トーンが硬すぎる"]
    assert result2["board"]["copy_dept"] == "## コピー案(修正後)"
    # 却下理由がプロンプトに渡っていること(6-3-1)
    revise_call = next(
        c
        for c in llm.structured_calls
        if c["output_model"] is SupervisorDecision and "トーンが硬すぎる" in c["user_message"]
    )
    assert revise_call is not None


async def test_approval_rejection_limit_halts_instead_of_looping_forever(config):
    config.limits.max_rejections = 2
    llm = FakeLLM()
    graph, cfg, result = await _run_to_approval(config, llm, "tenant-a", "s14", "コピーを作って")
    assert result["status"] == "AWAITING_APPROVAL"

    for i in range(2):
        llm.queue_structured(
            SupervisorDecision(
                verdict="REVISE", target_depts=["copy_dept"], instruction="直す", reason="却下対応"
            )
        )
        llm.queue_text(f"## コピー案(修正{i + 1}回目)")
        llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
        llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
        llm.queue_structured(ApprovalSummary(headline=f"修正{i + 1}回目", qa_result="合格"))

        result = await graph.ainvoke(
            Command(resume={"decision": "reject", "comment": f"却下理由{i + 1}"}), config=cfg
        )

    assert result["status"] == "HALTED"
    assert result["rejection_count"] == 2
    assert result["rejection_history"] == ["却下理由1", "却下理由2"]
    assert "2回の修正" in result["error_message"]
