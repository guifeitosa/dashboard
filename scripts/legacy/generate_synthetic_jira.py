import csv
import random
from datetime import datetime, timedelta
import pandas as pd

random.seed(42)

TEAMS = ["Time Alfa", "Time Beta", "Time Gama"]
ISSUE_TYPES = ["História", "Incidente", "GMUD"]
STATUSES = ["A Fazer", "Fazendo", "Em Análise", "Feito"]
START_DATE = datetime.now() - timedelta(days=180)
END_DATE = datetime.now()
MONTH_STARTS = []
current = START_DATE.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
while current <= END_DATE:
    MONTH_STARTS.append(current)
    if current.month == 12:
        current = current.replace(year=current.year + 1, month=1)
    else:
        current = current.replace(month=current.month + 1)

KEY_COUNTER = 100
records = []

SUMMARY_TEMPLATES = [
    "Implementar nova funcionalidade {}",
    "Corrigir erro de regressão {}",
    "Aprimorar componente {}",
    "Validar fluxo de {}",
    "Preparar deploy {}",
    "Analisar incidente {}",
]


def random_timestamp(start, end):
    delta = end - start
    seconds = random.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=seconds)


def month_end(dt):
    if dt.month == 12:
        next_month = dt.replace(year=dt.year + 1, month=1, day=1)
    else:
        next_month = dt.replace(month=dt.month + 1, day=1)
    return next_month - timedelta(seconds=1)


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def format_datetime(dt: datetime):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def format_date(dt: datetime):
    return dt.strftime("%Y-%m-%dT00:00:00")

for idx, month_start in enumerate(MONTH_STARTS):
    end = month_end(month_start)
    base_total = 70 + int((len(MONTH_STARTS) - idx) * 3) + random.randint(-6, 6)
    month_modifier = 1 + (0.2 * random.uniform(-1, 1))
    total_issues = clamp(int(base_total * month_modifier), 65, 98)

    gmud_count = clamp(int(total_issues * random.uniform(0.10, 0.16)), 5, 16)
    incident_count = clamp(int(total_issues * random.uniform(0.15, 0.22)), 8, 22)
    historia_count = total_issues - gmud_count - incident_count
    if historia_count < 0:
        historia_count = max(0, total_issues - gmud_count - incident_count)

    counts = {
        "GMUD": gmud_count,
        "Incidente": incident_count,
        "História": historia_count,
    }

    for issuetype, count in counts.items():
        for _ in range(count):
            team = random.choice(TEAMS)
            created = random_timestamp(month_start, end)
            status = "Feito"
            resolved = None
            data_implantacao = ""

            if month_start == MONTH_STARTS[-1]:
                if random.random() < 0.28:
                    status = random.choice(STATUSES[:-1])
            else:
                if random.random() < 0.06:
                    status = random.choice(STATUSES[:-1])

            if status == "Feito":
                if issuetype == "Incidente":
                    hours = clamp(int(random.gauss(16, 10)), 1, 120)
                    resolved = created + timedelta(hours=hours)
                elif issuetype == "GMUD":
                    days = clamp(int(random.gauss(8, 4)), 1, 22)
                    resolved = created + timedelta(days=days, hours=random.randint(0, 23), minutes=random.randint(0, 59))
                    implant_delay = random.randint(0, 4)
                    data_implantacao = format_date(resolved + timedelta(days=implant_delay))
                else:
                    days = clamp(int(random.gauss(7, 3)), 1, 18)
                    resolved = created + timedelta(days=days, hours=random.randint(0, 23), minutes=random.randint(0, 59))

            summary = random.choice(SUMMARY_TEMPLATES).format(KEY_COUNTER)
            KEY_COUNTER += 1

            records.append({
                "Summary": summary,
                "Issue Type": issuetype,
                "Status": status,
                "Team": team,
                "Created": format_datetime(created),
                "Resolved": format_datetime(resolved) if resolved else "",
                "Data de Implantacao": data_implantacao,
            })

random.shuffle(records)

with open("data/jira_import.csv", mode="w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=["Summary", "Issue Type", "Status", "Team", "Created", "Resolved", "Data de Implantacao"],
    )
    writer.writeheader()
    writer.writerows(records)

print(f"Generated {len(records)} synthetic Jira import records to data/jira_import.csv")

df = pd.read_csv("data/jira_import.csv", encoding="utf-8")
print(df.head())
