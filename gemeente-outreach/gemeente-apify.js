#!/usr/bin/env node

/**
 * Gemeente Duurzaamheid Contact Scraper (Apify)
 *
 * For each gemeente, builds targeted URLs (duurzaamheid, college, bestuur pages)
 * and uses Apify to scrape them for named contacts + direct email addresses.
 * Writes results to the "Duurzame Contacten" sheet tab.
 *
 * Prerequisites:
 *   1. Run gemeente-import.js --write first (populates website URLs)
 *   2. Set APIFY_TOKEN env var to your personal API token
 *
 * Usage:
 *   APIFY_TOKEN=your_token node gemeente-apify.js          — dry run (show URLs, no Apify call)
 *   APIFY_TOKEN=your_token node gemeente-apify.js --run    — trigger Apify + write to sheet
 *   APIFY_TOKEN=your_token node gemeente-apify.js --top    — only scrape top-50 sustainability gemeenten
 */

import { get as httpsGet, request as httpsRequest } from 'https';
import { google } from 'googleapis';

const SPREADSHEET_ID = '1QTCF2nddHm87mDYiRtLBYQKD6j6C1DPC22h2CLENQ1E';
const GEMEENTEN_SHEET = 'Gemeenten';
const OUTPUT_SHEET = 'Duurzame Contacten';
const DRY_RUN = !process.argv.includes('--run');
const TOP_ONLY = process.argv.includes('--top');

const APIFY_TOKEN = process.env.APIFY_TOKEN;
// Website Content Crawler — renders JS, follows links, extracts full page text
const APIFY_ACTOR = 'apify/website-content-crawler';

const OUTPUT_HEADERS = ['Gemeente', 'Naam', 'Functie', 'Email', 'Bron URL'];

// URL paths to try per gemeente — ordered by most likely to have named contacts
const TARGET_PATHS = [
  '/duurzaamheid',
  '/klimaat',
  '/milieu',
  '/bestuur-en-organisatie/college-van-bw',
  '/bestuur/college',
  '/over-de-gemeente/college-van-burgemeester-en-wethouders',
  '/college',
  '/contact/duurzaamheid',
  '/themas/duurzaamheid',
];

// Top-50 sustainability-active gemeenten (for --top mode)
const TOP_GEMEENTEN = new Set([
  'Amsterdam', 'Rotterdam', 'Utrecht', 'Den Haag', 'Eindhoven',
  'Groningen', 'Tilburg', 'Almere', 'Breda', 'Nijmegen',
  'Enschede', 'Haarlem', 'Arnhem', 'Zaanstad', 'Amersfoort',
  'Apeldoorn', 'Zwolle', 'Leiden', 'Maastricht', 'Dordrecht',
  'Zoetermeer', 'Delft', 'Ede', 'Deventer', 'Emmen',
  'Westland', 'Alkmaar', 'Leeuwarden', 'Venlo', 'Helmond',
  'Wageningen', 'Zeewolde', 'Dalfsen', 'Tynaarlo', 'Blaricum',
  'Rozendaal', 'Zoeterwoude', 'Putten', 'Rijssen-Holten',
  'Haarlemmermeer', 'Kaag en Braassem', 'Alphen aan den Rijn', 'Gouda',
  'Súdwest-Fryslân', 'Midden-Groningen', 'Smallingerland',
  'Opsterland', 'Waadhoeke', 'Dantumadiel', 'Nissewaard',
]);

// Regex patterns to extract named contacts from scraped page text
const EMAIL_RE = /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g;
const NAME_RE = /(?:wethouder|adviseur|coordinator|coördinator|beleidsmedewerker|projectleider|ambtenaar|manager|directeur)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})/gi;

// Only keep direct personal emails — skip generic ones
const SKIP_EMAIL_RE = /^(info|contact|gemeente|post|administratie|communicatie|receptie|noreply|no-reply)@/i;

function isDuurzaamEmail(email) {
  return /duurzaam|milieu|klimaat|energie|groen|recycl|circulair/i.test(email);
}

function apifyPost(path, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const req = httpsRequest({
      hostname: 'api.apify.com',
      path,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(data),
        'Authorization': `Bearer ${APIFY_TOKEN}`,
      },
    }, (res) => {
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => {
        try { resolve(JSON.parse(Buffer.concat(chunks).toString())); }
        catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

function apifyGet(path) {
  return new Promise((resolve, reject) => {
    httpsGet({
      hostname: 'api.apify.com',
      path,
      headers: { 'Authorization': `Bearer ${APIFY_TOKEN}` },
    }, (res) => {
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => {
        try { resolve(JSON.parse(Buffer.concat(chunks).toString())); }
        catch (e) { reject(e); }
      });
    }).on('error', reject);
  });
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function pollRun(runId) {
  console.log(`Polling run ${runId}...`);
  while (true) {
    const res = await apifyGet(`/v2/actor-runs/${runId}?token=${APIFY_TOKEN}`);
    const status = res.data?.status;
    process.stdout.write(`\r  Status: ${status}        `);
    if (status === 'SUCCEEDED') { console.log(''); return res.data.defaultDatasetId; }
    if (['FAILED', 'ABORTED', 'TIMED-OUT'].includes(status)) throw new Error(`Apify run ${status}`);
    await sleep(20000);
  }
}

async function fetchDataset(datasetId) {
  // Fetch in batches of 1000
  const items = [];
  let offset = 0;
  while (true) {
    const res = await apifyGet(
      `/v2/datasets/${datasetId}/items?token=${APIFY_TOKEN}&format=json&clean=true&limit=1000&offset=${offset}`
    );
    const batch = Array.isArray(res) ? res : (res.data?.items || []);
    items.push(...batch);
    if (batch.length < 1000) break;
    offset += 1000;
  }
  return items;
}

async function getAuth() {
  const auth = new google.auth.GoogleAuth({
    scopes: ['https://www.googleapis.com/auth/spreadsheets'],
  });
  return auth.getClient();
}

async function readGemeentenSheet(sheets) {
  const res = await sheets.spreadsheets.values.get({
    spreadsheetId: SPREADSHEET_ID,
    range: `${GEMEENTEN_SHEET}!A1:E`,
  });
  return res.data.values || [];
}

async function ensureSheet(sheets, name) {
  const meta = await sheets.spreadsheets.get({ spreadsheetId: SPREADSHEET_ID });
  const exists = meta.data.sheets.some(s => s.properties.title === name);
  if (!exists) {
    await sheets.spreadsheets.batchUpdate({
      spreadsheetId: SPREADSHEET_ID,
      requestBody: { requests: [{ addSheet: { properties: { title: name } } }] },
    });
    console.log(`Created sheet tab: ${name}`);
  }
}

async function writeToSheet(sheets, rows) {
  await ensureSheet(sheets, OUTPUT_SHEET);
  const values = [OUTPUT_HEADERS, ...rows];
  await sheets.spreadsheets.values.update({
    spreadsheetId: SPREADSHEET_ID,
    range: `${OUTPUT_SHEET}!A1`,
    valueInputOption: 'RAW',
    requestBody: { values },
  });
  console.log(`Wrote ${rows.length} contacts to "${OUTPUT_SHEET}" tab.`);
}

/**
 * Extract direct personal emails from scraped page text.
 * Returns emails that look personal (firstname.lastname@ or duurzaamheid-role@).
 */
function extractPersonalEmails(text, domain) {
  if (!text) return [];
  const all = [...new Set(text.match(EMAIL_RE) || [])];
  return all.filter(e => {
    // Must be on this gemeente's domain
    if (!e.toLowerCase().includes(domain.replace(/^www\./, ''))) return false;
    // Skip generic addresses
    if (SKIP_EMAIL_RE.test(e)) return false;
    return true;
  });
}

async function main() {
  console.log('\n=== Gemeente Duurzaamheid Contact Scraper ===');
  console.log(`Mode: ${DRY_RUN ? 'DRY RUN' : 'LIVE'}${TOP_ONLY ? ' | TOP-50 only' : ' | all 342 gemeenten'}\n`);

  if (!APIFY_TOKEN) {
    console.error('ERROR: APIFY_TOKEN environment variable is not set.');
    process.exit(1);
  }

  const auth = await getAuth();
  const sheets = google.sheets({ version: 'v4', auth });

  console.log('Reading Gemeenten sheet...');
  const rows = await readGemeentenSheet(sheets);
  if (rows.length < 2) {
    console.error('Gemeenten sheet is empty. Run gemeente-import.js --write first.');
    process.exit(1);
  }

  // Build target URL list: multiple paths per gemeente
  const gemeenten = [];
  for (let i = 1; i < rows.length; i++) {
    const naam = rows[i][0] || '';
    const website = rows[i][2] || '';
    if (!website || !naam) continue;

    const cleanNaam = naam.replace(/^gemeente\s+/i, '').trim();
    if (TOP_ONLY && !TOP_GEMEENTEN.has(cleanNaam)) continue;

    let base = website.replace(/\/$/, '');
    if (!base.startsWith('http')) base = `https://${base}`;
    gemeenten.push({ naam: cleanNaam, base });
  }

  // Build one URL per path per gemeente
  const startUrls = [];
  for (const g of gemeenten) {
    for (const path of TARGET_PATHS) {
      startUrls.push({ url: `${g.base}${path}`, userData: { gemeente: g.naam, base: g.base } });
    }
  }

  console.log(`Targeting ${gemeenten.length} gemeenten × ${TARGET_PATHS.length} paths = ${startUrls.length} URLs`);

  if (DRY_RUN) {
    console.log('\nSample URLs (first gemeente):');
    startUrls.slice(0, TARGET_PATHS.length).forEach(u => console.log(`  ${u.url}`));
    console.log(`\nDry run done. Run with --run to trigger Apify.`);
    console.log(`Tip: add --top to only scrape the top-50 sustainability gemeenten first.`);
    return;
  }

  console.log(`\nTriggering Apify actor: ${APIFY_ACTOR}`);

  const runRes = await apifyPost(
    `/v2/acts/${encodeURIComponent(APIFY_ACTOR)}/runs?token=${APIFY_TOKEN}`,
    {
      startUrls: startUrls.map(u => ({ url: u.url })),
      maxCrawlDepth: 0,        // only the exact URLs we provide — no further crawling
      maxCrawlPages: startUrls.length,
      crawlerType: 'playwright:firefox',
      removeElementsCssSelector: 'nav, footer, script, style',
    }
  );

  const runId = runRes.data?.id;
  if (!runId) {
    console.error('Failed to start run:', JSON.stringify(runRes, null, 2));
    process.exit(1);
  }

  console.log(`Run started. ID: ${runId}`);
  const estMinutes = Math.ceil(startUrls.length / 10);
  console.log(`Estimated time: ~${estMinutes} minutes\n`);

  const datasetId = await pollRun(runId);
  console.log(`Run complete. Dataset: ${datasetId}`);

  console.log('Fetching results...');
  const items = await fetchDataset(datasetId);
  console.log(`Got ${items.length} pages scraped.\n`);

  // Build map: gemeente name → array of { email, functie, url }
  const contactMap = {};

  // Build reverse map: base domain → gemeente name
  const domainToGemeente = {};
  for (const g of gemeenten) {
    try {
      const domain = new URL(g.base).hostname;
      domainToGemeente[domain] = g.naam;
    } catch {}
  }

  for (const item of items) {
    const pageUrl = item.url || '';
    const text = item.text || item.markdown || '';
    if (!text) continue;

    let domain = '';
    try { domain = new URL(pageUrl).hostname; } catch { continue; }

    const gemeente = domainToGemeente[domain];
    if (!gemeente) continue;

    const emails = extractPersonalEmails(text, domain);
    if (emails.length === 0) continue;

    if (!contactMap[gemeente]) contactMap[gemeente] = [];
    for (const email of emails) {
      // Try to extract a name near this email in the text
      const emailIdx = text.indexOf(email);
      const context = text.slice(Math.max(0, emailIdx - 200), emailIdx + 100);
      const nameMatch = context.match(/([A-Z][a-z]+(?:\s+(?:van|de|den|der|van\sde|van\sden)?\s*[A-Z][a-z]+){1,3})/);
      const naam = nameMatch ? nameMatch[1].trim() : '';

      // Try to find a role near the email
      const roleMatch = context.match(/(wethouder|adviseur|co[oö]rdinator|beleidsmedewerker|projectleider|manager|hoofd)[^\n,]{0,60}/i);
      const functie = roleMatch ? roleMatch[0].trim() : (isDuurzaamEmail(email) ? 'duurzaamheid contact' : '');

      contactMap[gemeente].push({ naam, functie, email, url: pageUrl });
    }
  }

  // Build output rows — deduplicate by email
  const outputRows = [];
  const seenEmails = new Set();

  for (const gemeente of gemeenten.map(g => g.naam)) {
    const contacts = contactMap[gemeente] || [];
    // Prefer contacts with duurzaam in email, then those with a name
    contacts.sort((a, b) => {
      if (isDuurzaamEmail(a.email) && !isDuurzaamEmail(b.email)) return -1;
      if (!isDuurzaamEmail(a.email) && isDuurzaamEmail(b.email)) return 1;
      if (a.naam && !b.naam) return -1;
      if (!a.naam && b.naam) return 1;
      return 0;
    });
    for (const c of contacts) {
      if (seenEmails.has(c.email)) continue;
      seenEmails.add(c.email);
      outputRows.push([gemeente, c.naam, c.functie, c.email, c.url]);
    }
  }

  console.log(`Extracted ${outputRows.length} personal contacts across ${Object.keys(contactMap).length} gemeenten.\n`);

  if (outputRows.length === 0) {
    console.log('No personal emails found. The pages may not have publicly listed direct contacts.');
    console.log('Consider trying LinkedIn Sales Navigator for manual outreach.');
    return;
  }

  await writeToSheet(sheets, outputRows);
  console.log(`\nDone. Check the "${OUTPUT_SHEET}" tab in your Google Sheet.`);
}

main().catch(err => { console.error(err); process.exit(1); });
