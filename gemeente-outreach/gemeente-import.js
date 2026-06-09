#!/usr/bin/env node

/**
 * Gemeente Contact Importer
 *
 * Downloads the official Dutch municipality list from organisaties.overheid.nl,
 * parses contact fields, fills missing emails from website domain, and writes
 * all rows to a "Gemeenten" sheet tab in the existing spreadsheet.
 *
 * Usage:
 *   node gemeente-import.js          — preview only (dry run)
 *   node gemeente-import.js --write  — write to Google Sheet
 */

import { get as httpGet } from 'https';
import { google } from 'googleapis';

const SPREADSHEET_ID = '1QTCF2nddHm87mDYiRtLBYQKD6j6C1DPC22h2CLENQ1E';
const SHEET_NAME = 'Gemeenten';
const DRY_RUN = !process.argv.includes('--write');

// Official CSV export — updated daily by overheid.nl
const CSV_URL = 'https://organisaties.overheid.nl/export/Gemeenten.csv';

// Output columns written to the sheet
const HEADERS = ['Naam', 'Email', 'Website', 'Contactformulier', 'Email bron'];

async function fetchCSV(url) {
  return new Promise((resolve, reject) => {
    httpGet(url, { headers: { 'User-Agent': 'SchoolOfRecycling-GemeenteImport/1.0' } }, (res) => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        fetchCSV(res.headers.location).then(resolve).catch(reject);
        return;
      }
      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode} fetching ${url}`));
        return;
      }
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
      res.on('error', reject);
    }).on('error', reject);
  });
}

/**
 * Minimal CSV parser that handles quoted fields with embedded commas/newlines.
 * Returns array of objects keyed by header row.
 */
function parseCSV(text) {
  const rows = [];
  let field = '';
  let inQuotes = false;
  let currentRow = [];

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    const next = text[i + 1];

    if (inQuotes) {
      if (ch === '"' && next === '"') { field += '"'; i++; }
      else if (ch === '"') { inQuotes = false; }
      else { field += ch; }
    } else {
      if (ch === '"') { inQuotes = true; }
      else if (ch === ';' || ch === ',') { currentRow.push(field.trim()); field = ''; }
      else if (ch === '\n') {
        currentRow.push(field.trim());
        field = '';
        rows.push(currentRow);
        currentRow = [];
      } else if (ch === '\r') { /* skip */ }
      else { field += ch; }
    }
  }
  if (field || currentRow.length) { currentRow.push(field.trim()); rows.push(currentRow); }

  if (rows.length < 2) return [];
  const headers = rows[0];
  return rows.slice(1).map(row => {
    const obj = {};
    headers.forEach((h, i) => { obj[h] = row[i] || ''; });
    return obj;
  });
}

/**
 * Derive info@ email from website URL.
 * e.g. "https://www.amsterdam.nl" → "info@amsterdam.nl"
 */
function emailFromWebsite(website) {
  if (!website) return '';
  try {
    const url = new URL(website.startsWith('http') ? website : `https://${website}`);
    const domain = url.hostname.replace(/^www\./, '');
    if (!domain || !domain.includes('.')) return '';
    return `info@${domain}`;
  } catch {
    return '';
  }
}

/**
 * Derive info@ from municipality name as last-resort fallback.
 * Handles simple cases: "Gemeente Amsterdam" → "info@amsterdam.nl"
 */
function emailFromName(name) {
  const clean = name
    .replace(/^gemeente\s+/i, '')
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[^a-z0-9\-]/g, '');
  return clean ? `info@${clean}.nl` : '';
}

function findField(row, ...candidates) {
  for (const key of candidates) {
    for (const rowKey of Object.keys(row)) {
      if (rowKey.toLowerCase().includes(key.toLowerCase()) && row[rowKey]) {
        return row[rowKey];
      }
    }
  }
  return '';
}

async function getAuth() {
  const auth = new google.auth.GoogleAuth({
    scopes: ['https://www.googleapis.com/auth/spreadsheets'],
  });
  return auth.getClient();
}

async function ensureSheet(sheets) {
  const meta = await sheets.spreadsheets.get({ spreadsheetId: SPREADSHEET_ID });
  const exists = meta.data.sheets.some(s => s.properties.title === SHEET_NAME);
  if (!exists) {
    await sheets.spreadsheets.batchUpdate({
      spreadsheetId: SPREADSHEET_ID,
      requestBody: {
        requests: [{ addSheet: { properties: { title: SHEET_NAME } } }],
      },
    });
    console.log(`Created sheet tab: ${SHEET_NAME}`);
  } else {
    console.log(`Sheet tab already exists: ${SHEET_NAME}`);
  }
}

async function writeToSheet(sheets, rows) {
  const values = [HEADERS, ...rows];
  await sheets.spreadsheets.values.update({
    spreadsheetId: SPREADSHEET_ID,
    range: `${SHEET_NAME}!A1`,
    valueInputOption: 'RAW',
    requestBody: { values },
  });
  console.log(`Wrote ${rows.length} gemeente rows to sheet.`);
}

async function main() {
  console.log('\n=== Gemeente Contact Importer ===');
  console.log(`Mode: ${DRY_RUN ? 'DRY RUN (add --write to write to sheet)' : 'WRITE'}\n`);

  console.log(`Downloading CSV from overheid.nl...`);
  const csv = await fetchCSV(CSV_URL);
  console.log(`Downloaded ${csv.length} bytes.`);

  const records = parseCSV(csv);
  console.log(`Parsed ${records.length} records.\n`);

  if (records.length === 0) {
    console.error('No records parsed — check CSV format.');
    process.exit(1);
  }

  // Print available columns from the first record for debugging
  console.log('CSV columns detected:', Object.keys(records[0]).join(' | '));
  console.log('');

  const output = [];
  let fromWebsite = 0, fromName = 0, missing = 0;

  for (const rec of records) {
    const naam = findField(rec, 'naam', 'name', 'officiële');
    if (!naam) continue;

    const emailRaw = findField(rec, 'e-mail', 'email', 'mail');
    const website = findField(rec, 'internetpagina', 'website', 'url', 'pagina');
    const contactform = findField(rec, 'contactformulier', 'contact form', 'formulier');

    let email = emailRaw;
    let emailBron = 'CSV';

    if (!email && website) {
      email = emailFromWebsite(website);
      emailBron = 'website-afgeleid';
      fromWebsite++;
    } else if (!email) {
      email = emailFromName(naam);
      emailBron = email ? 'naam-afgeleid' : '';
      if (email) fromName++; else missing++;
    }

    output.push([naam, email, website, contactform, emailBron]);

    if (DRY_RUN && output.length <= 10) {
      console.log(`  ${naam} | ${email} | ${website}`);
    }
  }

  if (DRY_RUN && output.length > 10) {
    console.log(`  ... and ${output.length - 10} more`);
  }

  console.log(`\n=== Summary ===`);
  console.log(`Total gemeenten:       ${output.length}`);
  console.log(`Email from CSV:        ${output.length - fromWebsite - fromName - missing}`);
  console.log(`Email from website:    ${fromWebsite}`);
  console.log(`Email derived (naam):  ${fromName}`);
  console.log(`No email found:        ${missing}`);

  if (!DRY_RUN) {
    const auth = await getAuth();
    const sheets = google.sheets({ version: 'v4', auth });
    await ensureSheet(sheets);
    await writeToSheet(sheets, output);
    console.log(`\nDone. Open your Google Sheet to see the Gemeenten tab.`);
  } else {
    console.log(`\nDry run complete. Run with --write to write ${output.length} rows to Google Sheet.`);
  }
}

main().catch(err => { console.error(err); process.exit(1); });
