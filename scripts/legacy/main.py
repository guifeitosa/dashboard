import pandas as pd

from jira_client import load_issues_as_dataframe
from metrics import calculate_metrics_summary


def print_metrics_summary(summary):
    if summary.empty:
        print("\nNão há métricas calculadas. Verifique se as issues retornadas possuem dates/resolutiondate válidos.")
        return

    display_df = summary.copy()
    display_df["cfr_percent"] = display_df["cfr_percent"].map(lambda x: f"{x:.1f}%" if pd.notna(x) else "N/A")
    display_df["mttr_hours"] = display_df["mttr_hours"].map(lambda x: f"{x:.1f}" if pd.notna(x) else "N/A")
    display_df["lead_time_days"] = display_df["lead_time_days"].map(lambda x: f"{x:.1f}" if pd.notna(x) else "N/A")

    print("\nResumo de métricas por time e mês:\n")
    print(display_df.to_string(index=False))


if __name__ == "__main__":
    df = load_issues_as_dataframe()
    print(f"Carregadas {len(df)} issues do Jira")
    print("Status counts:", df["status"].value_counts(dropna=False).to_dict())
    print("Tipos de issue:", df["issuetype"].value_counts(dropna=False).to_dict())
    print("Times disponíveis:", df["team"].value_counts(dropna=False).to_dict())

    summary = calculate_metrics_summary(df)
    print_metrics_summary(summary)
