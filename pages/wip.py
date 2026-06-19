"""WIP — Work in Progress page with CFD and diagnostics."""
import altair as alt
import pandas as pd
import streamlit as st

from core_metrics import build_wip_diagnostics, compute_wip_limit, prepare_df
from db import engine
from squad_health import render_context_bar, render_squad_health
from status_time import reconstruct_wip_history

# Canonical flow order for História issues
_HISTORIA_FLOW = [
    "Backlog",
    "Discovery",
    "Design",
    "Pronto pra Refinamento",
    "Em Refinamento",
    "Pronto pra desenvolvimento",
    "Sprint Backlog",
    "Em desenvolvimento",
    "Pronto pra testes",
    "Em testes",
    "Revisão de Produto",
    "Pronto pra produção",
]

_CFD_COLORS = [
    "#bfdbfe", "#93c5fd", "#60a5fa", "#3b82f6", "#2563eb", "#1d4ed8",
    "#1e40af", "#a7f3d0", "#34d399", "#10b981", "#059669", "#047857",
    "#6ee7b7", "#fde68a", "#fbbf24", "#f59e0b",
]


@st.cache_data(ttl=300)
def _load_issues() -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM issues_raw", engine)


@st.cache_data(ttl=300)
def _load_transitions() -> pd.DataFrame:
    try:
        return pd.read_sql("SELECT * FROM issue_transitions", engine)
    except Exception:
        return pd.DataFrame()


def _card(label: str, value: str, color: str = "#0f172a", sub: str = "") -> str:
    sub_el = (
        f'<div style="font-size:11px;color:#64748b;margin-top:5px;line-height:1.4;">{sub}</div>'
        if sub else ""
    )
    return (
        f'<div style="background:white;border-radius:12px;padding:18px 22px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07);border:1px solid #f1f5f9;">'
        f'<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;margin-bottom:6px;">{label}</div>'
        f'<div style="font-size:28px;font-weight:800;color:{color};line-height:1.1;">{value}</div>'
        f'{sub_el}'
        f'</div>'
    )


def _section_label(text: str) -> None:
    st.markdown(
        f'<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
        f'letter-spacing:.07em;margin:20px 0 10px;">{text}</div>',
        unsafe_allow_html=True,
    )


def _ordered_statuses(statuses: list[str]) -> list[str]:
    in_flow = [s for s in _HISTORIA_FLOW if s in statuses]
    others = sorted(s for s in statuses if s not in _HISTORIA_FLOW)
    return in_flow + others


def main() -> None:
    render_squad_health()
    render_context_bar()

    selected_team = st.session_state.get("global_team", "Todos")
    team_arg = None if selected_team == "Todos" else selected_team

    df_raw = _load_issues()
    df_transitions = _load_transitions()
    df = prepare_df(df_raw.copy())

    # ── Introduction ──────────────────────────────────────────────────────────
    st.markdown(
        """
        <div style="background:white;border-radius:12px;padding:16px 22px;margin-bottom:12px;
                    box-shadow:0 1px 3px rgba(0,0,0,.06);border:1px solid #f1f5f9;">
            <span style="font-size:14px;color:#475569;line-height:1.6;">
            <strong>WIP (Work in Progress)</strong> é a quantidade de itens que o time está
            trabalhando ao mesmo tempo. Quanto mais coisas em paralelo, mais tempo cada uma
            demora pra terminar. Essa página mostra onde o trabalho está acumulando e o que
            fazer a respeito.
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Compute data ──────────────────────────────────────────────────────────
    wip_data = compute_wip_limit(df, df_transitions, team=team_arg)
    wip_history = reconstruct_wip_history(df, df_transitions, team=team_arg)

    wip_current = wip_data.get("wip_current", {})
    wip_limit   = wip_data.get("wip_limit", {})
    over_limit  = wip_data.get("over_limit", [])
    total_wip   = sum(wip_current.values())

    top_status = max(wip_current, key=wip_current.get) if wip_current else "—"
    top_count  = wip_current.get(top_status, 0) if wip_current else 0
    over_count = len(over_limit)

    total_limit = sum(wip_limit.values())
    if total_limit > 0 and total_wip > total_limit:
        impact_pct = (total_wip / total_limit - 1) * 100
        impact_label = f"+{impact_pct:.0f}%"
        impact_sub   = "acima do que o time absorve bem"
        impact_color = "#dc2626"
    else:
        impact_label = "No limite"
        impact_sub   = "volume dentro do ritmo do time"
        impact_color = "#15803d"

    # ── KPI cards ─────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.html(_card(
            "Total em andamento",
            str(total_wip) if total_wip > 0 else "—",
            sub=f"{wip_data.get('throughput_avg', 0):.1f} itens/mês (média histórica)"
        ))
    with c2:
        st.html(_card(
            "Status com mais itens",
            f"{top_count}" if wip_current else "—",
            sub=top_status if wip_current else "",
        ))
    with c3:
        color = "#dc2626" if over_count > 0 else "#15803d"
        label = f"{over_count} acima" if over_count > 0 else "Todos OK"
        sub   = "do que o time absorve" if over_count > 0 else "nenhum acumulando"
        st.html(_card("Status sobrecarregados", label, color=color, sub=sub))
    with c4:
        st.html(_card("Impacto no ritmo", impact_label, color=impact_color, sub=impact_sub))

    # ── Expander técnico ──────────────────────────────────────────────────────
    with st.expander("Como esses limites são calculados?"):
        st.markdown(
            "O limite ideal por status é estimado com base no ritmo histórico do time — "
            "quantos itens ele entrega por mês e quanto tempo cada item passa em cada etapa. "
            "Quanto mais itens além desse limite, maior a tendência de aumento no tempo de entrega. "
            "Esse conceito tem base na Lei de Little, amplamente usada em gestão de fluxo."
        )

    # ── CFD chart ─────────────────────────────────────────────────────────────
    _section_label("Diagrama de Fluxo Cumulativo (CFD)")

    chart_df = wip_history.copy()
    if team_arg:
        chart_df = chart_df[chart_df["team"] == team_arg]

    if chart_df.empty:
        st.info(
            "Dados insuficientes para o CFD. "
            "O histórico de transições é necessário para reconstruir o fluxo ao longo do tempo."
        )
    else:
        statuses_in_data = chart_df["status"].unique().tolist()
        status_order = _ordered_statuses(statuses_in_data)
        color_range = (_CFD_COLORS * 4)[:len(status_order)]

        chart = (
            alt.Chart(chart_df)
            .mark_area(interpolate="monotone", opacity=0.88)
            .encode(
                x=alt.X("date:T", title="Semana", axis=alt.Axis(format="%d/%m")),
                y=alt.Y("count:Q", stack=True, title="Itens em andamento"),
                color=alt.Color(
                    "status:N",
                    scale=alt.Scale(domain=status_order, range=color_range),
                    sort=status_order,
                    legend=alt.Legend(title="Status", orient="right", labelFontSize=11),
                ),
                order=alt.Order("status:N", sort="ascending"),
                tooltip=[
                    alt.Tooltip("date:T", title="Semana", format="%d/%m/%Y"),
                    alt.Tooltip("status:N", title="Status"),
                    alt.Tooltip("count:Q", title="Itens"),
                ],
            )
            .properties(height=290)
        )
        st.altair_chart(
            chart.configure_view(strokeWidth=0),
            use_container_width=True,
        )
        st.markdown(
            '<div style="font-size:12px;color:#64748b;margin-top:-6px;padding-bottom:4px;">'
            "Bandas paralelas e do mesmo tamanho = fluxo saudável. "
            "Banda crescendo enquanto outra encolhe = gargalo acumulando naquele status. "
            "Entrega consistente = todas as bandas avançando juntas ao longo do tempo."
            "</div>",
            unsafe_allow_html=True,
        )

    # ── WIP atual por status ──────────────────────────────────────────────────
    _section_label("WIP Atual por Status")

    if not wip_current:
        st.info("Nenhum item em andamento encontrado.")
    else:
        status_order_wip = _ordered_statuses(list(wip_current.keys()))
        rows = []
        for status in status_order_wip:
            count = wip_current.get(status, 0)
            if count == 0:
                continue
            limit = wip_limit.get(status, 0)
            if limit > 0:
                pct = count / limit
                if pct <= 0.8:
                    situacao = "✅ OK"
                elif pct <= 1.0:
                    situacao = "⚠️ Próximo do limite"
                else:
                    situacao = "🔴 Acima do limite"
            else:
                situacao = "— Sem dados"
            rows.append({
                "Status": status,
                "Itens agora": count,
                "Limite ideal": limit if limit > 0 else "—",
                "Situação": situacao,
            })

        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ── Diagnóstico & Recomendação ────────────────────────────────────────────
    _section_label("Diagnóstico & Recomendação")

    events = build_wip_diagnostics(
        wip_data, wip_history, team_label=selected_team, period=""
    )

    diag_items = [e for e in events if e.layer in ("insight", "diagnostic")]
    rec_items  = [e for e in events if e.layer == "recommendation"]

    if not diag_items:
        st.markdown(
            '<span style="font-size:13px;color:#94a3b8;">'
            "Nenhum fator de destaque identificado no WIP atual."
            "</span>",
            unsafe_allow_html=True,
        )
    else:
        _sev_icon = {
            "critical": "🔴", "high": "🟠", "medium": "🟡",
            "low": "🟢", "info": "ℹ️",
        }
        col_d, col_r = st.columns(2, gap="large")
        with col_d:
            st.markdown("**Diagnóstico**")
            for e in diag_items:
                icon = _sev_icon.get(e.severity, "•")
                st.markdown(f"- {icon} **{e.title}**  \n  {e.description}")
        with col_r:
            st.markdown("**Recomendação**")
            for r in rec_items:
                st.markdown(f"- {r.description}")


main()
