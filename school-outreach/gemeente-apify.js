#!/usr/bin/env node

/**
 * Gemeente Apify Email Scraper
 *
 * Reads website URLs from the Gemeenten sheet, runs the Apify
 * Contact Details Scraper on them, then writes discovered emails back.
 *
 * Prerequisites:
 *   1. Run gemeente-import.js --write first (populates website URLs)
 *   2. Set APIFY_TOKEN env var to your personal API token
 *
 * Usage:
 *   APIFY_TOKEN=your_token node gemeente-apify.js          — dry run
 *   APIFY_TOKEN=your_token node gemeente-apify.js --run    — trigger Apify + update sheet
 */

import { get as httpsGet, request as httpsRequest } from 'https';
import { google } from 'googleapis';

const SPREADSHEET_ID = '1QTCF2nddHm87mDYiRtLBYQKD6j6C1DPC22h2CLENQ1E';
const SHEET_NAME = 'Gemeenten';
const DRY_RUN = !process.argv.includes('--run');

const APIFY_TOKEN = process.env.APIFY_TOKEN;
const APIFY_ACTOR = 'apify/contact-details-scraper';

// Column indices in the Gemeenten sheet (0-based, after header row)
const COL = { NAAM: 0, EMAIL: 1, WEBSITE: 2, CONTACTFORM: 3, BRON: 4 };

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

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function pollRun(runId) {
  console.log(`Polling run ${runId}...`);
  while (true) {
    const res = await apifyGet(`/v2/acts/${encodeURIComponent(APIFY_ACTOR)}/runs/${runId}?token=${APIFY_TOKEN}`);
    const status = res.data?.status;
    console.log(`  Status: ${status}`);
    if (status === 'SUCCEEDED') return res.data.defaultDatasetId;
    if (['FAILED', 'ABORTED', 'TIMED-OUT'].includes(status)) {
      throw new Error(`Apify run ${status}`);
    }
    await sleep(15000); // check every 15s
  }
}

async function fetchDataset(datasetId) {
  const res = await apifyGet(`/v2/datasets/${datasetId}/items?token=${APIFY_TOKEN}&format=json&clean=true`);
  return Array.isArray(res) ? res : (res.data?.items || []);
}

async function getAuth() {
  const auth = new google.auth.GoogleAuth({
    scopes: ['https://www.googleapis.com/auth/spreadsheets'],
  });
  return auth.getClient();
}

async function readSheet(sheets) {
  const res = await sheets.spreadsheets.values.get({
    spreadsheetId: SPREADSHEET_ID,
    range: `${SHEET_NAME}!A1:E`,
  });
  return res.data.values || [];
}

async function updateCell(sheets, row, col, value) {
  const colLetter = String.fromCharCode(65 + col);
  const range = `${SHEET_NAME}!${colLetter}${row}`;
  await sheets.spreadsheets.values.update({
    spreadsheetId: SPREADSHEET_ID,
    range,
    valueInputOption: 'RAW',
    requestBody: { values: [[value]] },
  });
}

/**
 * Pick the best email from Apify results for a given URL.
 * Prefers sustainability-related addresses, then generic info@, then first found.
 */
function pickBestEmail(emails) {
  if (!emails || emails.length === 0) return null;
  const sustainability = emails.find(e =>
    /duurzaam|milieu|klimaat|groen|recycl/i.test(e)
  );
  if (sustainability) return sustainability;
  const info = emails.find(e => /^info@|^gemeente@|^contact@/i.test(e));
  if (info) return info;
  return emails[0];
}

async function main() {
  console.log('\n=== Gemeente Apify Email Scraper ===');
  console.log(`Mode: ${DRY_RUN ? 'DRY RUN (add --run to trigger Apify)' : 'LIVE'}\n`);

  if (!APIFY_TOKEN) {
    console.error('ERROR: APIFY_TOKEN environment variable is not set.');
    console.error('  Export it before running: export APIFY_TOKEN=your_token_here');
    process.exit(1);
  }

  const auth = await getAuth();
  const sheets = google.sheets({ version: 'v4', auth });

  console.log('Reading Gemeenten sheet...');
  const rows = await readSheet(sheets);
  if (rows.length < 2) {
    console.error('Sheet is empty. Run gemeente-import.js --write first.');
    process.exit(1);
  }

  // Collect rows with a website but missing or derived email
  const targets = [];
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    const naam = row[COL.NAAM] || '';
    const email = row[COL.EMAIL] || '';
    const website = row[COL.WEBSITE] || '';
    const bron = row[COL.BRON] || '';

    if (!website) continue;
    // Skip if we already have a CSV-sourced email (most reliable)
    if (bron === 'CSV' && email) continue;

    targets.push({ sheetRow: i + 1, naam, website, currentEmail: email });
  }

  console.log(`Found ${targets.length} gemeenten to scrape (have website, no CSV email).\n`);

  if (DRY_RUN) {
    console.log('First 10 targets:');
    targets.slice(0, 10).forEach(t => console.log(`  ${t.naam} → ${t.website}`));
    if (targets.length > 10) console.log(`  ... and ${targets.length - 10} more`);
    console.log(`\nDry run done. Run with --run to trigger Apify scraper.`);
    return;
  }

  // Build start URL list for Apify
  const startUrls = targets.map(t => ({ url: t.website }));

  console.log(`Triggering Apify actor: ${APIFY_ACTOR}`);
  console.log(`Sending ${startUrls.length} URLs...\n`);

  const runRes = await apifyPost(
    `/v2/acts/${encodeURIComponent(APIFY_ACTOR)}/runs?token=${APIFY_TOKEN}`,
    {
      startUrls,
      maxDepth: 1,          // only crawl 1 level deep per site (contact/about pages)
      maxPagesPerDomain: 5, // limit pages per gemeente site
      contactSelectors: ['a[href^="mailto:"]', 'p', 'div', 'span'],
    }
  );

  const runId = runRes.data?.id;
  if (!runId) {
    console.error('Failed to start Apify run:', JSON.stringify(runRes, null, 2));
    process.exit(1);
  }

  console.log(`Run started. ID: ${runId}`);
  console.log('Waiting for completion (this takes ~10-20 minutes for 300 sites)...\n');

  const datasetId = await pollRun(runId);
  console.log(`\nRun complete. Dataset: ${datasetId}`);

  console.log('Fetching results...');
  const items = await fetchDataset(datasetId);
  console.log(`Got ${items.length} result items.\n`);

  // Build a map: url → emails[]
  const emailMap = {};
  for (const item of items) {
    const url = item.url || item.pageUrl || '';
    const emails = item.emails || item.contactEmails || [];
    const domain = (() => {
      try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; }
    })();
    if (domain && emails.length > 0) {
      if (!emailMap[domain]) emailMap[domain] = [];
      emailMap[domain].push(...emails);
    }
  }

  // Write emails back to sheet
  let updated = 0, notFound = 0;
  for (const target of targets) {
    let domain = '';
    try { domain = new URL(target.website).hostname.replace(/^www\./, ''); } catch {}

    const emails = emailMap[domain] || [];
    const best = pickBestEmail(emails);

    if (best) {
      console.log(`  ✓ ${target.naam}: ${best}`);
      await updateCell(sheets, target.sheetRow, COL.EMAIL, best);
      await updateCell(sheets, target.sheetRow, COL.BRON, 'apify');
      updated++;
    } else {
      notFound++;
    }
  }

  console.log(`\n=== Done ===`);
  console.log(`Emails found and written: ${updated}`);
  console.log(`No email found:           ${notFound}`);
}

main().catch(err => { console.error(err); process.exit(1); });
