"""Tests for DashboardConfig (config.py)."""
from __future__ import annotations

import pytest

SAMPLE_YAML = """
jira:
  url: "https://test.atlassian.net"
  project_key: "TEST"
  custom_fields:
    team: "customfield_10001"
    data_implantacao: "customfield_10015"

workflow:
  groups:
    padrao:
      types: ["História", "Melhoria"]
      terminal: ["Concluído", "Done", "Feito (migrated)"]
      near_done: ["Revisão de Produto", "Pronto pra produção"]
      in_progress: ["Em desenvolvimento"]
      flow_order: ["Backlog", "Em desenvolvimento", "Revisão de Produto", "Concluído"]
      lead_time:
        start_status: "Em desenvolvimento"
        end_status: "Concluído"
        fallback_start: "created"
      cycle_time:
        start_status: "Em desenvolvimento"
        end_status: "Revisão de Produto"
        fallback_end: "Concluído"

    incidente:
      types: ["Incidente"]
      terminal: ["Concluído", "Done"]
      in_progress: ["Em Desenvolvimento"]
      flow_order: ["Sprint Backlog", "Em Desenvolvimento", "Concluído"]
      lead_time: null
      cycle_time: null

    gmud:
      types: ["GMUD"]
      terminal_success: ["Implantado com Sucesso"]
      terminal_failure: ["Implantado com Falha"]
      in_progress: ["Aguardando Implantação"]
      flow_order: ["Sprint Backlog", "Aguardando Implantação", "Implantado com Sucesso"]
      lead_time: null
      cycle_time: null

    subtask_dev:
      types: ["DEV", "Bug-Dev"]
      terminal: ["Concluído", "Done"]
      has_code_review: true
      code_review_status: "Code Review"
      in_progress: ["Em desenvolvimento", "Code Review"]
      flow_order: ["Sprint Backlog", "Em desenvolvimento", "Code Review", "Concluído"]
      lead_time: null
      cycle_time: null

    subtask_qa:
      types: ["QA"]
      terminal: ["Concluído", "Done"]
      has_code_review: false
      in_progress: ["Em desenvolvimento"]
      flow_order: ["Sprint Backlog", "Em desenvolvimento", "Concluído"]
      lead_time: null
      cycle_time: null

teams:
  source: "jira_field"
  fallback: "round_robin"
  names: ["Time Alpha", "Time Beta"]

display:
  max_home_diagnostics: 4
  wip_limit_multiplier: 1.5
  aging_critical_days: 30
  code_review_alert_days: 5
  near_done_alert_days: 3
"""


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(SAMPLE_YAML, encoding="utf-8")
    import config as mod
    monkeypatch.setattr(mod, "_YAML_PATH", yaml_file)
    mod.get_config.cache_clear()  # flush any previous singleton
    yield mod.get_config()
    # Only clear the cache — do NOT repopulate while _YAML_PATH is still patched.
    # monkeypatch will restore _YAML_PATH afterward; next caller gets the real yaml.
    mod.get_config.cache_clear()


@pytest.fixture
def cfg_no_yaml(tmp_path, monkeypatch):
    import config as mod
    monkeypatch.setattr(mod, "_YAML_PATH", tmp_path / "does_not_exist.yaml")
    mod.get_config.cache_clear()
    yield mod.get_config()
    mod.get_config.cache_clear()


# ── Load from YAML ────────────────────────────────────────────────────────────

class TestLoadFromYaml:
    def test_terminal_statuses_is_union_of_all_groups(self, cfg):
        ts = cfg.terminal_statuses
        assert "concluído" in ts
        assert "done" in ts
        assert "implantado com sucesso" in ts
        assert "implantado com falha" in ts

    def test_terminal_statuses_lowercased(self, cfg):
        for s in cfg.terminal_statuses:
            assert s == s.lower(), f"Expected lowercase, got: {s!r}"

    def test_migrated_suffix_stripped_in_terminal(self, cfg):
        # "Feito (migrated)" → "feito" (not "feito (migrated)")
        assert "feito" in cfg.terminal_statuses
        assert "feito (migrated)" not in cfg.terminal_statuses

    def test_subtask_types_with_code_review_contains_dev_and_bug_dev(self, cfg):
        cr = cfg.subtask_types_with_code_review
        assert "DEV" in cr
        assert "Bug-Dev" in cr

    def test_subtask_types_with_code_review_excludes_qa(self, cfg):
        assert "QA" not in cfg.subtask_types_with_code_review

    def test_group_for_type_historia(self, cfg):
        assert cfg.group_for_type("História") == "padrao"

    def test_group_for_type_qa(self, cfg):
        assert cfg.group_for_type("QA") == "subtask_qa"

    def test_group_for_type_gmud(self, cfg):
        assert cfg.group_for_type("GMUD") == "gmud"

    def test_group_for_type_unknown_returns_none(self, cfg):
        assert cfg.group_for_type("TipoDesconhecido") is None

    def test_flow_order_incidente(self, cfg):
        fo = cfg.flow_order("Incidente")
        assert fo == ["Sprint Backlog", "Em Desenvolvimento", "Concluído"]

    def test_flow_order_unknown_type_falls_back_to_padrao(self, cfg):
        fo = cfg.flow_order("TipoNaoExistente")
        assert "Em desenvolvimento" in fo

    def test_types_in_group_padrao(self, cfg):
        types = cfg.types_in_group("padrao")
        assert "História" in types
        assert "Melhoria" in types

    def test_types_in_group_subtask_dev(self, cfg):
        types = cfg.types_in_group("subtask_dev")
        assert "DEV" in types
        assert "Bug-Dev" in types

    def test_all_subtask_types_contains_dev_qa_bugdev(self, cfg):
        types = cfg.all_subtask_types
        assert "DEV" in types
        assert "QA" in types
        assert "Bug-Dev" in types

    def test_near_done_statuses_raw_case(self, cfg):
        nd = cfg.near_done_statuses
        assert "Revisão de Produto" in nd
        assert "Pronto pra produção" in nd

    def test_code_review_status(self, cfg):
        assert cfg.code_review_status == "Code Review"

    def test_gmud_success_statuses_lowercased(self, cfg):
        assert "implantado com sucesso" in cfg.gmud_success_statuses

    def test_gmud_failure_statuses_lowercased(self, cfg):
        assert "implantado com falha" in cfg.gmud_failure_statuses

    def test_jira_custom_fields(self, cfg):
        cf = cfg.jira_custom_fields
        assert cf["team"] == "customfield_10001"
        assert cf["data_implantacao"] == "customfield_10015"

    def test_jira_project_key(self, cfg):
        assert cfg.jira_project_key == "TEST"

    def test_team_names(self, cfg):
        assert cfg.team_names == ["Time Alpha", "Time Beta"]

    def test_team_source(self, cfg):
        assert cfg.team_source == "jira_field"

    def test_numeric_types_int(self, cfg):
        assert isinstance(cfg.max_home_diagnostics, int)
        assert isinstance(cfg.aging_critical_days, int)
        assert isinstance(cfg.code_review_alert_days, int)
        assert isinstance(cfg.near_done_alert_days, int)

    def test_numeric_types_float(self, cfg):
        assert isinstance(cfg.wip_limit_multiplier, float)

    def test_numeric_values_correct(self, cfg):
        assert cfg.max_home_diagnostics == 4
        assert cfg.wip_limit_multiplier == 1.5
        assert cfg.aging_critical_days == 30
        assert cfg.code_review_alert_days == 5
        assert cfg.near_done_alert_days == 3


# ── Fallback (no YAML) ────────────────────────────────────────────────────────

class TestFallback:
    def test_terminal_statuses_non_empty(self, cfg_no_yaml):
        ts = cfg_no_yaml.terminal_statuses
        assert isinstance(ts, frozenset)
        assert len(ts) > 0

    def test_terminal_fallback_has_feito_and_done(self, cfg_no_yaml):
        assert "feito" in cfg_no_yaml.terminal_statuses
        assert "done" in cfg_no_yaml.terminal_statuses

    def test_near_done_fallback(self, cfg_no_yaml):
        nd = cfg_no_yaml.near_done_statuses
        assert "Revisão de Produto" in nd
        assert "Pronto pra produção" in nd

    def test_numeric_fallback_values(self, cfg_no_yaml):
        assert cfg_no_yaml.max_home_diagnostics == 4
        assert cfg_no_yaml.wip_limit_multiplier == 1.5
        assert cfg_no_yaml.aging_critical_days == 30
        assert cfg_no_yaml.code_review_alert_days == 5
        assert cfg_no_yaml.near_done_alert_days == 3

    def test_subtask_cr_fallback_non_empty(self, cfg_no_yaml):
        assert len(cfg_no_yaml.subtask_types_with_code_review) > 0

    def test_group_for_type_fallback_returns_none(self, cfg_no_yaml):
        assert cfg_no_yaml.group_for_type("História") is None


# ── Singleton ─────────────────────────────────────────────────────────────────

class TestLeadCycleTimeConfig:
    def test_lead_time_config_historia_returns_dict(self, cfg):
        lt = cfg.lead_time_config("História")
        assert isinstance(lt, dict)
        assert lt["start_status"] == "Em desenvolvimento"
        assert lt["end_status"] == "Concluído"
        assert lt["fallback_start"] == "created"

    def test_lead_time_config_tarefa_returns_padrao(self, cfg):
        lt = cfg.lead_time_config("Tarefa")
        assert lt is not None
        assert lt["end_status"] == "Concluído"

    def test_lead_time_config_gmud_returns_none(self, cfg):
        assert cfg.lead_time_config("GMUD") is None

    def test_lead_time_config_incidente_returns_none(self, cfg):
        assert cfg.lead_time_config("Incidente") is None

    def test_lead_time_config_dev_subtask_returns_none(self, cfg):
        assert cfg.lead_time_config("DEV") is None

    def test_cycle_time_config_tarefa_returns_dict_with_fallback(self, cfg):
        ct = cfg.cycle_time_config("Tarefa")
        assert isinstance(ct, dict)
        assert ct["start_status"] == "Em desenvolvimento"
        assert ct["end_status"] == "Revisão de Produto"
        assert ct["fallback_end"] == "Concluído"

    def test_cycle_time_config_incidente_returns_none(self, cfg):
        assert cfg.cycle_time_config("Incidente") is None

    def test_cycle_time_config_gmud_returns_none(self, cfg):
        assert cfg.cycle_time_config("GMUD") is None

    def test_lead_time_config_unknown_type_falls_back_to_padrao(self, cfg):
        lt = cfg.lead_time_config("TipoDesconhecido")
        assert lt is not None
        assert lt["start_status"] == "Em desenvolvimento"


class TestSingleton:
    def test_same_instance_on_consecutive_calls(self, cfg, monkeypatch):
        import config as mod
        c1 = mod.get_config()
        c2 = mod.get_config()
        assert c1 is c2

    def test_reload_forces_new_instance(self, tmp_path, monkeypatch):
        import config as mod
        yaml_v1 = tmp_path / "config.yaml"
        yaml_v1.write_text("display:\n  max_home_diagnostics: 7\n", encoding="utf-8")
        monkeypatch.setattr(mod, "_YAML_PATH", yaml_v1)
        mod.get_config.cache_clear()
        assert mod.get_config().max_home_diagnostics == 7

        # Overwrite same file with different value
        yaml_v1.write_text("display:\n  max_home_diagnostics: 12\n", encoding="utf-8")
        mod.get_config.cache_clear()
        assert mod.get_config().max_home_diagnostics == 12
        mod.get_config.cache_clear()  # leave cache empty; monkeypatch restores path
