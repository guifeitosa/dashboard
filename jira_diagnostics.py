import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")

if not JIRA_BASE_URL or not JIRA_EMAIL or not JIRA_API_TOKEN:
    raise EnvironmentError("Missing Jira credentials in .env")

headers = {
    "Authorization": "Basic " + base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode()).decode(),
    "Accept": "application/json",
}

print("JIRA_BASE_URL:", JIRA_BASE_URL)
print("Testing /rest/api/3/field")
resp = requests.get(f"{JIRA_BASE_URL}/rest/api/3/field", headers=headers)
print(resp.status_code, resp.reason)
print(resp.text[:2000])

print("Testing /rest/api/3/search")
resp2 = requests.get(f"{JIRA_BASE_URL}/rest/api/3/search", headers=headers, params={"jql": "project = TD", "maxResults": 1})
print(resp2.status_code, resp2.reason)
print(resp2.text[:2000])

print('Looking for Team/Data de Implantação fields...')
for field in resp.json() if False else []:
    pass
