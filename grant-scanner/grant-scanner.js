#!/usr/bin/env node

/**
 * School of Recycling — Grant Scanner
 *
 * Scans public grant databases for funding opportunities:
 *   - EU Funding & Tenders Portal (for Stichting / EU grants)
 *   - Grants.gov (for LLC / worldwide grants)
 *
 * Filters by sustainability + education keywords relevant to School of Recycling.
 * Writes results to Google Sheets (tab: "Grants") and ./grants-found.json.
 *
 * Usage:
 *   node grant-scanner.js           — scan and preview
 *   node grant-scanner.js --write   — scan and write to Google Sheet
 */

import { writeFileSync, existsSync, readFileSync } from 'fs';
import { google } from 'googleapis';

const DRY_RUN = !process.argv.includes('--write');
const SPREADSHEET_ID = '1QTCF2nddHm87mDYiRtLBYQKD6j6C1DPC22h2CLENQ1E';
const GRANTS_SHEET = 'Grants';
const OUTPUT_FILE = './grants-found.json';

// School of Recycling profile for relevance matching
// Stichting: Dutch nonprofit, ANBI-eligible, SDG 4/12/13/14, digital recycling education for schools
// LLC: commercial arm, worldwide, EdTech / sustainability education
const KEYWORDS = [
  'recycling', 'circular economy', 'plastic', 'waste', 'pollution',
  'sustainability education', 'environmental education', 'climate education',
  'education', 'school', 'youth', 'children', 'digital learning',
  'SDG 4', 'SDG 12', 'SDG 13', 'SDG 14', 'edtech',
  'material', 'upstream', 'lifecycle', 'packaging',
];

// ─── Auth ────────────────────────────────────────────────────────────────────

async function getAuth() {
  const auth = new google.auth.GoogleAuth({
    scopes: ['https://www.googleapis.com/auth/spreadsheets'],
  });
  return auth.getClient();
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function today() {
  return new Date().toISOString().split('T')[0];
}

function isRelevant(text = '') {
  const lower = text.toLowerCase();
  return KEYWORDS.some(kw => lower.includes(kw));
}

function loadExisting() {
  if (existsSync(OUTPUT_FILE)) return JSON.parse(readFileSync(OUTPUT_FILE, 'utf8'));
  return [];
}

function dedupe(grants) {
  const seen = new Set();
  return grants.filter(g => {
    const key = g.id || g.title;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

// ─── EU Funding & Tenders Portal ─────────────────────────────────────────────

async function fetchEUGrants() {
  console.log('\n[EU] Querying EU Funding & Tenders Portal...');
  const results = [];

  const queries = [
    'recycling education children schools',
    'plastic pollution education youth',
    'circular economy digital learning',
    'waste management school curriculum',
    'environmental education SDG',
  ];

  for (const q of queries) {
    const url = `https://api.tech.ec.europa.eu/search-api/prod/rest/search?` +
      new URLSearchParams({
        apiKey: 'SEDIA',
        text: q,
        pageSize: '50',
        pageNumber: '1',
        language: 'en',
      });

    try {
      const res = await fetch(url, {
        headers: { 'Accept': 'application/json' },
        signal: AbortSignal.timeout(15000),
      });
      if (!res.ok) {
        console.warn(`  [EU] HTTP ${res.status} for query "${q}"`);
        continue;
      }
      const data = await res.json();
      const items = data?.results || [];
      console.log(`  [EU] "${q}" → ${items.length} raw results`);

      for (const item of items) {
        const title = item.title || item.metadata?.title?.[0] || '';
        const description = item.summary || item.metadata?.description?.[0] || '';
        const deadline = item.metadata?.deadlineDate?.[0] || item.metadata?.closingDate?.[0] || '';
        const link = item.url || `https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-details/${item.metadata?.identifier?.[0] || ''}`;
        const id = item.metadata?.identifier?.[0] || item.id || title;

        if (!isRelevant(title + ' ' + description)) continue;

        results.push({
          id,
          source: 'EU Funding & Tenders',
          entity: 'Stichting',
          geography: 'EU',
          title: title.trim(),
          description: description.slice(0, 300).trim(),
          deadline,
          link,
          foundDate: today(),
        });
      }
    } catch (e) {
      console.warn(`  [EU] Error fetching "${q}": ${e.message}`);
    }
  }

  console.log(`  [EU] ${results.length} relevant results after filtering`);
  return results;
}

// ─── Grants.gov (US / worldwide, open to LLC) ─────────────────────────────────

async function fetchGrantsGov() {
  console.log('\n[Grants.gov] Querying Grants.gov (worldwide / LLC eligible)...');
  const results = [];

  // Grants.gov v2 search API — no key required
  const url = 'https://apply07.grants.gov/grantsws/rest/opportunities/search/';
  const queries = [
    { keyword: 'recycling education youth schools', rows: 25 },
    { keyword: 'plastic pollution environmental education', rows: 25 },
    { keyword: 'circular economy digital curriculum', rows: 25 },
    { keyword: 'waste systems sustainability education', rows: 25 },
  ];

  for (const q of queries) {
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({
          keyword: q.keyword,
          oppStatuses: 'forecasted|posted',
          rows: q.rows,
          startRecordNum: 0,
          sortBy: 'openDate|desc',
        }),
        signal: AbortSignal.timeout(15000),
      });

      if (!res.ok) {
        console.warn(`  [Grants.gov] HTTP ${res.status} for keyword "${q.keyword}"`);
        continue;
      }

      const data = await res.json();
      const items = data?.oppHits || [];
      console.log(`  [Grants.gov] "${q.keyword}" → ${items.length} raw results`);

      for (const item of items) {
        const title = item.title || '';
        const description = item.synopsis || item.description || '';
        const deadline = item.closeDate || item.responseDueDate || '';
        const id = item.id || item.number || title;
        const link = `https://www.grants.gov/search-grants?keywords=${encodeURIComponent(item.number || title)}`;

        if (!isRelevant(title + ' ' + description)) continue;

        results.push({
          id: String(id),
          source: 'Grants.gov',
          entity: 'LLC',
          geography: 'Worldwide (US-origin)',
          title: title.trim(),
          description: description.slice(0, 300).trim(),
          deadline,
          link,
          agency: item.agencyName || item.agency || '',
          foundDate: today(),
        });
      }
    } catch (e) {
      console.warn(`  [Grants.gov] Error: ${e.message}`);
    }
  }

  console.log(`  [Grants.gov] ${results.length} relevant results after filtering`);
  return results;
}

// ─── GrantStation / Instrumentl fallback (RSS) ───────────────────────────────

async function fetchEUPortalRSS() {
  // Additional EU source: Horizon Europe open calls RSS
  console.log('\n[EU RSS] Checking Horizon Europe open calls...');
  const results = [];

  const feedUrl = 'https://ec.europa.eu/info/funding-tenders/opportunities/data/topicDetails/topicSearchTableResults.json?isOnlyTopic=false&status=31094502,31094501&callStatus=31094501,31094502,31094503&type=1,2,8&sortBy=startDate&orderBy=desc&onlyFavourite=false&topicListKey=topicSearchTablePageState&isArchived=false';

  try {
    const res = await fetch(feedUrl, {
      headers: { 'Accept': 'application/json' },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) {
      console.warn(`  [EU RSS] HTTP ${res.status}`);
      return results;
    }
    const data = await res.json();
    const topics = data?.TopicResults?.Topics || [];
    console.log(`  [EU RSS] ${topics.length} open Horizon calls found`);

    for (const topic of topics) {
      const title = topic.TopicTitle || '';
      const description = topic.TopicObjective || topic.TopicAbstract || '';
      const deadline = topic.deadlineDates?.[0]?.deadlineDate || '';
      const id = topic.TopicID || topic.Identifier || title;
      const link = `https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-details/${topic.Identifier || ''}`;

      if (!isRelevant(title + ' ' + description)) continue;

      results.push({
        id: String(id),
        source: 'Horizon Europe',
        entity: 'Stichting',
        geography: 'EU',
        title: title.trim(),
        description: description.slice(0, 300).trim(),
        deadline,
        link,
        foundDate: today(),
      });
    }
  } catch (e) {
    console.warn(`  [EU RSS] Error: ${e.message}`);
  }

  console.log(`  [EU RSS] ${results.length} relevant Horizon results`);
  return results;
}

// ─── Google Sheets writer ─────────────────────────────────────────────────────

async function ensureGrantsSheet(sheets) {
  const meta = await sheets.spreadsheets.get({ spreadsheetId: SPREADSHEET_ID });
  const existing = meta.data.sheets.map(s => s.properties.title);
  if (existing.includes(GRANTS_SHEET)) return;

  console.log(`  Creating "${GRANTS_SHEET}" tab in spreadsheet...`);
  await sheets.spreadsheets.batchUpdate({
    spreadsheetId: SPREADSHEET_ID,
    requestBody: {
      requests: [{ addSheet: { properties: { title: GRANTS_SHEET } } }],
    },
  });
}

async function writeToSheet(sheets, grants) {
  await ensureGrantsSheet(sheets);

  const header = ['Found Date', 'Entity', 'Geography', 'Source', 'Title', 'Description', 'Deadline', 'Agency', 'Link', 'ID'];
  const rows = grants.map(g => [
    g.foundDate,
    g.entity,
    g.geography,
    g.source,
    g.title,
    g.description,
    g.deadline,
    g.agency || '',
    g.link,
    g.id,
  ]);

  // Clear and rewrite the sheet
  await sheets.spreadsheets.values.clear({
    spreadsheetId: SPREADSHEET_ID,
    range: GRANTS_SHEET,
  });

  await sheets.spreadsheets.values.update({
    spreadsheetId: SPREADSHEET_ID,
    range: `${GRANTS_SHEET}!A1`,
    valueInputOption: 'RAW',
    requestBody: { values: [header, ...rows] },
  });

  console.log(`  Written ${rows.length} grants to "${GRANTS_SHEET}" sheet`);
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  console.log('\n=== School of Recycling — Grant Scanner ===');
  console.log(`Mode: ${DRY_RUN ? 'PREVIEW (add --write to update sheet)' : 'WRITE'}`);
  console.log(`Segment: sustainability + plastic education`);
  console.log(`Entities: LLC (worldwide) | Stichting (EU — ANBI-eligible)`);
  console.log(`Focus:    Recycling/waste education, SDG 4/12/13/14, digital EdTech for schools\n`);

  const [euGrants, grantsGov, horizonGrants] = await Promise.all([
    fetchEUGrants(),
    fetchGrantsGov(),
    fetchEUPortalRSS(),
  ]);

  const allGrants = dedupe([...euGrants, ...horizonGrants, ...grantsGov]);

  const llcGrants = allGrants.filter(g => g.entity === 'LLC');
  const stichtingGrants = allGrants.filter(g => g.entity === 'Stichting');

  console.log(`\n=== Summary ===`);
  console.log(`LLC (worldwide):   ${llcGrants.length} grants found`);
  console.log(`Stichting (EU):    ${stichtingGrants.length} grants found`);
  console.log(`Total unique:      ${allGrants.length}`);

  if (allGrants.length > 0) {
    console.log('\n--- LLC / Worldwide ---');
    for (const g of llcGrants) {
      console.log(`  [${g.source}] ${g.title}`);
      if (g.deadline) console.log(`    Deadline: ${g.deadline}`);
      console.log(`    ${g.link}`);
    }

    console.log('\n--- Stichting / EU ---');
    for (const g of stichtingGrants) {
      console.log(`  [${g.source}] ${g.title}`);
      if (g.deadline) console.log(`    Deadline: ${g.deadline}`);
      console.log(`    ${g.link}`);
    }
  }

  // Always save to JSON
  const existing = loadExisting();
  const existingIds = new Set(existing.map(g => g.id));
  const newGrants = allGrants.filter(g => !existingIds.has(g.id));
  const merged = dedupe([...existing, ...allGrants]);
  writeFileSync(OUTPUT_FILE, JSON.stringify(merged, null, 2));
  console.log(`\nSaved ${merged.length} total grants (${newGrants.length} new) to ${OUTPUT_FILE}`);

  if (!DRY_RUN) {
    const auth = await getAuth();
    const sheets = google.sheets({ version: 'v4', auth });
    await writeToSheet(sheets, merged);
  } else {
    console.log('\n[DRY RUN] Sheet not updated. Run with --write to write to Google Sheet.');
  }
}

main().catch(err => { console.error(err); process.exit(1); });
