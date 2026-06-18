"""
scripts/diagnose_status_categories.py

Pergunta central: por que 83 Incidentes com status "Feito" não têm
resolutiondate no banco, enquanto 348 Histórias com o mesmo status têm?

Hipótese: Jira pode retornar statusCategory.key diferente para o mesmo
nome de status quando o tipo de issue usa um workflow diferente.

O fallback em jira_client.normalize_issue faz:
    if not resolutiondate and status_category == "done":
        resolutiondate = updated
Se statusCategory.key != "done" para Incidente/Feito, o fallback não
dispara e resolutiondate fica nulo → is_resolved=False → MTTR quebra.

Uso (da raiz do projeto):
    python scripts/diagnose_status_categories.py
"""

import os
import sys
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jira_client import fetch_all_issues  # reusa auth, paginação, etc.

FOCUS_STATUS = {"Feito", "Concluído"}
FOCUS_TYPES  = {"Incidente", "História", "GMUD"}


def main() -> None:
    print("Buscando issues no Jira (issuetype, status, resolutiondate, updated)...")
    issues = fetch_all_issues(
        jql="project = TD",
        fields=["issuetype", "status", "resolutiondate", "updated"],
    )
    print(f"  {len(issues)} issues obtidas.\n")

    # ── Agrega por (issuetype, status_name, statusCategory_key) ──────────────
    # Valores: [n_total, n_native_resdate, n_null_updated]
    groups: dict[tuple, list[int]] = collections.defaultdict(lambda: [0, 0, 0])

    for issue in issues:
        f = issue.get("fields", {})
        issuetype   = (f.get("issuetype") or {}).get("name", "?")
        status_obj  = f.get("status") or {}
        status_name = status_obj.get("name", "?")
        cat_key     = (status_obj.get("statusCategory") or {}).get("key", "?")
        cat_name    = (status_obj.get("statusCategory") or {}).get("name", "?")
        has_native  = f.get("resolutiondate") is not None
        has_updated = f.get("updated") is not None

        key = (issuetype, status_name, cat_key, cat_name)
        groups[key][0] += 1
        if has_native:
            groups[key][1] += 1
        if not has_updated:
            groups[key][2] += 1

    # ── Tabela completa ───────────────────────────────────────────────────────
    cols = ["ISSUETYPE", "STATUS", "CAT_KEY", "CAT_NAME", "TOTAL", "NATIVE_RESDATE", "NULL_UPDATED"]
    widths = [14, 12, 12, 14, 7, 14, 12]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)

    print(fmt.format(*cols))
    print("  ".join("-" * w for w in widths))

    for (issuetype, status, cat_key, cat_name), (n, n_nat, n_no_upd) in sorted(groups.items()):
        print(fmt.format(issuetype, status, cat_key, cat_name, n, n_nat, n_no_upd))

    # ── Foco: Feito por issuetype ─────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("FOCO: STATUS 'Feito' — comparação entre issuetypes")
    print("=" * 72)

    feito_groups = {
        (it, st, ck, cn): vals
        for (it, st, ck, cn), vals in groups.items()
        if st == "Feito"
    }

    if not feito_groups:
        print("  Nenhuma issue com status 'Feito' encontrada.")
    else:
        for (issuetype, status, cat_key, cat_name), (n, n_nat, n_no_upd) in sorted(feito_groups.items()):
            fallback_fires = cat_key == "done"
            effective_resdate = n_nat + (n - n_nat if fallback_fires else 0) - n_no_upd
            print(f"\n  {issuetype} / Feito")
            print(f"    statusCategory.key  = '{cat_key}'  ({cat_name})")
            print(f"    total               = {n}")
            print(f"    native resolutiondate (Jira) = {n_nat}")
            print(f"    null updated (sem fallback possível) = {n_no_upd}")
            if fallback_fires:
                print(f"    fallback dispara? SIM (cat == 'done')")
                print(f"    → resolutiondate esperado após fallback: ~{n - n_no_upd} issues")
            else:
                print(f"    fallback dispara? NÃO (cat == '{cat_key}', não 'done')")
                print(f"    → {n - n_nat} issues ficam sem resolutiondate → is_resolved=False")

    # ── Foco: Concluído ───────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("FOCO: STATUS 'Concluído'")
    print("=" * 72)

    conc_groups = {
        (it, st, ck, cn): vals
        for (it, st, ck, cn), vals in groups.items()
        if st == "Concluído"
    }

    if not conc_groups:
        print("  Nenhuma issue com status 'Concluído' encontrada.")
    else:
        for (issuetype, status, cat_key, cat_name), (n, n_nat, n_no_upd) in sorted(conc_groups.items()):
            fallback_fires = cat_key == "done"
            print(f"\n  {issuetype} / Concluído")
            print(f"    statusCategory.key = '{cat_key}'  ({cat_name})")
            print(f"    total={n}, native_resdate={n_nat}, null_updated={n_no_upd}")
            print(f"    fallback dispara? {'SIM' if fallback_fires else f'NÃO (cat={cat_key!r})'}")

    # ── Diagnóstico final ─────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("DIAGNÓSTICO")
    print("=" * 72)

    incidente_feito = next(
        ((ck, n, n_nat) for (it, st, ck, _), (n, n_nat, _) in groups.items()
         if it == "Incidente" and st == "Feito"),
        None,
    )
    historia_feito = next(
        ((ck, n, n_nat) for (it, st, ck, _), (n, n_nat, _) in groups.items()
         if it == "História" and st == "Feito"),
        None,
    )

    if historia_feito and incidente_feito:
        ck_h, n_h, nat_h = historia_feito
        ck_i, n_i, nat_i = incidente_feito
        if ck_h == ck_i:
            print(f"\n  Ambos têm statusCategory.key='{ck_h}' — o workflow É o mesmo.")
            print(f"  História/Feito: {nat_h}/{n_h} com native resdate.")
            print(f"  Incidente/Feito: {nat_i}/{n_i} com native resdate.")
            print("  → Problema provavelmente não é o statusCategory.")
            print("  → Verificar se Incidentes têm 'updated' preenchido ou outro campo para fallback.")
        else:
            print(f"\n  WORKFLOW DIFERENTE CONFIRMADO:")
            print(f"  História / Feito  → statusCategory.key = '{ck_h}'")
            print(f"  Incidente / Feito → statusCategory.key = '{ck_i}'")
            if ck_i != "done":
                print(f"  → Fallback NÃO dispara para Incidente/Feito.")
                print(f"  → Correção: ampliar fallback para incluir cat_key='{ck_i}',")
                print(f"    OU mapear resolutiondate via transição para status '{ck_i}'.")
    else:
        print("  Dados insuficientes para comparação direta.")


if __name__ == "__main__":
    main()
