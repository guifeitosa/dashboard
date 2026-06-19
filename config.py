"""Central configuration loader for the Métricas Dashboard.

Reads config.yaml from the project root. Falls back to safe hardcoded values
when the file is missing or malformed — the system never breaks without it.

Usage
-----
    from config import get_config
    cfg = get_config()
    cfg.terminal_statuses        # frozenset[str] (lowercased)
    cfg.near_done_statuses       # frozenset[str] (raw case)
    cfg.flow_order("História")   # list[str]

Reload (tests only)
-------------------
    from config import reload_config
    reload_config()
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

_LOG = logging.getLogger(__name__)
_YAML_PATH = Path(__file__).parent / "config.yaml"

# ── Fallback constants (mirror current hardcoded values) ─────────────────────

_FALLBACK_TERMINAL: frozenset[str] = frozenset({
    "feito", "concluído", "concluido", "done", "fechado", "closed",
    "resolvido", "resolved", "completo", "completed",
    "implantado com sucesso", "implantado com falha",
})
_FALLBACK_NEAR_DONE: frozenset[str] = frozenset({"Revisão de Produto", "Pronto pra produção"})
_FALLBACK_GMUD_SUCCESS: frozenset[str] = frozenset({"implantado com sucesso"})
_FALLBACK_GMUD_FAILURE: frozenset[str] = frozenset({"implantado com falha"})
_FALLBACK_SUBTASK_CR_TYPES: frozenset[str] = frozenset({"Subtask"})
_FALLBACK_ALL_SUBTASK_TYPES: frozenset[str] = frozenset({"Subtask"})
_FALLBACK_CODE_REVIEW_STATUS: str = "Code Review"


def _norm(s: str) -> str:
    """Strip '(migrated)' suffix and lowercase — matches jira_client._normalize_migrated + .lower()."""
    s = s.strip()
    if s.endswith(" (migrated)"):
        s = s[: -len(" (migrated)")].strip()
    return s.lower()


class DashboardConfig:
    """Typed, read-only view over config.yaml."""

    def __init__(self, raw: dict | None = None) -> None:
        self._raw: dict = raw or {}

    # ── Workflow — derived sets ───────────────────────────────────────────────

    @property
    def terminal_statuses(self) -> frozenset[str]:
        """Lowercased union of ALL terminal statuses from every workflow group."""
        groups = self._raw.get("workflow", {}).get("groups", {})
        if not groups:
            return _FALLBACK_TERMINAL
        result: set[str] = set()
        for g in groups.values():
            for key in ("terminal", "terminal_success", "terminal_failure"):
                for s in g.get(key, []):
                    result.add(_norm(s))
        return frozenset(result)

    @property
    def near_done_statuses(self) -> frozenset[str]:
        """Raw-case near-done statuses from the 'padrao' group."""
        groups = self._raw.get("workflow", {}).get("groups", {})
        nd = groups.get("padrao", {}).get("near_done", [])
        return frozenset(nd) if nd else _FALLBACK_NEAR_DONE

    @property
    def gmud_success_statuses(self) -> frozenset[str]:
        """Lowercased GMUD successful-deployment statuses."""
        groups = self._raw.get("workflow", {}).get("groups", {})
        lst = groups.get("gmud", {}).get("terminal_success", [])
        return frozenset(_norm(s) for s in lst) if lst else _FALLBACK_GMUD_SUCCESS

    @property
    def gmud_failure_statuses(self) -> frozenset[str]:
        """Lowercased GMUD failed-deployment statuses."""
        groups = self._raw.get("workflow", {}).get("groups", {})
        lst = groups.get("gmud", {}).get("terminal_failure", [])
        return frozenset(_norm(s) for s in lst) if lst else _FALLBACK_GMUD_FAILURE

    @property
    def subtask_types_with_code_review(self) -> frozenset[str]:
        """Issuetypes (raw case) where has_code_review=true — e.g. DEV, Bug-Dev."""
        groups = self._raw.get("workflow", {}).get("groups", {})
        types: set[str] = set()
        for g in groups.values():
            if g.get("has_code_review"):
                types.update(g.get("types", []))
        return frozenset(types) if types else _FALLBACK_SUBTASK_CR_TYPES

    @property
    def code_review_status(self) -> str:
        """The status name representing Code Review (raw case)."""
        groups = self._raw.get("workflow", {}).get("groups", {})
        for g in groups.values():
            if g.get("has_code_review") and g.get("code_review_status"):
                return str(g["code_review_status"])
        return _FALLBACK_CODE_REVIEW_STATUS

    @property
    def all_subtask_types(self) -> frozenset[str]:
        """All issuetypes belonging to subtask groups (raw case)."""
        groups = self._raw.get("workflow", {}).get("groups", {})
        types: set[str] = set()
        for name, g in groups.items():
            if name.startswith("subtask"):
                types.update(g.get("types", []))
        return frozenset(types) if types else _FALLBACK_ALL_SUBTASK_TYPES

    # ── Workflow — lookup helpers ─────────────────────────────────────────────

    def flow_order(self, issuetype: str) -> list[str]:
        """Return the flow_order list for the given issuetype.
        Falls back to 'padrao' flow when type is unknown.
        """
        groups = self._raw.get("workflow", {}).get("groups", {})
        for g in groups.values():
            if issuetype in g.get("types", []):
                return list(g.get("flow_order", []))
        return list(groups.get("padrao", {}).get("flow_order", []))

    def group_for_type(self, issuetype: str) -> str | None:
        """Return the group name for an issuetype, or None if not found."""
        groups = self._raw.get("workflow", {}).get("groups", {})
        for name, g in groups.items():
            if issuetype in g.get("types", []):
                return name
        return None

    def types_in_group(self, group_name: str) -> list[str]:
        """Return the list of issuetypes in a given group."""
        groups = self._raw.get("workflow", {}).get("groups", {})
        return list(groups.get(group_name, {}).get("types", []))

    # ── Display properties ────────────────────────────────────────────────────

    @property
    def max_home_diagnostics(self) -> int:
        return int(self._raw.get("display", {}).get("max_home_diagnostics", 4))

    @property
    def wip_limit_multiplier(self) -> float:
        return float(self._raw.get("display", {}).get("wip_limit_multiplier", 1.5))

    @property
    def aging_critical_days(self) -> int:
        return int(self._raw.get("display", {}).get("aging_critical_days", 30))

    @property
    def code_review_alert_days(self) -> int:
        return int(self._raw.get("display", {}).get("code_review_alert_days", 5))

    @property
    def near_done_alert_days(self) -> int:
        return int(self._raw.get("display", {}).get("near_done_alert_days", 3))

    # ── Teams properties ──────────────────────────────────────────────────────

    @property
    def team_names(self) -> list[str]:
        return list(self._raw.get("teams", {}).get("names", []))

    @property
    def team_source(self) -> str:
        return str(self._raw.get("teams", {}).get("source", "round_robin"))

    # ── Jira properties ───────────────────────────────────────────────────────

    @property
    def jira_custom_fields(self) -> dict[str, str]:
        return dict(self._raw.get("jira", {}).get("custom_fields", {}))

    @property
    def jira_project_key(self) -> str:
        return str(self._raw.get("jira", {}).get("project_key", "TD"))

    @property
    def jira_url(self) -> str:
        return str(self._raw.get("jira", {}).get("url", ""))


# ── Module-level singleton ────────────────────────────────────────────────────

def _load_yaml() -> dict:
    if not _YAML_PATH.exists():
        _LOG.debug("config.yaml not found at %s — using fallback values", _YAML_PATH)
        return {}
    try:
        import yaml
        with open(_YAML_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        _LOG.warning("Failed to load config.yaml: %s — using fallback values", exc)
        return {}


@lru_cache(maxsize=1)
def get_config() -> DashboardConfig:
    """Return the singleton DashboardConfig. Cached after first call."""
    return DashboardConfig(_load_yaml())


def reload_config() -> DashboardConfig:
    """Clear the cache and reload config.yaml. Intended for tests."""
    get_config.cache_clear()
    return get_config()
