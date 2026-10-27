import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.join(__dirname, '.env') });

const SYNC_URL = process.env.VERCEL_SYNC_URL;
const SYNC_SECRET = process.env.SYNC_SECRET;
const LOCAL_STORAGE = process.env.CHICKENSEE_LOCAL_STORAGE || path.resolve(__dirname, '..', 'local-storage');
const RESULTS_DIR = path.join(LOCAL_STORAGE, 'results');
const REPO_DATA_RESULTS = path.resolve(__dirname, '..', 'data', 'results');

if (!fs.existsSync(REPO_DATA_RESULTS)) {
  fs.mkdirSync(REPO_DATA_RESULTS, { recursive: true });
}

console.log('--- ChickenSee Results & Queue Sync Tool ---');
console.log('Results folder:', RESULTS_DIR);
console.log('Vercel Target:', SYNC_URL);

async function syncAll() {
  if (!fs.existsSync(RESULTS_DIR)) {
    console.error(`Local results folder does not exist: ${RESULTS_DIR}`);
    return;
  }

  const files = fs.readdirSync(RESULTS_DIR).filter((f) => f.endsWith('.json'));
  console.log(`Found ${files.length} JSON file(s) to synchronize.\n`);

  let successCount = 0;
  let failCount = 0;

  for (const file of files) {
    const fullPath = path.join(RESULTS_DIR, file);
    const content = fs.readFileSync(fullPath);

    // Keep repo data/results folder mirrored
    try {
      fs.writeFileSync(path.join(REPO_DATA_RESULTS, file), content);
    } catch {}

    try {
      process.stdout.write(`Syncing ${file} (${content.length} B)... `);
      const signedUrlResponse = await fetch(SYNC_URL, {
        method: 'POST',
        headers: {
          'x-sync-secret': SYNC_SECRET,
          'x-filename': encodeURIComponent(file),
          'x-action': 'get-upload-url',
        },
      });

      const uploadInfo = await signedUrlResponse.json().catch(() => ({}));
      if (!signedUrlResponse.ok || !uploadInfo.uploadUrl || !uploadInfo.storagePath) {
        throw new Error(uploadInfo.error || `Upload URL request failed (${signedUrlResponse.status})`);
      }

      const uploadResponse = await fetch(uploadInfo.uploadUrl, {
        method: 'PUT',
        headers: {
          'content-type': 'application/json',
          'content-length': content.length.toString(),
          'x-upsert': 'true',
        },
        body: content,
      });

      if (uploadResponse.ok) {
        console.log(`✓ OK (${uploadInfo.storagePath})`);
        successCount++;
      } else {
        const responseText = await uploadResponse.text();
        console.log(`✗ ${uploadResponse.status}: ${responseText || 'Upload error'}`);
        failCount++;
      }
    } catch (err) {
      console.log(`✗ Network error: ${err.message}`);
      failCount++;
    }
  }

  console.log(`\n========================================`);
  console.log(`Sync completed: ${successCount} succeeded, ${failCount} failed.`);
  console.log(`========================================`);
}

syncAll();
