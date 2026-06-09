#!/usr/bin/env node

/**
 * Duurzame Gemeente Contacts
 *
 * Downloads the overheid.nl staff CSV, finds named contacts with
 * sustainability-related roles, and writes them to a "Duurzame Contacten"
 * sheet tab — no info@ addresses, only real named contacts.
 *
 * Usage:
 *   node gemeente-duurzaam.js          — dry run (preview)
 *   node gemeente-duurzaam.js --write  — write to Google Sheet
 */

import { get as httpsGet } from 'https';
import { google } from 'googleapis';

const SPREADSHEET_ID = '1QTCF2nddHm87mDYiRtLBYQKD6j6C1DPC22h2CLENQ1E';
const SHEET_NAME = 'Duurzame Contacten';
const DRY_RUN = !process.argv.includes('--write');

// Staff CSV — name + role title per gemeente, updated daily
const MEDEWERKERS_URL = 'https://organisaties.overheid.nl/export-medewerkers/Gemeenten.csv';
// Gemeente CSV — for website/email lookup
const GEMEENTEN_URL = 'https://organisaties.overheid.nl/export/Gemeenten.csv';

const HEADERS = ['Gemeente', 'Naam', 'Functie', 'Email', 'Website'];

// Role keywords that indicate a sustainability contact
const DUURZAAM_KEYWORDS = [
  'duurzaam', 'milieu', 'klimaat', 'energie', 'groen', 'recycl',
  'circulair', 'biodiversiteit', 'natuurlijk', 'omgevingsdienst',
];

// Top ~50 sustainability-active gemeenten (GDI index + G40 active members)
// Used to prioritise — if empty, all matching contacts are included
const TOP_GEMEENTEN = new Set([
  'Amsterdam', 'Rotterdam', 'Utrecht', 'Den Haag', 'Eindhoven',
  'Groningen', 'Tilburg', 'Almere', 'Breda', 'Nijmegen',
  'Enschede', 'Haarlem', 'Arnhem', 'Zaanstad', 'Amersfoort',
  'Apeldoorn', 'Zwolle', 'Leiden', 'Maastricht', 'Dordrecht',
  'Zoetermeer', 'Delft', 'Ede', 'Deventer', 'Emmen',
  'Westland', 'Alkmaar', 'Leeuwarden', 'Venlo', 'Helmond',
  'Wageningen', 'Zeewolde', 'Dalfsen', 'Tynaarlo', 'Blaricum',
  'Rozendaal', 'Zoeterwoude', 'Putten', 'Rijssen-Holten',
  'Súdwest-Fryslân', 'Haarlemmermeer', 'Nissewaard', 'Midden-Groningen',
  'Dantumadiel', 'Waadhoeke', 'Opsterland', 'Smallingerland',
  'Kaag en Braassem', 'Alphen aan den Rijn', 'Gouda',
]);

function fetchCSV(url) {
  return new Promise((resolve, reject) => {
    httpsGet(url, { headers: { 'User-Agent': 'SchoolOfRecycling-GemeenteImport/1.0' } }, (res) => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        fetchCSV(res.headers.location).then(resolve).catch(reject);
        return;
      }
      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode} from ${url}`));
        return;
      }
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
      res.on('error', reject);
    }).on('error', reject);
  });
}

function parseCSV(text) {
  const rows = [];
  let field = '', inQuotes = false, currentRow = [];

  for (let i = 0; i < text.length; i++) {
    const ch = text[i], next = text[i + 1];
    if (inQuotes) {
      if (ch === '"' && next === '"') { field += '"'; i++; }
      else if (ch === '"') { inQuotes = false; }
      else { field += ch; }
    } else {
      if (ch === '"') { inQuotes = true; }
      else if (ch === ';' || ch === ',') { currentRow.push(field.trim()); field = ''; }
      else if (ch === '\n') { currentRow.push(field.trim()); rows.push(currentRow); currentRow = []; field = ''; }
      else if (ch === '\r') { /* skip */ }
      else { field += ch; }
    }
  }
  if (field || currentRow.length) { currentRow.push(field.trim()); rows.push(currentRow); }

  if (rows.length < 2) return [];
  const headers = rows[0];
  return rows.slice(1).map(row => {
    const obj = {};
    headers.forEach((h, i) => { obj[h.trim()] = (row[i] || '').trim(); });
    return obj;
  });
}

function findField(obj, ...keys) {
  for (const key of keys) {
    for (const k of Object.keys(obj)) {
      if (k.toLowerCase().includes(key.toLowerCase()) && obj[k]) return obj[k];
    }
  }
  return '';
}

function isDuurzaamRole(functie) {
  const lower = functie.toLowerCase();
  return DUURZAAM_KEYWORDS.some(kw => lower.includes(kw));
}

function emailFromWebsite(website) {
  try {
    const url = new URL(website.startsWith('http') ? website : `https://${website}`);
    const domain = url.hostname.replace(/^www\./, '');
    return domain.includes('.') ? `info@${domain}` : '';
  } catch { return ''; }
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
      requestBody: { requests: [{ addSheet: { properties: { title: SHEET_NAME } } }] },
    });
    console.log(`Created sheet tab: ${SHEET_NAME}`);
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
  console.log(`Wrote ${rows.length} rows to ${SHEET_NAME} tab.`);
}

async function main() {
  console.log('\n=== Duurzame Gemeente Contact Finder ===');
  console.log(`Mode: ${DRY_RUN ? 'DRY RUN (add --write to write to sheet)' : 'WRITE'}\n`);

  console.log('Downloading staff CSV...');
  const staffCSV = await fetchCSV(MEDEWERKERS_URL);
  console.log(`Downloaded ${staffCSV.length} bytes.`);

  console.log('Downloading gemeente CSV (for websites)...');
  const gemeentenCSV = await fetchCSV(GEMEENTEN_URL);

  const staffRecords = parseCSV(staffCSV);
  const gemeenteRecords = parseCSV(gemeentenCSV);

  console.log(`Staff records: ${staffRecords.length}`);
  console.log(`Gemeente records: ${gemeenteRecords.length}`);
  console.log(`Staff CSV columns: ${Object.keys(staffRecords[0] || {}).join(' | ')}\n`);

  // Build website lookup: gemeente name → website + email
  const gemeenteInfo = {};
  for (const rec of gemeenteRecords) {
    const naam = findField(rec, 'naam', 'officiële');
    const cleanNaam = naam.replace(/^gemeente\s+/i, '').trim();
    const website = findField(rec, 'internetpagina', 'website', 'pagina');
    const email = findField(rec, 'e-mail', 'email');
    if (cleanNaam) gemeenteInfo[cleanNaam.toLowerCase()] = { website, email };
  }

  // Filter staff for sustainability roles
  const contacts = [];
  const seenNames = new Set();

  for (const rec of staffRecords) {
    const orgNaam = findField(rec, 'organisatie');
    const naam = findField(rec, 'naam');
    const functie = findField(rec, 'functie');

    if (!naam || !functie) continue;
    if (!isDuurzaamRole(functie)) continue;

    // Normalise gemeente name (strip "Gemeente " prefix)
    const gemeenteNaam = orgNaam.replace(/^gemeente\s+/i, '').trim();
    const key = `${gemeenteNaam.toLowerCase()}|${naam.toLowerCase()}`;
    if (seenNames.has(key)) continue;
    seenNames.add(key);

    // Look up website/email
    const info = gemeenteInfo[gemeenteNaam.toLowerCase()] || {};
    const website = info.website || '';
    let email = info.email || '';
    if (!email) continue; // skip contacts without a real email address

    const isTopGemeente = TOP_GEMEENTEN.has(gemeenteNaam);

    contacts.push({ gemeenteNaam, naam, functie, email, website, isTopGemeente });
  }

  // Sort: top gemeenten first, then alphabetically
  contacts.sort((a, b) => {
    if (a.isTopGemeente && !b.isTopGemeente) return -1;
    if (!a.isTopGemeente && b.isTopGemeente) return 1;
    return a.gemeenteNaam.localeCompare(b.gemeenteNaam);
  });

  console.log(`Found ${contacts.length} sustainability contacts across ${new Set(contacts.map(c => c.gemeenteNaam)).size} gemeenten.`);
  console.log(`Top-50 gemeente contacts: ${contacts.filter(c => c.isTopGemeente).length}\n`);

  if (DRY_RUN) {
    console.log('First 15 contacts:');
    contacts.slice(0, 15).forEach(c =>
      console.log(`  ${c.isTopGemeente ? '★' : ' '} ${c.gemeenteNaam} | ${c.naam} | ${c.functie}`)
    );
    if (contacts.length > 15) console.log(`  ... and ${contacts.length - 15} more`);
    console.log(`\nDry run done. Run with --write to write to sheet.`);
    return;
  }

  const rows = contacts.map(c => [c.gemeenteNaam, c.naam, c.functie, c.email, c.website]);

  const auth = await getAuth();
  const sheets = google.sheets({ version: 'v4', auth });
  await ensureSheet(sheets);
  await writeToSheet(sheets, rows);
  console.log(`\nDone. Check the "${SHEET_NAME}" tab in your Google Sheet.`);
}

main().catch(err => { console.error(err); process.exit(1); });
