from jira_client import get_custom_field_ids, fetch_all_issues

if __name__ == '__main__':
    custom_fields = get_custom_field_ids(['Team', 'Data de Implantação'])
    print('custom_fields', custom_fields)
    issues = fetch_all_issues('project = TD', ['issuetype', 'status', 'created', 'resolutiondate'] + list(custom_fields.values()))
    print('issues count', len(issues))
    if issues:
        issue = issues[0]
        print('issue keys', list(issue.keys()))
        print('fields keys', list(issue['fields'].keys()))
        print('sample fields')
        for k, v in issue['fields'].items():
            if k in ['issuetype', 'status', 'created', 'resolutiondate', 'updated'] or k in custom_fields.values():
                print(k, v)
        print('full issue first 1k', str(issue)[:1000])
