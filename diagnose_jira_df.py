from jira_client import load_issues_as_dataframe

if __name__ == '__main__':
    df = load_issues_as_dataframe()
    print('shape', df.shape)
    print('columns', df.columns.tolist())
    print('head', df[['key','issuetype','status','team','created','resolutiondate','data_implantacao']].head(10).to_dict('records'))
    print('value_counts issuetype', df['issuetype'].value_counts(dropna=False).to_dict())
    print('value_counts status', df['status'].value_counts(dropna=False).to_dict())
    print('value_counts team', df['team'].value_counts(dropna=False).to_dict())
    print('created nulls', df['created'].isna().sum())
    print('resolution nulls', df['resolutiondate'].isna().sum())
    print('implant nulls', df['data_implantacao'].isna().sum())
    print('year_month unique', sorted(df['year_month'].dropna().unique().tolist()))
    print('any resolved rows', df[df['resolutiondate'].notna()].shape)
    print('any team non-null', df[df['team'].notna()].shape)
