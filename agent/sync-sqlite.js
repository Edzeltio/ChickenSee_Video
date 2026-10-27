import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.join(__dirname, '.env') });

const SYNC_URL = process.env.VERCEL_SYNC_URL;
const SYNC_SECRET = process.env.SYNC_SECRET;

const sourceFile = process.argv[2] || 'C:/Users/Kairi/Downloads/rec_2026-10-13_14-33-33_429922-20261026T110010Z-1-001/rec_2026-10-13_14-33-33_429922/sensors.sqlite';

async function uploadDb() {
  if (!fs.existsSync(sourceFile)) {
    console.error(`File not found: ${sourceFile}`);
    process.exit(1);
  }

  const stats = fs.statSync(sourceFile);
  const filename = path.basename(sourceFile);
  console.log(`[SYNC] Reading database: ${sourceFile} (${(stats.size / 1024).toFixed(1)} KB)`);
  console.log(`[SYNC] Target API: ${SYNC_URL}`);

  console.log('[SYNC] Requesting Supabase Storage upload URL...');
  const res = await fetch(SYNC_URL, {
    method: 'POST',
    headers: {
      'x-sync-secret': SYNC_SECRET,
      'x-filename': encodeURIComponent(filename),
      'x-action': 'get-upload-url',
    },
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.uploadUrl || !data.storagePath) {
    throw new Error(`Sync API error (${res.status}): ${JSON.stringify(data)}`);
  }

  console.log(`[SYNC] Generated signed URL for remote path: ${data.storagePath}`);
  console.log('[SYNC] Uploading SQLite payload directly to Supabase Storage...');

  const fileStream = fs.createReadStream(sourceFile);
  const putRes = await fetch(data.uploadUrl, {
    method: 'PUT',
    headers: {
      'content-type': 'application/x-sqlite3',
      'content-length': stats.size.toString(),
      'x-upsert': 'true',
    },
    body: fileStream,
    duplex: 'half',
  });

  if (!putRes.ok) {
    const errText = await putRes.text();
    throw new Error(`Upload failed (${putRes.status}): ${errText}`);
  }

  console.log(`\n======================================================`);
  console.log(`✓ SUCCESS: ${filename} is now synchronized to the cloud!`);
  console.log(`  Remote Path: ${data.storagePath}`);
  console.log(`  Size: ${stats.size} bytes`);
  console.log(`======================================================\n`);
}

uploadDb().catch((err) => {
  console.error('[SYNC ERROR]', err.message);
  process.exit(1);
});
