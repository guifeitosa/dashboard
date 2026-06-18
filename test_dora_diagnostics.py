"""Unit tests for build_dora_diagnostics — pure functions, no Streamlit."""
import pytest
from core_metrics import build_dora_diagnostics


# ── Helpers ───────────────────────────────────────────────────────────────────

def _m(
    lead: float | None = 5.0,    # High band (1-7d)
    freq: float | None = 3.0,    # High band (≤5d)
    mttr: float | None = 12.0,   # High band (<24h)
    cfr:  float | None = 20.0,   # High band (≤30%)
) -> dict:
    return {
        "lead_time_days":       lead,
        "deploy_freq_interval": freq,
        "mttr_hours":           mttr,
        "cfr_percent":          cfr,
    }


# ── No previous data ──────────────────────────────────────────────────────────

class TestNoPrevData:
    def test_returns_empty_when_prev_is_none(self):
        diag, rec = build_dora_diagnostics(_m(), None)
        assert diag == []
        assert rec == []

    def test_lists_are_always_parallel(self):
        diag, rec = build_dora_diagnostics(_m(), _m())
        assert len(diag) == len(rec)


# ── Rule 1: Faixa em deterioração ─────────────────────────────────────────────

class TestRule1Deterioracao:
    def test_lead_time_worsened_fires(self):
        # High(2.0d) → Medium(10.0d)
        diag, rec = build_dora_diagnostics(_m(lead=10.0), _m(lead=2.0))
        assert any("Lead Time" in d and "piorou" in d for d in diag)
        assert any("aprovações" in r for r in rec)

    def test_deploy_freq_worsened_fires(self):
        # High(3.0d interval) → Medium(15.0d interval)
        diag, rec = build_dora_diagnostics(_m(freq=15.0), _m(freq=3.0))
        assert any("Deployment Frequency" in d and "piorou" in d for d in diag)
        assert any("prioridade" in r or "cautela" in r for r in rec)

    def test_mttr_worsened_fires(self):
        # Elite(0.5h) → High(12.0h)
        diag, rec = build_dora_diagnostics(_m(mttr=12.0), _m(mttr=0.5))
        assert any("MTTR" in d and "piorou" in d for d in diag)
        assert any("incidentes" in r for r in rec)

    def test_cfr_worsened_fires(self):
        # Elite(10%) → High(25%)
        diag, rec = build_dora_diagnostics(_m(cfr=25.0), _m(cfr=10.0))
        assert any("CFR" in d and "piorou" in d for d in diag)
        assert any("falha" in r or "padrão" in r for r in rec)

    def test_na_current_does_not_fire(self):
        diag, _ = build_dora_diagnostics(_m(lead=None), _m(lead=2.0))
        assert not any("Lead Time" in d and "piorou" in d for d in diag)

    def test_na_prev_does_not_fire(self):
        diag, _ = build_dora_diagnostics(_m(lead=10.0), _m(lead=None))
        assert not any("Lead Time" in d and "piorou" in d for d in diag)

    def test_same_band_does_not_fire(self):
        # 3.0d and 5.0d are both High band (1-7d)
        diag, _ = build_dora_diagnostics(_m(lead=5.0), _m(lead=3.0))
        assert not any("piorou" in d for d in diag)

    def test_band_text_includes_prev_and_cur(self):
        # High → Medium: message must name both bands
        diag, _ = build_dora_diagnostics(_m(lead=10.0), _m(lead=2.0))
        assert any("High" in d and "Medium" in d for d in diag)

    def test_multiple_metrics_fire_independently(self):
        # Lead Time High→Medium, CFR High→Low — two separate entries
        diag, rec = build_dora_diagnostics(
            _m(lead=10.0, cfr=50.0),
            _m(lead=2.0,  cfr=20.0),
        )
        assert len([d for d in diag if "piorou" in d]) == 2
        assert len(diag) == len(rec)


# ── Rule 2: Faixa em melhoria ─────────────────────────────────────────────────

class TestRule2Melhoria:
    def test_lead_time_improved_fires(self):
        # Medium(10.0d) → High(3.0d)
        diag, rec = build_dora_diagnostics(_m(lead=3.0), _m(lead=10.0))
        assert any("Lead Time" in d and "melhorou" in d for d in diag)
        assert any("manter" in r for r in rec)

    def test_cfr_improved_fires(self):
        # High(25%) → Elite(10%)
        diag, rec = build_dora_diagnostics(_m(cfr=10.0), _m(cfr=25.0))
        assert any("CFR" in d and "melhorou" in d for d in diag)
        assert any("manter" in r for r in rec)

    def test_na_does_not_fire(self):
        diag, _ = build_dora_diagnostics(_m(cfr=None), _m(cfr=25.0))
        assert not any("CFR" in d and "melhorou" in d for d in diag)

    def test_same_band_does_not_fire(self):
        # 3.0d and 5.0d both High — no "melhorou" even though value improved
        diag, _ = build_dora_diagnostics(_m(lead=3.0), _m(lead=5.0))
        assert not any("melhorou" in d for d in diag)

    def test_generic_recommendation_text(self):
        diag, rec = build_dora_diagnostics(_m(mttr=0.5), _m(mttr=12.0))
        assert any("MTTR" in d and "melhorou" in d for d in diag)
        assert any("manter essa prática" in r for r in rec)


# ── Rule 3: Cruzamento CFR × Deployment Frequency ────────────────────────────

class TestRule3CruzamentoCFRDeploy:
    def test_fires_when_cfr_up_and_freq_down(self):
        # CFR 20%→35%, deploy interval 3d→8d
        diag, rec = build_dora_diagnostics(
            _m(cfr=35.0, freq=8.0),
            _m(cfr=20.0, freq=3.0),
        )
        rule3 = [d for d in diag if "frequência de deploy" in d]
        assert len(rule3) == 1
        assert any("atrasando" in r or "cautela" in r for r in rec)

    def test_does_not_fire_when_cfr_up_only(self):
        # freq unchanged
        diag, _ = build_dora_diagnostics(
            _m(cfr=35.0, freq=3.0),
            _m(cfr=20.0, freq=3.0),
        )
        assert not any("frequência de deploy" in d for d in diag)

    def test_does_not_fire_when_freq_down_only(self):
        # CFR unchanged
        diag, _ = build_dora_diagnostics(
            _m(cfr=20.0, freq=8.0),
            _m(cfr=20.0, freq=3.0),
        )
        assert not any("frequência de deploy" in d for d in diag)

    def test_does_not_fire_when_cfr_na(self):
        diag, _ = build_dora_diagnostics(
            _m(cfr=None, freq=8.0),
            _m(cfr=20.0, freq=3.0),
        )
        assert not any("frequência de deploy" in d for d in diag)

    def test_does_not_fire_when_freq_na(self):
        diag, _ = build_dora_diagnostics(
            _m(cfr=35.0, freq=None),
            _m(cfr=20.0, freq=3.0),
        )
        assert not any("frequência de deploy" in d for d in diag)

    def test_fires_independently_of_band_change(self):
        # CFR stays in High band (20%→25%), freq stays in High band (3d→3.5d)
        # Rule 1/2 must NOT fire; Rule 3 must fire (raw values both went up)
        diag, _ = build_dora_diagnostics(
            _m(cfr=25.0, freq=3.5),
            _m(cfr=20.0, freq=3.0),
        )
        assert any("frequência de deploy" in d for d in diag)
        assert not any("piorou de faixa" in d for d in diag)
        assert not any("melhorou de faixa" in d for d in diag)
