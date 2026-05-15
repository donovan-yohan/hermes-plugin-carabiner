import importlib.util
import json
import sys
import types
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "__init__.py"
spec = importlib.util.spec_from_file_location("carabiner_plugin", PLUGIN_PATH)
assert spec is not None
carabiner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = carabiner
spec.loader.exec_module(carabiner)

CarabinerHonchoClient = carabiner.CarabinerHonchoClient
build_feedback_message = carabiner.build_feedback_message
build_handoff_query = carabiner.build_handoff_query
normalize_peer_id = carabiner.normalize_peer_id
tool_handoff_context = carabiner.tool_handoff_context
tool_record_feedback = carabiner.tool_record_feedback


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, body=None, query=None):
        self.calls.append((method, path, body, query))
        if path.endswith("/messages"):
            return [{"id": "msg_1"}]
        if path.endswith("/representation"):
            return {"representation": "kame expects screenshot evidence"}
        if path.endswith("/search"):
            return [{"content": "fallback evidence: include console output", "peer_id": "agent-kame-qa", "session_id": "work-1", "metadata": {"kind": "peer_feedback"}}]
        return {"id": body.get("id") if isinstance(body, dict) else "ok"}


class EmptyRepresentationTransport(FakeTransport):
    def __call__(self, method, path, body=None, query=None):
        self.calls.append((method, path, body, query))
        if path.endswith("/representation"):
            return {"representation": ""}
        if path.endswith("/search"):
            return [{"content": "raw fallback says screenshots are required", "peer_id": "agent-kame-qa", "session_id": "work-1", "metadata": {"kind": "peer_feedback"}}]
        return {"id": body.get("id") if isinstance(body, dict) else "ok"}


def test_normalize_peer_id_makes_honcho_safe_stable_ids():
    assert normalize_peer_id("agent:ika-frontend") == "agent-ika-frontend"
    assert normalize_peer_id("Kame QA") == "kame-qa"
    assert normalize_peer_id("agent:kame_qa") == "agent-kame-qa"


def test_build_feedback_message_keeps_critique_operational_not_identity_label():
    message = build_feedback_message(
        observer="agent:kame-qa",
        subject="agent:ika-frontend",
        relationship="qa_handoff",
        repo="hermes-agent",
        raw_feedback="This handoff is unverifiable without screenshots.",
        operational_critique="For frontend QA handoffs, include screenshot and console evidence.",
        confidence=0.82,
        evidence={"work_id": "work:hermes-agent:123"},
    )

    assert message["peer_id"] == "agent-kame-qa"
    assert message["metadata"]["kind"] == "peer_feedback"
    assert message["metadata"]["subject"] == "agent-ika-frontend"
    assert "unverifiable" in message["content"]
    assert "include screenshot" in message["content"]
    assert "ika is sloppy" not in message["content"].lower()


def test_record_feedback_tool_writes_workspace_session_peers_and_message():
    transport = FakeTransport()
    client = CarabinerHonchoClient(
        base_url="https://memory.example.test",
        api_key="secret",
        workspace="hermes-ocean-dev",
        transport=transport,
    )

    result = json.loads(tool_record_feedback({
        "observer": "agent:kame-qa",
        "subject": "agent:ika-frontend",
        "relationship": "qa_handoff",
        "repo": "hermes-agent",
        "task_type": "frontend",
        "raw_feedback": "No screenshot proof.",
        "operational_critique": "Include screenshots before QA handoff.",
        "evidence": {"work_id": "work:hermes-agent:123"},
    }, client=client))

    assert result["success"] is True
    assert result["workspace"] == "hermes-ocean-dev"
    assert result["session_id"] == "work-hermes-agent-123"
    paths = [call[1] for call in transport.calls]
    assert "/v3/workspaces" in paths
    assert "/v3/workspaces/hermes-ocean-dev/peers" in paths
    assert "/v3/workspaces/hermes-ocean-dev/sessions/work-hermes-agent-123/messages" in paths


def test_build_handoff_query_is_scoped_to_repo_task_and_artifact():
    query = build_handoff_query(
        from_agent="agent:ika-frontend",
        to_agent="agent:kame-qa",
        repo="hermes-agent",
        task_type="frontend",
        artifact_type="ui_change",
    )

    assert "ika-frontend" in query
    assert "kame-qa" in query
    assert "hermes-agent" in query
    assert "frontend" in query
    assert "ui_change" in query


def test_handoff_context_falls_back_to_raw_search_when_representation_empty():
    transport = EmptyRepresentationTransport()
    client = CarabinerHonchoClient(
        base_url="https://memory.example.test",
        api_key="secret",
        workspace="hermes-ocean-dev",
        transport=transport,
    )

    result = json.loads(tool_handoff_context({
        "from_agent": "agent:ika-frontend",
        "to_agent": "agent:kame-qa",
        "repo": "hermes-agent",
        "task_type": "frontend",
        "artifact_type": "ui_change",
    }, client=client))

    assert result["success"] is True
    assert result["source"] == "raw_search_fallback"
    assert "screenshots are required" in result["context"]
    assert any(call[1].endswith("/search") for call in transport.calls)


def test_register_uses_carabiner_tool_names_and_toolset():
    class FakeContext:
        def __init__(self):
            self.tools = []
            self.hooks = []

        def register_tool(self, **kwargs):
            self.tools.append(kwargs)

        def register_hook(self, name, handler):
            self.hooks.append((name, handler))

    ctx = FakeContext()
    carabiner.register(ctx)

    assert [tool["name"] for tool in ctx.tools] == [
        "carabiner_record_feedback",
        "carabiner_record_episode",
        "carabiner_handoff_context",
        "carabiner_context",
        "carabiner_audit",
    ]
    assert {tool["toolset"] for tool in ctx.tools} == {"carabiner"}
    assert [hook[0] for hook in ctx.hooks] == ["pre_tool_call", "post_tool_call", "subagent_stop"]


def test_manifest_tools_match_registered_tools():
    class FakeContext:
        def __init__(self):
            self.tools = []

        def register_tool(self, **kwargs):
            self.tools.append(kwargs["name"])

        def register_hook(self, name, handler):
            pass

    ctx = FakeContext()
    carabiner.register(ctx)
    manifest = (PLUGIN_PATH.parent / "plugin.yaml").read_text()
    manifest_tools = [line.strip()[2:] for line in manifest.splitlines() if line.startswith("  - carabiner_")]

    assert manifest_tools == ctx.tools


def test_load_config_prefers_carabiner_env_with_legacy_fallback(monkeypatch):
    monkeypatch.delenv("CARABINER_API_KEY", raising=False)
    monkeypatch.setenv("HONCHO_RELATIONSHIP_API_KEY", "legacy-key")
    cfg = carabiner.load_carabiner_config()
    assert cfg.api_key == "legacy-key"

    monkeypatch.setenv("CARABINER_API_KEY", "new-key")
    cfg = carabiner.load_carabiner_config()
    assert cfg.api_key == "new-key"


def test_load_config_merges_partial_carabiner_block_with_legacy_block(monkeypatch):
    for key in [
        "CARABINER_API_KEY",
        "HONCHO_RELATIONSHIP_API_KEY",
        "CARABINER_BASE_URL",
        "HONCHO_RELATIONSHIP_BASE_URL",
        "CARABINER_WORKSPACE",
        "HONCHO_RELATIONSHIP_WORKSPACE",
    ]:
        monkeypatch.delenv(key, raising=False)

    fake_config = types.ModuleType("hermes_cli.config")
    setattr(fake_config, "load_config", lambda: {
        "agent_relationship_memory": {
            "base_url": "https://legacy.example.test",
            "api_key": "legacy-config-key",
            "workspace": "legacy-workspace",
            "timeout": 7,
        },
        "carabiner": {
            "enabled": False,
            "workspace": "new-workspace",
        },
    })
    fake_pkg = types.ModuleType("hermes_cli")
    monkeypatch.setitem(sys.modules, "hermes_cli", fake_pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", fake_config)

    cfg = carabiner.load_carabiner_config()

    assert cfg.base_url == "https://legacy.example.test"
    assert cfg.api_key == "legacy-config-key"
    assert cfg.workspace == "new-workspace"
    assert cfg.timeout == 7
    assert cfg.enabled is False


def test_delegate_task_hooks_record_start_and_outcome_when_capture_enabled(monkeypatch):
    captured = []
    monkeypatch.setenv("CARABINER_CAPTURE_SUBAGENTS", "true")
    monkeypatch.setenv("CARABINER_AGENT_ID", "agent:ika-frontend")
    monkeypatch.setenv("CARABINER_DEFAULT_REPO", "relay-ide")
    monkeypatch.setenv("CARABINER_DEFAULT_TASK_TYPE", "frontend")
    monkeypatch.setattr(carabiner, "tool_record_episode", lambda args: captured.append(args) or '{"success": true}')

    args = {"goal": "implement sidebar tabs", "role": "leaf", "toolsets": ["terminal", "file"]}
    carabiner._on_pre_tool_call(
        tool_name="delegate_task",
        args=args,
        task_id="task-1",
        session_id="session-1",
        tool_call_id="call-1",
    )
    carabiner._on_post_tool_call(
        tool_name="delegate_task",
        args=args,
        result='{"results":[{"task_index":0,"status":"completed","summary":"implemented sidebar tabs","duration_seconds":12,"api_calls":3}]}',
        task_id="task-1",
        session_id="session-1",
        tool_call_id="call-1",
        duration_ms=12345,
    )

    assert len(captured) == 2
    assert captured[0]["relationship"] == "delegation_start"
    assert captured[0]["actor"] == "agent-ika-frontend"
    assert captured[0]["participants"] == ["agent-leaf"]
    assert captured[0]["evidence"]["source"] == "pre_tool_call:delegate_task"
    assert "implement sidebar tabs" in captured[0]["claim"]
    assert captured[1]["relationship"] == "delegation_outcome"
    assert captured[1]["outcome"] == "completed"
    assert captured[1]["repo"] == "relay-ide"
    assert captured[1]["task_type"] == "frontend"
    assert "implemented sidebar tabs" in captured[1]["claim"]
    assert captured[1]["evidence"]["source"] == "post_tool_call:delegate_task"


def test_subagent_stop_hook_is_legacy_opt_in(monkeypatch):
    captured = []
    monkeypatch.setenv("CARABINER_CAPTURE_SUBAGENTS", "true")
    monkeypatch.setenv("CARABINER_CAPTURE_SUBAGENT_STOP", "true")
    monkeypatch.setenv("CARABINER_AGENT_ID", "agent:ika-frontend")
    monkeypatch.setattr(carabiner, "tool_record_episode", lambda args: captured.append(args) or '{"success": true}')

    carabiner._on_subagent_stop(
        parent_session_id="parent-1",
        child_role="leaf",
        child_summary="implemented sidebar tabs",
        child_status="completed",
        duration_ms=1234,
    )

    assert len(captured) == 1
    assert captured[0]["actor"] == "agent-ika-frontend"
    assert captured[0]["participants"] == ["agent-leaf"]
    assert captured[0]["evidence"]["source"] == "subagent_stop_hook"
