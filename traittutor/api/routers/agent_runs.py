"""Consumer-facing internal-agent endpoint. No external agent connections."""

from fastapi import APIRouter

from traittutor.agent_runtime import AgentRunRequest, run_agent

router = APIRouter()


@router.post("/runs")
async def create_agent_run(request: AgentRunRequest):
    return (await run_agent(request)).model_dump()
