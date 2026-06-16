const fs = require('fs');
const path = require('path');

const teams = ['Time Alfa', 'Time Beta', 'Time Gama'];
const issueTypes = ['Story', 'Bug', 'Task', 'Incidente', 'GMUD'];
const now = new Date();
const start = new Date(now.getTime() - 180 * 24 * 60 * 60 * 1000);
const monthStarts = [];
let current = new Date(start.getFullYear(), start.getMonth(), 1, 0, 0, 0, 0);
while (current <= now) {
  monthStarts.push(new Date(current));
  current = new Date(current.getFullYear(), current.getMonth() + 1, 1);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function randInt(min, max) {
  return min + Math.floor(Math.random() * (max - min + 1));
}

function randTimestamp(from, to) {
  const diff = to.getTime() - from.getTime();
  return new Date(from.getTime() + Math.floor(Math.random() * diff));
}

function isoDateTime(date) {
  return date.toISOString().replace('T', ' ').replace('Z', '');
}

function monthEnd(date) {
  return new Date(date.getFullYear(), date.getMonth() + 1, 0, 23, 59, 59, 999);
}

const records = [];
let keyCounter = 100;

for (let idx = 0; idx < monthStarts.length; idx++) {
  const monthStart = monthStarts[idx];
  const monthEndDate = monthEnd(monthStart);
  const baseTotal = 70 + idx * 4 + randInt(-6, 8);
  const monthModifier = 1 + (Math.sin(idx / monthStarts.length * Math.PI * 2) * 0.1) + (Math.random() - 0.5) * 0.12;
  const totalIssues = clamp(Math.round(baseTotal * monthModifier), 70, 95);

  let gmudCount = clamp(Math.round(totalIssues * (0.1 + Math.random() * 0.06)), 4, 16);
  let incidentCount = clamp(Math.round(totalIssues * (0.13 + gmudCount * 0.003 + Math.random() * 0.06)), 7, 24);
  let remaining = totalIssues - gmudCount - incidentCount;
  if (remaining < 0) {
    incidentCount = Math.max(7, totalIssues - gmudCount);
    remaining = totalIssues - gmudCount - incidentCount;
  }

  const storyCount = clamp(Math.round(remaining * (0.42 + Math.random() * 0.08)), 5, Math.max(5, remaining));
  const bugCount = clamp(Math.round(remaining * (0.24 + Math.random() * 0.08)), 4, Math.max(4, remaining - storyCount));
  const taskCount = Math.max(0, remaining - storyCount - bugCount);

  const counts = {
    GMUD: gmudCount,
    Incidente: incidentCount,
    Story: storyCount,
    Bug: bugCount,
    Task: taskCount,
  };

  for (const issuetype of issueTypes) {
    const count = counts[issuetype] || 0;
    for (let i = 0; i < count; i++) {
      const team = teams[randInt(0, teams.length - 1)];
      const created = randTimestamp(monthStart, monthEndDate);
      let status = 'resolvido';
      let resolutiondate = '';
      let data_implantacao = '';

      if (issuetype === 'Incidente') {
        if (Math.random() > 0.9) status = 'aberto';
        if (status === 'resolvido') {
          const hours = clamp(Math.round(18 + (Math.random() - 0.5) * 16), 1, 120);
          resolutiondate = new Date(created.getTime() + hours * 3600 * 1000);
        }
      } else if (issuetype === 'GMUD') {
        if (Math.random() > 0.88) status = 'aberto';
        if (status === 'resolvido') {
          const days = clamp(Math.round(7 + (Math.random() - 0.5) * 4), 1, 18);
          resolutiondate = new Date(created.getTime() + days * 24 * 3600 * 1000 + randInt(0, 23) * 3600 * 1000 + randInt(0, 59) * 60000);
          const implantDelay = randInt(0, 4);
          const deployed = new Date(resolutiondate.getTime() + implantDelay * 24 * 3600 * 1000);
          data_implantacao = deployed.toISOString().slice(0, 10);
        }
      } else {
        if (Math.random() > 0.87) status = 'aberto';
        if (status === 'resolvido') {
          const days = clamp(Math.round(5 + (Math.random() - 0.5) * 4), 1, 18);
          resolutiondate = new Date(created.getTime() + days * 24 * 3600 * 1000 + randInt(0, 23) * 3600 * 1000 + randInt(0, 59) * 60000);
        }
      }

      if (status === 'aberto') resolutiondate = '';
      keyCounter += 1;
      records.push({
        key: `PROJ-${keyCounter}`,
        issuetype,
        team,
        status,
        created: isoDateTime(created),
        resolutiondate: resolutiondate ? isoDateTime(resolutiondate) : '',
        data_implantacao,
      });
    }
  }
}

records.sort(() => Math.random() - 0.5);
if (!fs.existsSync('data')) fs.mkdirSync('data');
const header = ['key', 'issuetype', 'team', 'status', 'created', 'resolutiondate', 'data_implantacao'];
const csv = [header.join(',')].concat(records.map((row) => header.map((col) => `"${String(row[col]).replace(/"/g, '""')}"`).join(','))).join('\n');
fs.writeFileSync(path.join('data', 'jira_issues_synthetic.csv'), csv, 'utf8');
console.log(`Generated ${records.length} synthetic Jira issue records to data/jira_issues_synthetic.csv`);
