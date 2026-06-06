#!/usr/bin/env node

/**
 * School of Recycling — Outreach Automation
 *
 * Reads the Tracker sheet, sends E1/E2/E3 emails via Gmail,
 * checks for bounces and warm replies, and updates the sheet.
 *
 * Usage:
 *   node outreach.js          — dry run (preview only, no emails sent)
 *   node outreach.js --send   — live run (sends emails, updates sheet)
 *   node outreach.js --replies — scan inbox for warm replies
 *
 * Auth: uses Application Default Credentials (set by google-github-actions/auth in CI,
 * or GOOGLE_APPLICATION_CREDENTIALS locally pointing to a credential config file).
 */

import { readFileSync, writeFileSync, existsSync } from 'fs';
import { google } from 'googleapis';

const SPREADSHEET_ID = '1QTCF2nddHm87mDYiRtLBYQKD6j6C1DPC22h2CLENQ1E';
const SHEET = 'Tracker';
const DRY_RUN = !process.argv.includes('--send');
const CHECK_REPLIES = process.argv.includes('--replies');
const FOLLOW_UP_DAYS = 7;
const LOG_FILE = './outreach-log.json';
const SENDER = 'Martijn Huizing <martijn@schoolofrecycling.com>';
const SENDER_EMAIL = 'martijn@schoolofrecycling.com';

const COL = {
  ACTION: 0,
  SEND: 1,
  LANGUAGE: 2,
  SCHOOL: 3,
  CITY: 4,
  PROVINCE: 5,
  COUNTRY: 6,
  EMAIL: 7,
  E1_SENT: 8,
  E2_SENT: 9,
  E3_SENT: 10,
  STATUS: 11,
  NOTES: 12,
};

const SKIP_STATUSES = ['done', 'bounced', 'unsubscribe', 'niet geïnteresseerd', 'no', 'stop'];

const WARM_KEYWORDS = [
  'ja graag', 'ja, graag', 'interesse', 'meer informatie', 'stuur maar',
  'klinkt goed', 'ja hoor', 'graag meer', 'afspraak', 'gesprek',
  'yes please', 'interested', 'tell me more', 'sounds good', 'love to',
  'would like', 'please send', 'yes', 'graag',
];

async function getAuthClient() {
  const auth = new google.auth.GoogleAuth({
    scopes: [
      'https://www.googleapis.com/auth/gmail.modify',
      'https://www.googleapis.com/auth/spreadsheets',
    ],
  });
  return auth.getClient();
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function daysSince(dateStr) {
  if (!dateStr) return Infinity;
  const d = new Date(dateStr);
  if (isNaN(d)) return Infinity;
  return Math.floor((Date.now() - d.getTime()) / (1000 * 60 * 60 * 24));
}

function today() {
  return new Date().toISOString().split('T')[0];
}

function loadLog() {
  if (existsSync(LOG_FILE)) return JSON.parse(readFileSync(LOG_FILE, 'utf8'));
  return { sent: [], errors: [], warmReplies: [] };
}

function saveLog(log) {
  writeFileSync(LOG_FILE, JSON.stringify(log, null, 2));
}

function makeEmail(template, school) {
  return template.replace(/\{\{School\}\}/g, school);
}

const TEMPLATES = {
  subject1: 'Engelstalig lesprogramma plastic vervuiling — voor uw duurzaamheidscoördinator of Engels docent',
  body1: (school) => makeEmail(`Geachte medewerker van {{School}},

Mijn naam is Martijn Huizing, oprichter van School of Recycling. Wij ontwikkelen digitale Engelstalige lesprogramma's over duurzaamheid, speciaal ontwikkeld voor de middelbare school.

Ons eerste programma, Waste Detective: Plastic, behandelt plastic vervuiling in vijf korte online videolessen van 10 minuten, elk met bijbehorende werkbladen. Alles is kant-en-klaar voor de docent, geen voorbereiding nodig. Omdat het programma volledig in het Engels is, sluit het direct aan bij de Engelse les of een duurzaamheidsproject.

Het programma is nu beschikbaar voor leerlingen van 10 tot 16 jaar. Versies voor 17+ leerlingen komen zeer binnenkort beschikbaar, zodat het programma straks de hele school bedient.

Zou u deze email willen doorsturen naar uw duurzaamheidscoördinator of Engels docent?

Ik zou graag een kort kennismakingsgesprek inplannen om te kijken of het programma aansluit bij uw curriculum.

Met vriendelijke groet,

Martijn Huizing
Founder
martijn@schoolofrecycling.com
www.schoolofrecycling.com`, school),

  subject2: 'Re: Engelstalig lesprogramma plastic vervuiling — voor uw duurzaamheidscoördinator of Engels docent',
  body2: (school) => makeEmail(`Geachte medewerker van {{School}},

Ik stuur even een korte herinnering bij mijn vorige email, voor het geval die is blijven liggen.

Ik begrijp dat docenten het druk hebben, dus ik houd het kort. Waste Detective: Plastic is een volledig online Engelstalig programma over plastic vervuiling, nu beschikbaar voor leerlingen van 10 tot 16 jaar. Versies voor 17+ komen zeer binnenkort beschikbaar, zodat het programma straks de hele school bedient.

Ik stuur graag een gratis werkblad mee zodat u alvast een indruk kunt krijgen van de inhoud en kwaliteit, geheel vrijblijvend.

Zou u deze email willen doorsturen naar uw duurzaamheidscoördinator of Engels docent? Of geef mij het juiste email adres en ik neem zelf contact op.

Met vriendelijke groet,

Martijn Huizing
Founder
martijn@schoolofrecycling.com
www.schoolofrecycling.com`, school),

  subject3: 'Probeer module 1 gratis uit met uw leerlingen. Stuur gewoon "ja graag" terug.',
  body3: (school) => makeEmail(`Geachte medewerker van {{School}},

Dit is mijn laatste bericht voor nu, ik wil uw inbox niet onnodig vullen.

Ik wil {{School}} graag module 1 van Waste Detective: Plastic volledig gratis aanbieden om uit te proberen. Het is een online Engelstalig programma over plastic vervuiling, ideaal voor de Engelse les of als onderdeel van een duurzaamheidsproject. Versies voor oudere leerlingen komen binnenkort beschikbaar.

Geen gesprek nodig, geen verplichting. Als het aanslaat en u wilt meer weten, dan praten we verder.

Stuur gewoon "ja graag" terug en ik regel de toegang.

Met vriendelijke groet,

Martijn Huizing
Founder
martijn@schoolofrecycling.com
www.schoolofrecycling.com`, school),
};

function buildRawMessage(to, subject, body) {
  const message = [
    `To: ${to}`,
    `From: ${SENDER}`,
    `Subject: ${subject}`,
    `Content-Type: text/plain; charset=utf-8`,
    ``,
    body,
  ].join('\r\n');
  return Buffer.from(message).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function sendEmail(gmail, to, subject, body, log) {
  if (DRY_RUN) {
    console.log(`  [DRY RUN] Would send to: ${to}`);
    console.log(`  Subject: ${subject}`);
    return true;
  }
  try {
    await gmail.users.messages.send({
      userId: 'me',
      requestBody: { raw: buildRawMessage(to, subject, body) },
    });
    log.sent.push({ to, subject, date: today() });
    return true;
  } catch (e) {
    console.error(`  ERROR sending to ${to}: ${e.message}`);
    log.errors.push({ to, subject, error: e.message, date: today() });
    return false;
  }
}

async function updateCell(sheets, rowIndex, colIndex, value) {
  const row = rowIndex + 2;
  const col = String.fromCharCode(65 + colIndex);
  const range = `${SHEET}!${col}${row}`;
  if (DRY_RUN) {
    console.log(`  [DRY RUN] Would update ${range} = "${value}"`);
    return;
  }
  await sheets.spreadsheets.values.update({
    spreadsheetId: SPREADSHEET_ID,
    range,
    valueInputOption: 'RAW',
    requestBody: { values: [[value]] },
  });
}

async function checkWarmReplies(gmail, log) {
  console.log('\n=== Scanning for warm replies ===\n');
  const res = await gmail.users.messages.list({ userId: 'me', labelIds: ['INBOX'], maxResults: 100 });
  const messages = res.data.messages || [];
  console.log(`Found ${messages.length} messages to scan`);

  for (const msg of messages) {
    const full = await gmail.users.messages.get({ userId: 'me', id: msg.id, format: 'full' });
    const headers = full.data.payload?.headers || [];
    const from = headers.find(h => h.name === 'From')?.value || '';
    const subject = headers.find(h => h.name === 'Subject')?.value || '';
    const snippet = full.data.snippet || '';

    const isWarm = WARM_KEYWORDS.some(kw =>
      snippet.toLowerCase().includes(kw) || subject.toLowerCase().includes(kw)
    );

    if (isWarm && !log.warmReplies.find(r => r.messageId === msg.id)) {
      console.log(`\n🔥 WARM REPLY from: ${from}`);
      console.log(`   Subject: ${subject}`);
      console.log(`   Preview: ${snippet.slice(0, 120)}`);
      log.warmReplies.push({ messageId: msg.id, from, subject, snippet: snippet.slice(0, 200), date: today() });
    }
  }
  console.log('\nWarm reply scan complete.');
}

async function checkBounces(gmail, sheets, rows, log) {
  console.log('\n=== Scanning for bounces ===\n');
  const res = await gmail.users.messages.list({
    userId: 'me',
    q: 'from:mailer-daemon OR from:postmaster subject:delivery OR subject:undeliverable',
    maxResults: 50,
  });
  if (!res.data.messages) { console.log('No bounce messages found.'); return; }

  for (const msg of res.data.messages) {
    const full = await gmail.users.messages.get({ userId: 'me', id: msg.id, format: 'full' });
    const snippet = full.data.snippet || '';

    for (let i = 0; i < rows.length; i++) {
      const email = rows[i][COL.EMAIL] || '';
      const status = (rows[i][COL.STATUS] || '').toLowerCase();
      if (email && snippet.toLowerCase().includes(email.toLowerCase()) && status !== 'bounced') {
        console.log(`  Bounce detected for: ${email} (row ${i + 2})`);
        await updateCell(sheets, i, COL.STATUS, 'Bounced');
        rows[i][COL.STATUS] = 'Bounced';
      }
    }
  }
  console.log('Bounce scan complete.');
}

async function main() {
  const log = loadLog();
  const auth = await getAuthClient();
  const gmail = google.gmail({ version: 'v1', auth });
  const sheets = google.sheets({ version: 'v4', auth });

  console.log(`\n=== School of Recycling Outreach Automation ===`);
  console.log(`Mode: ${DRY_RUN ? 'DRY RUN (add --send to actually send)' : 'LIVE'}\n`);

  console.log('Fetching sheet data...');
  const sheetRes = await sheets.spreadsheets.values.batchGet({
    spreadsheetId: SPREADSHEET_ID,
    ranges: [SHEET],
  });
  const rows = sheetRes.data.valueRanges?.[0]?.values?.slice(1) || [];
  console.log(`Loaded ${rows.length} rows\n`);

  if (CHECK_REPLIES) {
    await checkWarmReplies(gmail, log);
    saveLog(log);
    if (log.warmReplies.length > 0) {
      console.log(`\n=== Warm Replies Summary ===`);
      log.warmReplies.forEach(r => console.log(`\nFrom: ${r.from}\nSubject: ${r.subject}\nPreview: ${r.snippet}`));
    }
    return;
  }

  await checkBounces(gmail, sheets, rows, log);

  let e1Count = 0, e2Count = 0, e3Count = 0, skipped = 0;

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const school = row[COL.SCHOOL] || '';
    const email = row[COL.EMAIL] || '';
    const status = (row[COL.STATUS] || '').toLowerCase().trim();
    const sendFlag = (row[COL.SEND] || '').trim();
    const e1Sent = row[COL.E1_SENT] || '';
    const e2Sent = row[COL.E2_SENT] || '';
    const e3Sent = row[COL.E3_SENT] || '';

    if (!email || !school) { skipped++; continue; }
    if (SKIP_STATUSES.some(s => status.includes(s))) { skipped++; continue; }

    if (sendFlag === 'Send' && !e1Sent) {
      console.log(`[E1] ${school} <${email}>`);
      const sent = await sendEmail(gmail, email, TEMPLATES.subject1, TEMPLATES.body1(school), log);
      if (sent) {
        await updateCell(sheets, i, COL.E1_SENT, today());
        row[COL.E1_SENT] = today();
        e1Count++;
        await sleep(2000);
      }
      continue;
    }

    if (e1Sent && !e2Sent && daysSince(e1Sent) >= FOLLOW_UP_DAYS) {
      console.log(`[E2] ${school} <${email}> (E1 sent ${daysSince(e1Sent)} days ago)`);
      const sent = await sendEmail(gmail, email, TEMPLATES.subject2, TEMPLATES.body2(school), log);
      if (sent) {
        await updateCell(sheets, i, COL.E2_SENT, today());
        row[COL.E2_SENT] = today();
        e2Count++;
        await sleep(2000);
      }
      continue;
    }

    if (e2Sent && !e3Sent && daysSince(e2Sent) >= FOLLOW_UP_DAYS) {
      console.log(`[E3] ${school} <${email}> (E2 sent ${daysSince(e2Sent)} days ago)`);
      const sent = await sendEmail(gmail, email, TEMPLATES.subject3, TEMPLATES.body3(school), log);
      if (sent) {
        await updateCell(sheets, i, COL.E3_SENT, today());
        row[COL.E3_SENT] = today();
        e3Count++;
        await sleep(2000);
      }
      continue;
    }

    skipped++;
  }

  saveLog(log);

  console.log(`\n=== Summary ===`);
  console.log(`E1 sent: ${e1Count}`);
  console.log(`E2 sent: ${e2Count}`);
  console.log(`E3 sent: ${e3Count}`);
  console.log(`Skipped: ${skipped}`);
  console.log(`Errors:  ${log.errors.length}`);
  if (DRY_RUN) console.log(`\nThis was a DRY RUN. Run with --send to actually send emails.`);
}

main().catch(err => { console.error(err); process.exit(1); });
