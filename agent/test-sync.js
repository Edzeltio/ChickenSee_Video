import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

dotenv.config({ path: path.join(__dirname, '.env') });

const SYNC_URL = process.env.VERCEL_SYNC_URL;
const SYNC_SECRET = process.env.SYNC_SECRET;

console.log('Testing URL:', SYNC_URL);
async function testSync() {
  try {
    const res = await fetch(SYNC_URL, {
      method: 'POST',
      headers: {
        'x-sync-secret': SYNC_SECRET,
        'x-action': 'get-upload-url',
        'x-filename': 'sync_connection_test.json',
      },
    });

    const status = res.status;
    const body = await res.json().catch(() => ({}));
    console.log(`Response Status: ${status}`);

    if (status === 200 && body.success && body.uploadUrl && body.storagePath) {
      console.log('\nSUCCESS: Sync authentication and Supabase upload URL generation succeeded.');
      console.log('Storage path:', body.storagePath);
    } else {
      console.error('\nFAILED:', body.error || `Status ${status}`);
    }
  } catch (err) {
    console.error('Fetch error:', err.message);
  }
}

testSync();
