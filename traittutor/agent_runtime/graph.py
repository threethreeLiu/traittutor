"""Small LangGraph ReAct-style product router with explicit policy preflight."""

from __future__ import annotations

from typing import Any, TypedDict
import uuid

from langgraph.graph import END, START, StateGraph

from traittutor.gateway import GatewayRequest, get_gateway

from .policy import preflight
from .schemas import AgentMode, AgentRunRequest, AgentRunResult, Intent


class AgentState(TypedDict, total=False):
    request: AgentRunRequest
    intent: Intent
    agent: str
    policy: list[Any]
    response: Any


def _classify(request: AgentRunRequest) -> tuple[Intent, str]:
    message = request.message.lower()
    if request.mode is AgentMode.LEARN:
        return Intent.LEARNING, "learning_coach"
    if any(word in message for word in ("research", "research", "sources", "检索", "研究")):
        return Intent.RESEARCH, "research_agent"
    if any(word in message for word in ("write", "rewrite", "draft", "写", "改写")):
        return Intent.WRITING, "task_agent"
    if any(word in message for word in ("plan", "schedule", "规划", "计划")):
        return Intent.PLANNING, "task_agent"
    if any(word in message for word in ("file", "pdf", "spreadsheet", "代码", "执行")):
        return Intent.FILE_TASK, "execution_agent"
    return Intent.GENERAL, "task_agent"


async def _route(state: AgentState) -> AgentState:
    intent, agent = _classify(state["request"])
    return {"intent": intent, "agent": agent, "policy": preflight(state["request"].message)}


async def _respond(state: AgentState) -> AgentState:
    request = state["request"]
    policy_text = "; ".join(f"{item.action}:{item.decision}" for item in state["policy"])
    response = await get_gateway().complete(
        GatewayRequest(
            prompt=request.message,
            system_prompt=(
                "You are TraitTutor, a helpful consumer learning assistant. "
                f"You are acting as {state['agent']} for intent {state['intent'].value}. "
                "Do not claim to have used tools unless their result is supplied. "
                f"Policy preflight: {policy_text}."
            ),
            purpose=f"agent:{state['agent']}",
            user_id=request.user_id,
            reasoning_effort="high" if state["intent"] is Intent.LEARNING else None,
        )
    )
    return {"response": response}


def _build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("route", _route)
    graph.add_node("respond", _respond)
    graph.add_edge(START, "route")
    graph.add_edge("route", "respond")
    graph.add_edge("respond", END)
    return graph.compile()


_graph = _build_graph()


async def run_agent(request: AgentRunRequest) -> AgentRunResult:
    state = await _graph.ainvoke({"request": request})
    response = state["response"]
    return AgentRunResult(
        run_id=str(uuid.uuid4()),
        intent=state["intent"],
        agent=state["agent"],
        content=response.content,
        gateway_request_id=response.request_id,
        policy=state["policy"],
    )
