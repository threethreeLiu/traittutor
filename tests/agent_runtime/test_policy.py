from traittutor.agent_runtime.policy import preflight


def test_sensitive_host_access_is_blocked():
    decisions = preflight("Read ~/.ssh and my password")
    assert any(item.decision == "blocked" for item in decisions)


def test_file_task_is_sandboxed():
    decisions = preflight("Run code over this file")
    assert any(item.action == "sandbox" and item.decision == "allowed" for item in decisions)
