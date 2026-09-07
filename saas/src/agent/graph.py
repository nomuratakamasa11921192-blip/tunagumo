import functools

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.agent.config_models import AppConfig
from src.agent.llm import StructuredLLM
from src.agent.nodes import (
    approval_node,
    ceo_office_node,
    clarify_node,
    join_node,
    make_dept_node,
    qa_auditor_node,
    route_from_approval,
    route_from_ceo_office,
)
from src.agent.state import OrgState
from src.rag.embeddings import EmbeddingProvider


def build_graph(
    config: AppConfig,
    llm: StructuredLLM | None = None,
    checkpointer=None,
    embedding_provider: EmbeddingProvider | None = None,
):
    """OrgStateのグラフを構築してコンパイルする。

    checkpointer未指定時はMemorySaver(プロセス内のみ)を使う。
    Phase 5でPostgresSaverに切り替える。

    embedding_provider未指定(None)の顧客は、RAG検索(Phase 7)を単にスキップする。
    """
    llm = llm or StructuredLLM()
    checkpointer = checkpointer if checkpointer is not None else MemorySaver()

    graph = StateGraph(OrgState)

    dept_ids = [d for d in config.departments if d not in ("ceo_office", "qa_auditor")]

    graph.add_node("ceo_office", functools.partial(ceo_office_node, app_config=config, llm=llm))
    graph.add_node("clarify", clarify_node)
    graph.add_node("join", join_node)
    graph.add_node("qa_auditor", functools.partial(qa_auditor_node, app_config=config, llm=llm))
    graph.add_node("approval", functools.partial(approval_node, app_config=config))

    for dept_id in dept_ids:
        graph.add_node(
            dept_id,
            make_dept_node(dept_id, app_config=config, llm=llm, embedding_provider=embedding_provider),
        )
        graph.add_edge(dept_id, "join")

    graph.add_edge("join", "qa_auditor")
    graph.add_edge("qa_auditor", "ceo_office")
    graph.add_edge("clarify", "ceo_office")
    graph.add_edge(START, "ceo_office")

    # ceo_officeの判断(status / next_depts)に応じて、clarify・approval・各部署・ENDへ動的に分岐する
    graph.add_conditional_edges(
        "ceo_office",
        route_from_ceo_office,
        [*dept_ids, "clarify", "approval", END],
    )
    # approvalは却下時にceo_officeへ差し戻し、承認/却下上限到達時はENDへ
    graph.add_conditional_edges("approval", route_from_approval, ["ceo_office", END])

    return graph.compile(checkpointer=checkpointer)
