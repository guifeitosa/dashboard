from jira_client import load_issues_as_dataframe
from metrics import calculate_mttr, calculate_cfr, calculate_lead_time_for_changes, calculate_deployment_frequency

df = load_issues_as_dataframe()
print('DF shape', df.shape)
print('Incidente resolved rows', df[(df['issuetype']=='Incidente') & (df['resolutiondate'].notna())].shape)
print('GMUD resolved rows', df[(df['issuetype']=='GMUD') & (df['resolutiondate'].notna())].shape)
print('History-like rows', df[df['issuetype']=='História'].shape)
print('issue type values', df['issuetype'].unique())
print('status values', df['status'].value_counts(dropna=False).to_dict())
print('resolved values', df['resolutiondate'].notna().value_counts().to_dict())
print('TEAM values', df['team'].unique())
print('CFR custom field non-null', df['data_implantacao'].notna().sum())

print('\nMTTR:')
print(calculate_mttr(df))
print('\nCFR:')
print(calculate_cfr(df))
print('\nLead Time:')
print(calculate_lead_time_for_changes(df))
print('\nDeployment Frequency:')
print(calculate_deployment_frequency(df))
