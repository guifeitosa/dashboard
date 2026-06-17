import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()
url = os.getenv("JIRA_BASE_URL")
email = os.getenv("JIRA_EMAIL")
token = os.getenv("JIRA_API_TOKEN")
headers = {
    "Authorization": "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode(),
    "Accept": "application/json",
}
params = {
    "jql": "project = TD",
    "fields": "issuetype,status,created,resolutiondate,updated,customfield_10001,customfield_10042",
    "maxResults": 1,
}
resp = requests.get(url + "/rest/api/3/search/jql", headers=headers, params=params)
print(resp.status_code, resp.reason)
print(resp.url)
print(resp.text[:2000])
