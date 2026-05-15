"""Honcho-backed sidecar memory for Hermes specialist-carabiners.

This plugin intentionally does not register as a Hermes memory provider. It is a
normal tool plugin so Hindsight can remain the active semantic memory backend
while Honcho stores scoped peer-to-peer collaboration feedback for handoffs, QA, reviews, and specialist-agent coordination.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://memory.fish-rattlesnake.ts.net"
DEFAULT_WORKSPACE = "hermes-ocean-dev"
TOOLSET = "carabiner"

Transport = Callable[[str, str, dict[str, Any] | None, dict[str, Any] | None], Any]


# ---------------------------------------------------------------------------
# Formatting / normalization helpers
# ---------------------------------------------------------------------------

def normalize_peer_id(value: str | None) -> str:
    """Convert human-ish agent names into stable Honcho resource IDs."""
    raw = (value or "").strip().lower()
    raw = raw.replace(":", "-").replace("_", "-")
    raw = re.sub(r"[^a-z0-9.-]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-.")
    return raw or "unknown-peer"


def normalize_resource_id(value: str | None, *, fallback: str = "work-unspecified") -> str:
    raw = (value or "").strip().lower()
    raw = raw.replace(":", "-").replace("_", "-").replace("/", "-")
    raw = re.sub(r"[^a-z0-9.-]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-.")
    return raw[:180] or fallback


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)


def _session_id_from_args(args: dict[str, Any]) -> str:
    explicit = args.get("session_id") or args.get("work_id")
    if not explicit and isinstance(args.get("evidence"), dict):
        explicit = args["evidence"].get("work_id") or args["evidence"].get("id")
    if explicit:
        return normalize_resource_id(str(explicit))
    repo = normalize_resource_id(args.get("repo") or args.get("project") or "general")
    relationship = normalize_resource_id(args.get("relationship") or args.get("task_type") or "collaboration")
    return f"work-{repo}-{relationship}-{int(time.time())}"


def build_feedback_message(
    *,
    observer: str,
    subject: str,
    relationship: str,
    repo: str | None = None,
    task_type: str | None = None,
    artifact_type: str | None = None,
    raw_feedback: str,
    operational_critique: str,
    recommended_change: str | None = None,
    evidence: dict[str, Any] | None = None,
    confidence: float | int | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """Build a Honcho message for critique of observable work behavior."""
    observer_id = normalize_peer_id(observer)
    subject_id = normalize_peer_id(subject)
    confidence_value = 0.65 if confidence is None else float(confidence)
    confidence_value = max(0.0, min(confidence_value, 1.0))
    evidence = evidence or {}
    content = (
        "<peer_feedback>\n"
        f"  <observer>{observer_id}</observer>\n"
        f"  <subject>{subject_id}</subject>\n"
        f"  <relationship>{relationship}</relationship>\n"
        "  <scope\n"
        f"    repo={json.dumps(repo or '')}\n"
        f"    task_type={json.dumps(task_type or '')}\n"
        f"    artifact_type={json.dumps(artifact_type or '')}\n"
        "  />\n"
        f"  <raw_feedback>{raw_feedback.strip()}</raw_feedback>\n"
        f"  <operational_critique>{operational_critique.strip()}</operational_critique>\n"
        f"  <recommended_change>{(recommended_change or '').strip()}</recommended_change>\n"
        f"  <evidence>{_compact_json(evidence)}</evidence>\n"
        f"  <confidence>{confidence_value:.2f}</confidence>\n"
        "</peer_feedback>"
    )
    return {
        "peer_id": observer_id,
        "content": content,
        "metadata": {
            "kind": "peer_feedback",
            "observer": observer_id,
            "subject": subject_id,
            "relationship": relationship,
            "repo": repo,
            "task_type": task_type,
            "artifact_type": artifact_type,
            "severity": severity,
            "confidence": confidence_value,
            "evidence": evidence,
        },
    }


def build_episode_message(args: dict[str, Any]) -> dict[str, Any]:
    actor_id = normalize_peer_id(args.get("actor") or args.get("agent") or args.get("observer"))
    participants = [normalize_peer_id(p) for p in args.get("participants", []) if p]
    content = (
        "<collaboration_episode>\n"
        f"  <actor>{actor_id}</actor>\n"
        f"  <participants>{_compact_json(participants)}</participants>\n"
        f"  <relationship>{args.get('relationship') or ''}</relationship>\n"
        f"  <scope repo={json.dumps(args.get('repo') or '')} task_type={json.dumps(args.get('task_type') or '')} artifact_type={json.dumps(args.get('artifact_type') or '')}/>\n"
        f"  <claim>{(args.get('claim') or '').strip()}</claim>\n"
        f"  <outcome>{(args.get('outcome') or '').strip()}</outcome>\n"
        f"  <evidence>{_compact_json(args.get('evidence') or {})}</evidence>\n"
        f"  <confidence>{float(args.get('confidence', 0.5)):.2f}</confidence>\n"
        "</collaboration_episode>"
    )
    return {
        "peer_id": actor_id,
        "content": content,
        "metadata": {
            "kind": "collaboration_episode",
            "actor": actor_id,
            "participants": participants,
            "relationship": args.get("relationship"),
            "repo": args.get("repo"),
            "task_type": args.get("task_type"),
            "artifact_type": args.get("artifact_type"),
            "evidence": args.get("evidence") or {},
            "confidence": float(args.get("confidence", 0.5)),
        },
    }


def build_handoff_query(
    *,
    from_agent: str,
    to_agent: str,
    repo: str | None = None,
    task_type: str | None = None,
    artifact_type: str | None = None,
) -> str:
    return (
        "Retrieve operational collaboration expectations for handoff prep: "
        f"from {normalize_peer_id(from_agent)} to {normalize_peer_id(to_agent)}; "
        f"repo={repo or ''}; task_type={task_type or ''}; artifact_type={artifact_type or ''}. "
        "Focus on evidence-backed critique, QA/review/design expectations, and concrete checklist items. "
        "Ignore personality labels."
    )


def _format_search_hits(results: Any, *, limit: int = 5) -> str:
    """Compact raw Honcho search hits into prompt-safe context fallback text."""
    if isinstance(results, dict):
        items = results.get("results") or results.get("items") or []
    elif isinstance(results, list):
        items = results
    else:
        items = []
    lines: list[str] = []
    for idx, item in enumerate(items[: max(1, limit)], start=1):
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") or {}
        content = (item.get("content") or "").strip()
        if len(content) > 900:
            content = content[:897] + "..."
        peer = item.get("peer_id") or metadata.get("observer") or metadata.get("actor") or "unknown-peer"
        session_id = item.get("session_id") or metadata.get("session_id") or "unknown-session"
        kind = metadata.get("kind") or "relationship_memory"
        lines.append(f"{idx}. [{kind}] peer={peer} session={session_id}\n{content}")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Honcho HTTP client
# ---------------------------------------------------------------------------

@dataclass
class CarabinerConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    workspace: str = DEFAULT_WORKSPACE
    timeout: float = 15.0
    enabled: bool = True
    agent_id: str = ""
    default_repo: str = ""
    default_task_type: str = "delegation"


def _env(primary: str, legacy: str | None = None) -> str | None:
    value = os.getenv(primary)
    if value is not None:
        return value
    return os.getenv(legacy) if legacy else None


def load_carabiner_config() -> CarabinerConfig:
    cfg: dict[str, Any] = {}
    try:
        from hermes_cli.config import load_config
        root = load_config() or {}
        # Prefer the standalone plugin config block, but accept the prototype
        # block so early deployments can migrate without going dark. Merge
        # per-key so a partial `carabiner:` block can still inherit old values.
        legacy_cfg = root.get("agent_relationship_memory") or {}
        new_cfg = root.get("carabiner") or {}
        cfg = {
            **(legacy_cfg if isinstance(legacy_cfg, dict) else {}),
            **(new_cfg if isinstance(new_cfg, dict) else {}),
        }
    except Exception:
        cfg = {}

    return CarabinerConfig(
        base_url=_env("CARABINER_BASE_URL", "HONCHO_RELATIONSHIP_BASE_URL") or str(cfg.get("base_url") or DEFAULT_BASE_URL),
        api_key=_env("CARABINER_API_KEY", "HONCHO_RELATIONSHIP_API_KEY") or str(cfg.get("api_key") or ""),
        workspace=_env("CARABINER_WORKSPACE", "HONCHO_RELATIONSHIP_WORKSPACE") or str(cfg.get("workspace") or DEFAULT_WORKSPACE),
        timeout=float(_env("CARABINER_TIMEOUT", "HONCHO_RELATIONSHIP_TIMEOUT") or cfg.get("timeout") or 15.0),
        enabled=str(_env("CARABINER_ENABLED", "HONCHO_RELATIONSHIP_ENABLED") or cfg.get("enabled", "true")).lower() not in {"0", "false", "no", "off"},
        agent_id=_env("CARABINER_AGENT_ID") or str(cfg.get("agent_id") or os.getenv("HERMES_PROFILE") or ""),
        default_repo=_env("CARABINER_DEFAULT_REPO") or str(cfg.get("default_repo") or ""),
        default_task_type=_env("CARABINER_DEFAULT_TASK_TYPE") or str(cfg.get("default_task_type") or "delegation"),
    )


class CarabinerHonchoClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        workspace: str,
        timeout: float = 15.0,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.workspace = normalize_resource_id(workspace, fallback=DEFAULT_WORKSPACE)
        self.timeout = timeout
        self._transport = transport

    @classmethod
    def from_config(cls, config: CarabinerConfig | None = None) -> "CarabinerHonchoClient":
        config = config or load_carabiner_config()
        return cls(
            base_url=config.base_url,
            api_key=config.api_key,
            workspace=config.workspace,
            timeout=config.timeout,
        )

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        if self._transport is not None:
            return self._transport(method, path, body, query)
        if not self.api_key:
            raise RuntimeError("CARABINER_API_KEY is not configured")
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "ignore")
            raise RuntimeError(f"Honcho HTTP {e.code}: {raw[:500]}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Honcho request failed: {e}") from e

    def ensure_workspace(self) -> Any:
        return self.request("POST", "/v3/workspaces", {"id": self.workspace})

    def ensure_peer(self, peer_id: str) -> Any:
        return self.request("POST", f"/v3/workspaces/{self.workspace}/peers", {"id": normalize_peer_id(peer_id)})

    def ensure_session(self, session_id: str, peers: list[str]) -> Any:
        peer_cfg = {normalize_peer_id(p): {"observe_me": True, "observe_others": True} for p in peers if p}
        return self.request(
            "POST",
            f"/v3/workspaces/{self.workspace}/sessions",
            {"id": normalize_resource_id(session_id), "peers": peer_cfg},
        )

    def create_messages(self, session_id: str, messages: list[dict[str, Any]]) -> Any:
        return self.request(
            "POST",
            f"/v3/workspaces/{self.workspace}/sessions/{normalize_resource_id(session_id)}/messages",
            {"messages": messages},
        )

    def get_representation(
        self,
        *,
        observer: str,
        target: str,
        search_query: str | None = None,
        session_id: str | None = None,
        max_conclusions: int = 10,
    ) -> str:
        body = {
            "target": normalize_peer_id(target),
            "search_query": search_query,
            "session_id": normalize_resource_id(session_id) if session_id else None,
            "search_top_k": 8 if search_query else None,
            "max_conclusions": max(1, min(int(max_conclusions), 100)),
        }
        result = self.request(
            "POST",
            f"/v3/workspaces/{self.workspace}/peers/{normalize_peer_id(observer)}/representation",
            {k: v for k, v in body.items() if v is not None},
        )
        if isinstance(result, dict):
            return result.get("representation") or ""
        return ""

    def search_workspace(self, query: str, *, limit: int = 10) -> Any:
        return self.request(
            "POST",
            f"/v3/workspaces/{self.workspace}/search",
            {"query": query, "limit": max(1, min(int(limit), 50)), "filters": {}},
        )


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _json_result(**kwargs: Any) -> str:
    return json.dumps(kwargs, ensure_ascii=False)


def _json_error(message: str) -> str:
    return _json_result(success=False, error=message)


def _client_or_default(client: CarabinerHonchoClient | None = None) -> CarabinerHonchoClient:
    if client is not None:
        return client
    cfg = load_carabiner_config()
    if not cfg.enabled:
        raise RuntimeError("carabiner is disabled")
    return CarabinerHonchoClient.from_config(cfg)


def tool_record_feedback(args: dict[str, Any], *, client: CarabinerHonchoClient | None = None, **_: Any) -> str:
    try:
        observer = args.get("observer") or args.get("reviewer") or args.get("from_agent")
        subject = args.get("subject") or args.get("target") or args.get("to_agent")
        raw_feedback = (args.get("raw_feedback") or args.get("feedback") or "").strip()
        operational_critique = (args.get("operational_critique") or args.get("critique") or "").strip()
        relationship = (args.get("relationship") or "collaboration_feedback").strip()
        if not observer or not subject or not raw_feedback or not operational_critique:
            return _json_error("observer, subject, raw_feedback, and operational_critique are required")
        client = _client_or_default(client)
        observer_id = normalize_peer_id(observer)
        subject_id = normalize_peer_id(subject)
        session_id = _session_id_from_args(args)
        message = build_feedback_message(
            observer=observer_id,
            subject=subject_id,
            relationship=relationship,
            repo=args.get("repo"),
            task_type=args.get("task_type"),
            artifact_type=args.get("artifact_type"),
            raw_feedback=raw_feedback,
            operational_critique=operational_critique,
            recommended_change=args.get("recommended_change"),
            evidence=args.get("evidence") or {},
            confidence=args.get("confidence"),
            severity=args.get("severity"),
        )
        client.ensure_workspace()
        client.ensure_peer(observer_id)
        client.ensure_peer(subject_id)
        client.ensure_session(session_id, [observer_id, subject_id])
        created = client.create_messages(session_id, [message])
        return _json_result(
            success=True,
            workspace=client.workspace,
            session_id=session_id,
            observer=observer_id,
            subject=subject_id,
            messages_created=len(created) if isinstance(created, list) else 1,
        )
    except Exception as e:
        logger.debug("carabiner_record_feedback failed", exc_info=True)
        return _json_error(str(e))


def tool_record_episode(args: dict[str, Any], *, client: CarabinerHonchoClient | None = None, **_: Any) -> str:
    try:
        actor = args.get("actor") or args.get("agent") or args.get("observer")
        claim = (args.get("claim") or "").strip()
        if not actor or not claim:
            return _json_error("actor and claim are required")
        client = _client_or_default(client)
        actor_id = normalize_peer_id(actor)
        participants = [actor_id] + [normalize_peer_id(p) for p in args.get("participants", []) if p]
        session_id = _session_id_from_args(args)
        message = build_episode_message({**args, "actor": actor_id, "participants": participants})
        client.ensure_workspace()
        for peer in sorted(set(participants)):
            client.ensure_peer(peer)
        client.ensure_session(session_id, participants)
        created = client.create_messages(session_id, [message])
        return _json_result(
            success=True,
            workspace=client.workspace,
            session_id=session_id,
            actor=actor_id,
            messages_created=len(created) if isinstance(created, list) else 1,
        )
    except Exception as e:
        logger.debug("carabiner_record_episode failed", exc_info=True)
        return _json_error(str(e))


def tool_handoff_context(args: dict[str, Any], *, client: CarabinerHonchoClient | None = None, **_: Any) -> str:
    try:
        from_agent = args.get("from_agent")
        to_agent = args.get("to_agent")
        if not from_agent or not to_agent:
            return _json_error("from_agent and to_agent are required")
        client = _client_or_default(client)
        from_id = normalize_peer_id(from_agent)
        to_id = normalize_peer_id(to_agent)
        query = build_handoff_query(
            from_agent=from_id,
            to_agent=to_id,
            repo=args.get("repo"),
            task_type=args.get("task_type"),
            artifact_type=args.get("artifact_type"),
        )
        client.ensure_workspace()
        client.ensure_peer(from_id)
        client.ensure_peer(to_id)
        # Primary view: what the receiver has learned about the sender.
        receiver_view = client.get_representation(
            observer=to_id,
            target=from_id,
            search_query=query,
            session_id=args.get("session_id"),
            max_conclusions=int(args.get("max_conclusions") or 10),
        )
        # Secondary view: what the sender has learned about the receiver's expectations.
        sender_view = client.get_representation(
            observer=from_id,
            target=to_id,
            search_query=query,
            session_id=args.get("session_id"),
            max_conclusions=int(args.get("max_conclusions") or 10),
        )
        parts = []
        source = "representation"
        if receiver_view:
            parts.append(f"## {to_id}'s observed model of {from_id}\n{receiver_view}")
        if sender_view:
            parts.append(f"## {from_id}'s observed model of {to_id}\n{sender_view}")
        if not parts:
            raw_hits = _format_search_hits(client.search_workspace(query, limit=5), limit=5)
            if raw_hits:
                source = "raw_search_fallback"
                parts.append(
                    "## Raw relationship-memory hits (deriver/representation fallback)\n"
                    + raw_hits
                )
        return _json_result(
            success=True,
            workspace=client.workspace,
            from_agent=from_id,
            to_agent=to_id,
            query=query,
            source=source,
            context="\n\n".join(parts) or "No relationship context found yet.",
        )
    except Exception as e:
        logger.debug("carabiner_handoff_context failed", exc_info=True)
        return _json_error(str(e))


def tool_context(args: dict[str, Any], *, client: CarabinerHonchoClient | None = None, **_: Any) -> str:
    try:
        observer = args.get("observer")
        target = args.get("target") or args.get("subject")
        if not observer or not target:
            return _json_error("observer and target are required")
        client = _client_or_default(client)
        observer_id = normalize_peer_id(observer)
        target_id = normalize_peer_id(target)
        query = args.get("query") or build_handoff_query(
            from_agent=target_id,
            to_agent=observer_id,
            repo=args.get("repo"),
            task_type=args.get("task_type"),
            artifact_type=args.get("artifact_type"),
        )
        context = client.get_representation(
            observer=observer_id,
            target=target_id,
            search_query=query,
            session_id=args.get("session_id"),
            max_conclusions=int(args.get("max_conclusions") or 10),
        )
        source = "representation"
        if not context:
            raw_hits = _format_search_hits(client.search_workspace(query, limit=5), limit=5)
            if raw_hits:
                source = "raw_search_fallback"
                context = "## Raw relationship-memory hits (deriver/representation fallback)\n" + raw_hits
        return _json_result(success=True, workspace=client.workspace, observer=observer_id, target=target_id, source=source, context=context or "No relationship context found yet.")
    except Exception as e:
        logger.debug("carabiner_context failed", exc_info=True)
        return _json_error(str(e))


def tool_audit(args: dict[str, Any], *, client: CarabinerHonchoClient | None = None, **_: Any) -> str:
    try:
        client = _client_or_default(client)
        terms = [
            args.get("peer"), args.get("target"), args.get("repo"),
            args.get("task_type"), args.get("relationship"), args.get("query"),
        ]
        query = " ".join(str(t) for t in terms if t) or "peer_feedback collaboration_episode"
        result = client.search_workspace(query, limit=int(args.get("limit") or 10))
        return _json_result(success=True, workspace=client.workspace, query=query, results=result)
    except Exception as e:
        logger.debug("carabiner_audit failed", exc_info=True)
        return _json_error(str(e))


# ---------------------------------------------------------------------------
# Hook support
# ---------------------------------------------------------------------------

_hook_lock = threading.Lock()


def _configured_agent_id(cfg: CarabinerConfig) -> str:
    """Return the peer ID representing the current Hermes profile/agent."""
    value = cfg.agent_id or os.getenv("HERMES_PROFILE") or os.getenv("USER") or "agent:hermes"
    if ":" not in value:
        value = f"agent:{value}"
    return normalize_peer_id(value)


def _on_subagent_stop(**kwargs: Any) -> None:
    """Best-effort low-confidence episode capture for delegated workers."""
    cfg = load_carabiner_config()
    if not cfg.enabled or (_env("CARABINER_CAPTURE_SUBAGENTS", "HONCHO_RELATIONSHIP_CAPTURE_SUBAGENTS") or "false").lower() not in {"1", "true", "yes", "on"}:
        return
    summary = (kwargs.get("child_summary") or "").strip()
    child_role = kwargs.get("child_role") or "subagent"
    status = kwargs.get("child_status") or "unknown"
    if not summary:
        return
    parent_id = _configured_agent_id(cfg)
    child_id = normalize_peer_id(f"agent:{child_role}")
    with _hook_lock:
        try:
            tool_record_episode({
                "actor": parent_id,
                "participants": [child_id],
                "relationship": "delegation_outcome",
                "repo": cfg.default_repo,
                "task_type": cfg.default_task_type,
                "claim": f"{parent_id} delegated work to {child_id}; child finished with status {status}: {summary[:1200]}",
                "outcome": status,
                "confidence": 0.35,
                "evidence": {
                    "parent_session_id": kwargs.get("parent_session_id"),
                    "child_role": child_role,
                    "duration_ms": kwargs.get("duration_ms"),
                    "source": "subagent_stop_hook",
                },
            })
        except Exception:
            logger.debug("carabiner subagent capture failed", exc_info=True)


# ---------------------------------------------------------------------------
# Tool schemas / registration
# ---------------------------------------------------------------------------


def _object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


RECORD_FEEDBACK_SCHEMA = {
    "name": "carabiner_record_feedback",
    "description": "Record evidence-backed peer critique/feedback about observable work or handoff behavior in Honcho Carabiner memory.",
    "parameters": _object_schema({
        "observer": {"type": "string", "description": "Agent giving feedback, e.g. agent:kame-qa"},
        "subject": {"type": "string", "description": "Agent receiving feedback, e.g. agent:ika-frontend"},
        "relationship": {"type": "string", "description": "Collaboration relation, e.g. qa_handoff, code_review, design_handoff"},
        "repo": {"type": "string"},
        "task_type": {"type": "string"},
        "artifact_type": {"type": "string"},
        "raw_feedback": {"type": "string"},
        "operational_critique": {"type": "string", "description": "Actionable behavior/work-product critique; no personality labels."},
        "recommended_change": {"type": "string"},
        "evidence": {"type": "object"},
        "confidence": {"type": "number"},
        "severity": {"type": "string"},
        "session_id": {"type": "string"},
        "work_id": {"type": "string"},
    }, ["observer", "subject", "raw_feedback", "operational_critique"]),
}

RECORD_EPISODE_SCHEMA = {
    "name": "carabiner_record_episode",
    "description": "Record a compact collaboration episode in Honcho Carabiner memory.",
    "parameters": _object_schema({
        "actor": {"type": "string"},
        "participants": {"type": "array", "items": {"type": "string"}},
        "relationship": {"type": "string"},
        "repo": {"type": "string"},
        "task_type": {"type": "string"},
        "artifact_type": {"type": "string"},
        "claim": {"type": "string"},
        "outcome": {"type": "string"},
        "evidence": {"type": "object"},
        "confidence": {"type": "number"},
        "session_id": {"type": "string"},
        "work_id": {"type": "string"},
    }, ["actor", "claim"]),
}

HANDOFF_CONTEXT_SCHEMA = {
    "name": "carabiner_handoff_context",
    "description": "Retrieve scoped Honcho relationship context before one agent hands work to another.",
    "parameters": _object_schema({
        "from_agent": {"type": "string"},
        "to_agent": {"type": "string"},
        "repo": {"type": "string"},
        "task_type": {"type": "string"},
        "artifact_type": {"type": "string"},
        "session_id": {"type": "string"},
        "max_conclusions": {"type": "integer"},
    }, ["from_agent", "to_agent"]),
}

CONTEXT_SCHEMA = {
    "name": "carabiner_context",
    "description": "Retrieve Honcho relationship context for an observer/target peer pair.",
    "parameters": _object_schema({
        "observer": {"type": "string"},
        "target": {"type": "string"},
        "query": {"type": "string"},
        "repo": {"type": "string"},
        "task_type": {"type": "string"},
        "artifact_type": {"type": "string"},
        "session_id": {"type": "string"},
        "max_conclusions": {"type": "integer"},
    }, ["observer", "target"]),
}

AUDIT_SCHEMA = {
    "name": "carabiner_audit",
    "description": "Search/audit raw Honcho relationship-memory messages for a peer/repo/task scope.",
    "parameters": _object_schema({
        "peer": {"type": "string"},
        "target": {"type": "string"},
        "repo": {"type": "string"},
        "task_type": {"type": "string"},
        "relationship": {"type": "string"},
        "query": {"type": "string"},
        "limit": {"type": "integer"},
    }),
}


def _requirements_ok() -> bool:
    cfg = load_carabiner_config()
    return bool(cfg.enabled and cfg.base_url and cfg.api_key and cfg.workspace)


def register(ctx) -> None:
    ctx.register_tool(
        name="carabiner_record_feedback",
        toolset=TOOLSET,
        schema=RECORD_FEEDBACK_SCHEMA,
        handler=tool_record_feedback,
        check_fn=_requirements_ok,
        requires_env=["CARABINER_API_KEY"],
        description="Record peer feedback in Honcho-backed Carabiner memory",
        emoji="🧠",
    )
    ctx.register_tool(
        name="carabiner_record_episode",
        toolset=TOOLSET,
        schema=RECORD_EPISODE_SCHEMA,
        handler=tool_record_episode,
        check_fn=_requirements_ok,
        requires_env=["CARABINER_API_KEY"],
        description="Record a compact collaboration episode in Honcho",
        emoji="🧠",
    )
    ctx.register_tool(
        name="carabiner_handoff_context",
        toolset=TOOLSET,
        schema=HANDOFF_CONTEXT_SCHEMA,
        handler=tool_handoff_context,
        check_fn=_requirements_ok,
        requires_env=["CARABINER_API_KEY"],
        description="Fetch handoff-specific relationship context from Honcho",
        emoji="🧠",
    )
    ctx.register_tool(
        name="carabiner_context",
        toolset=TOOLSET,
        schema=CONTEXT_SCHEMA,
        handler=tool_context,
        check_fn=_requirements_ok,
        requires_env=["CARABINER_API_KEY"],
        description="Fetch observer/target relationship context from Honcho",
        emoji="🧠",
    )
    ctx.register_tool(
        name="carabiner_audit",
        toolset=TOOLSET,
        schema=AUDIT_SCHEMA,
        handler=tool_audit,
        check_fn=_requirements_ok,
        requires_env=["CARABINER_API_KEY"],
        description="Audit relationship-memory records in Honcho",
        emoji="🧠",
    )
    ctx.register_hook("subagent_stop", _on_subagent_stop)
