import chokidar from 'chokidar';
import fs from 'fs';
import path from 'path';
import dotenv from 'dotenv';
import {
  isRealtimeSyncCandidate,
  isSyncCandidate,
  createUploadBody,
  isUploadCurrent,
} from './sync-policy.js';

dotenv.config();

const SYNC_URL = process.env.VERCEL_SYNC_URL;
const SYNC_SECRET = process.env.SYNC_SECRET;
const STABILITY_THRESHOLD = parseInt(process.env.STABILITY_THRESHOLD_MS || '1000', 10);
const POLL_INTERVAL = parseInt(process.env.POLL_INTERVAL_MS || '250', 10);
const MAX_RETRIES = parseInt(process.env.MAX_RETRIES || '5', 10);
const SYNC_REQUEST_TIMEOUT_MS = 10_000;
const REALTIME_UPLOAD_TIMEOUT_MS = 15_000;
const CLOUD_SYNC_RETRY_DELAY_MS = 5 * 60 * 1000;
const ENABLE_CLOUD_SYNC = (process.env.ENABLE_CLOUD_SYNC || '0') === '1';
function resolveLocalStorageRoot() {
  if (process.env.CHICKENSEE_LOCAL_STORAGE) {
    const configured = path.resolve(process.env.CHICKENSEE_LOCAL_STORAGE);
    try {
      if (!fs.existsSync(configured)) {
        fs.mkdirSync(configured, { recursive: true });
      }
      return configured;
    } catch {
      // Fallback if permission error or non-existent user profile
    }
  }
  const fallback = path.resolve(process.cwd(), '..', 'local-storage');
  try {
    fs.mkdirSync(fallback, { recursive: true });
  } catch {}
  return fallback;
}

const LOCAL_STORAGE_ROOT = resolveLocalStorageRoot();
const STATE_FILE = path.resolve('./.sync-state.json');
const CONFIG_FILE = path.resolve('./directories.json');

let cloudSyncDisabledReason = ENABLE_CLOUD_SYNC ? null : 'disabled by ENABLE_CLOUD_SYNC=0';
const pendingCloudUploads = new Set();
const realtimeSyncRequested = new Set();
let realtimeSyncRunning = false;

// ─── Resolve watch directories ────────────────────────────────────────────────
// Priority: directories.json > WATCH_DIRS env (comma-separated) > WATCH_DIR > ./data
function resolveWatchDirs() {
  const dirs = new Set();
  const defaultDataDir = path.resolve(process.cwd(), 'data');

  // 1. Read from directories.json (set via `npm run setup`)
  if (fs.existsSync(CONFIG_FILE)) {
    try {
      const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
      if (Array.isArray(config.directories)) {
        config.directories.forEach((d) => {
          let resolved = path.resolve(d);
          if (!fs.existsSync(resolved) && (d.endsWith('/agent/data') || d.endsWith('\\agent\\data'))) {
            resolved = defaultDataDir;
          }
          if (fs.existsSync(resolved)) {
            dirs.add(resolved);
          }
        });
      }
    } catch (err) {
      console.warn('[WARN] Could not parse directories.json:', err.message);
    }
  }

  // 2. WATCH_DIRS env var (comma-separated list)
  if (process.env.WATCH_DIRS) {
    process.env.WATCH_DIRS.split(',')
      .map((d) => d.trim())
      .filter(Boolean)
      .forEach((d) => {
        let resolved = path.resolve(d);
        if (!fs.existsSync(resolved) && (d.endsWith('/agent/data') || d.endsWith('\\agent\\data'))) {
          resolved = defaultDataDir;
        }
        if (fs.existsSync(resolved)) {
          dirs.add(resolved);
        }
      });
  }

  // 3. Fallback single WATCH_DIR env var or ./data
  if (dirs.size === 0) {
    const fallbackDir = process.env.WATCH_DIR ? path.resolve(process.env.WATCH_DIR) : defaultDataDir;
    if (fs.existsSync(fallbackDir)) {
      dirs.add(fallbackDir);
    } else {
      try {
        fs.mkdirSync(defaultDataDir, { recursive: true });
        dirs.add(defaultDataDir);
      } catch (err) {
        console.warn('[WARN] Could not create fallback watch dir:', err.message);
      }
    }
  }

  // Analysis reports are written outside the source folders and need syncing too.
  const resultsDir = path.resolve(LOCAL_STORAGE_ROOT, 'results');
  dirs.add(resultsDir);
  return Array.from(dirs);
}

// ─── State Management ─────────────────────────────────────────────────────────
let syncState = {};
try {
  if (fs.existsSync(STATE_FILE)) {
    syncState = JSON.parse(fs.readFileSync(STATE_FILE, 'utf-8'));
  }
} catch (err) {
  console.warn('[WARN] Could not read sync state file, starting fresh:', err.message);
  syncState = {};
}

function saveState() {
  try {
    fs.writeFileSync(STATE_FILE, JSON.stringify(syncState, null, 2));
  } catch (err) {
    console.error('[ERROR] Failed to persist sync state:', err.message);
  }
}

// ─── Logging ──────────────────────────────────────────────────────────────────
function log(level, message, meta = '') {
  const timestamp = new Date().toISOString();
  console.log(`[${timestamp}] [${level}] ${message} ${meta}`.trim());
}

function isPermanentCloudError(message) {
  const m = String(message || '').toLowerCase();
  return (
    m.includes('has been suspended')
    || m.includes('store has been suspended')
    || m.includes('payment required')
    || m.includes('storage quota exceeded')
  );
}

function disableCloudSync(reason) {
  if (cloudSyncDisabledReason) return;
  cloudSyncDisabledReason = reason;
  log('WARN', `Cloud upload disabled for this session: ${reason}`);
  log('WARN', 'Files will remain local while cloud storage is unavailable; sync will retry every 5 minutes.');

  setTimeout(() => {
    if (cloudSyncDisabledReason !== reason) return;

    cloudSyncDisabledReason = null;
    log('INFO', 'Retrying cloud sync for files pending during the outage.');
    const pendingFiles = Array.from(pendingCloudUploads);
    pendingCloudUploads.clear();
    pendingFiles.forEach((filePath) => {
      if (shouldSync(filePath)) enqueueUpload(filePath);
    });
  }, CLOUD_SYNC_RETRY_DELAY_MS);
}

// ─── Direct Supabase Storage Upload ────────────────────────────────────────────
async function uploadDirectToSupabase(filePath, filename, ext, stats) {
  const tokenController = new AbortController();
  const tokenTimeout = setTimeout(
    () => tokenController.abort(),
    SYNC_REQUEST_TIMEOUT_MS,
  );
  let tokenData;
  try {
    const tokenRes = await fetch(SYNC_URL, {
      method: 'POST',
      headers: {
        'x-sync-secret': SYNC_SECRET,
        'x-filename': encodeURIComponent(filename),
        'x-action': 'get-upload-url',
      },
      signal: tokenController.signal,
    });

    if (!tokenRes.ok) {
      const errText = await tokenRes.text();
      throw new Error(`Supabase upload URL request failed (HTTP ${tokenRes.status}): ${errText}`);
    }

    tokenData = await tokenRes.json();
  } finally {
    clearTimeout(tokenTimeout);
  }

  if (!tokenData.uploadUrl || !tokenData.storagePath) {
    throw new Error('Sync API did not return a Supabase upload URL and storage path');
  }

  const mimeType = ext === '.mp4'
    ? 'video/mp4'
    : ext === '.json'
      ? 'application/json'
      : ext === '.jpg'
        ? 'image/jpeg'
        : 'application/x-sqlite3';
  const realtime = isRealtimeSyncCandidate(filePath, LOCAL_STORAGE_ROOT);
  const uploadBody = createUploadBody(filePath, LOCAL_STORAGE_ROOT);
  const contentLength = Buffer.isBuffer(uploadBody) ? uploadBody.length : stats.size;
  const uploadController = realtime ? new AbortController() : null;
  const uploadTimeout = uploadController
    ? setTimeout(
        () => uploadController.abort(),
        REALTIME_UPLOAD_TIMEOUT_MS,
    )
    : null;

  try {
    const response = await fetch(tokenData.uploadUrl, {
      method: 'PUT',
      headers: {
        'content-type': mimeType,
        'content-length': contentLength.toString(),
        'x-upsert': 'true',
      },
      body: uploadBody,
      duplex: 'half',
      ...(uploadController ? { signal: uploadController.signal } : {}),
    });

    if (!response.ok) {
      const errText = await response.text();
      throw new Error(`Supabase Storage upload failed (HTTP ${response.status}): ${errText}`);
    }
  } finally {
    if (uploadTimeout) clearTimeout(uploadTimeout);
    if (!Buffer.isBuffer(uploadBody)) uploadBody.destroy();
  }

  return { storagePath: tokenData.storagePath };
}

// ─── Upload logic with Automatic Retry ────────────────────────────────────────
async function uploadFileWithRetry(filePath, attempt = 1) {
  const filename = path.basename(filePath);
  const stats = fs.statSync(filePath);
  const ext = path.extname(filename).toLowerCase();
  const sizeMB = (stats.size / 1024 / 1024).toFixed(2);

  if (cloudSyncDisabledReason) {
    if (!pendingCloudUploads.has(filePath)) {
      log('SKIP', `Cloud upload skipped for "${filename}" (${cloudSyncDisabledReason})`);
    }
    pendingCloudUploads.add(filePath);
    return;
  }

  log('INFO', `[Attempt ${attempt}/${MAX_RETRIES}] Uploading "${filename}" (${sizeMB} MB)...`);

  try {
    const result = await uploadDirectToSupabase(filePath, filename, ext, stats);

    log('SUCCESS', `Synced "${filename}" →`, result.storagePath);

    syncState[path.resolve(filePath)] = {
      size: stats.size,
      mtime: stats.mtimeMs,
      uploadedAt: new Date().toISOString(),
      storage: 'supabase',
      storagePath: result.storagePath,
    };
    saveState();
  } catch (error) {
    log('ERROR', `Upload failed for "${filename}": ${error.message}`);
    if (isPermanentCloudError(error.message)) {
      pendingCloudUploads.add(filePath);
      disableCloudSync('Supabase Storage is unavailable');
      return;
    }
    if (attempt < MAX_RETRIES) {
      const delayMs = Math.pow(2, attempt) * 1000;
      log('INFO', `Retrying in ${delayMs / 1000}s...`);
      await new Promise((r) => setTimeout(r, delayMs));
      return uploadFileWithRetry(filePath, attempt + 1);
    } else {
      log('ERROR', `Max retries exceeded for "${filename}". Will retry on next file change.`);
      setTimeout(() => {
        if (!cloudSyncDisabledReason && shouldSync(filePath)) {
          log('INFO', `Retrying upload for "${filename}" after the retry delay.`);
          enqueueUpload(filePath);
        }
      }, 60_000);
    }
  }
}

function shouldSync(filePath) {
  const filename = path.basename(filePath);
  const resolvedPath = path.resolve(filePath);

  if (!isSyncCandidate(resolvedPath, LOCAL_STORAGE_ROOT)) return false;
  if (filename.startsWith('.') || filename.endsWith('.tmp') || filename.endsWith('.part')) return false;

  if (!fs.existsSync(resolvedPath)) return false;

  const stats = fs.statSync(resolvedPath);
  const prev = syncState[resolvedPath];

  if (isUploadCurrent(prev, stats)) {
    return false;
  }

  return true;
}

// ─── Sequential Upload Queue ──────────────────────────────────────────────────
const uploadQueue = [];
let isProcessingQueue = false;

function enqueueUpload(filePath) {
  if (isRealtimeSyncCandidate(filePath, LOCAL_STORAGE_ROOT)) {
    realtimeSyncRequested.add(filePath);
    if (realtimeSyncRunning) return;

    realtimeSyncRunning = true;
    void (async () => {
      try {
        while (realtimeSyncRequested.size > 0) {
          const [nextFile] = realtimeSyncRequested;
          realtimeSyncRequested.delete(nextFile);
          if (shouldSync(nextFile)) {
            log('QUEUE', `Prioritizing live status sync for "${path.basename(nextFile)}".`);
            await uploadFileWithRetry(nextFile);
          }
        }
      } catch (error) {
        log('ERROR', 'Could not synchronize live detection status:', error.message);
      } finally {
        realtimeSyncRunning = false;
        if (realtimeSyncRequested.size > 0) {
          enqueueUpload(realtimeSyncRequested.values().next().value);
        }
      }
    })();
    return;
  }

  if (!uploadQueue.includes(filePath)) {
    uploadQueue.push(filePath);
    // Sort queue by file size ascending so small files sync first
    uploadQueue.sort((a, b) => {
      try {
        return fs.statSync(a).size - fs.statSync(b).size;
      } catch {
        return 0;
      }
    });
    log('QUEUE', `Queued "${path.basename(filePath)}" (Queue size: ${uploadQueue.length})`);
    processQueue();
  }
}

async function processQueue() {
  if (isProcessingQueue || uploadQueue.length === 0) return;
  isProcessingQueue = true;

  while (uploadQueue.length > 0) {
    const nextFile = uploadQueue.shift();
    if (shouldSync(nextFile)) {
      log('QUEUE', `Processing (${uploadQueue.length} remaining): "${path.basename(nextFile)}"`);
      await uploadFileWithRetry(nextFile);
    }
  }

  isProcessingQueue = false;
}

// ─── Main Watcher Initializer ─────────────────────────────────────────────────
function startWatcher() {
  const watchDirs = resolveWatchDirs();

  console.log('[START] ══════════════════════════════════════════════════');
  console.log('[START]   ChickenSee Sync Agent (Sequential Queue)');
  console.log('[START] ══════════════════════════════════════════════════');
  console.log(`[START] Sync target : ${SYNC_URL || '(not set - check .env)'}`);
  console.log(`[START] Cloud sync  : ${cloudSyncDisabledReason ? `OFF (${cloudSyncDisabledReason})` : 'ON'}`);
  console.log('[START] Video uploads: OFF (results, queue status, and databases only)');
  console.log(`[START] Local store : ${LOCAL_STORAGE_ROOT}`);
  console.log(`[START] Directories : ${watchDirs.length} configured`);
  watchDirs.forEach((d) => console.log(`[START]   → ${d}`));
  console.log('[START] ══════════════════════════════════════════════════');

  // Verify and create directories if needed
  watchDirs.forEach((d) => {
    if (!fs.existsSync(d)) {
      try {
        fs.mkdirSync(d, { recursive: true });
        log('INFO', `Created directory: ${d}`);
      } catch (err) {
        log('WARN', `Could not create directory ${d}:`, err.message);
      }
    }
  });

  const watcher = chokidar.watch(watchDirs, {
    persistent: true,
    ignoreInitial: false,
    usePolling: process.env.CHOKIDAR_USEPOLLING === 'true',
    interval: POLL_INTERVAL,
    binaryInterval: POLL_INTERVAL,
    awaitWriteFinish: {
      stabilityThreshold: STABILITY_THRESHOLD,
      pollInterval: POLL_INTERVAL,
    },
    ignored: /(^|[\/\\])\..|(\.tmp$)|(\.part$)/,
    depth: 3,
  });

  watcher
    .on('add', (filePath) => {
      if (shouldSync(filePath)) {
        log('DETECT', `New file ready: ${path.basename(filePath)}  (${path.dirname(filePath)})`);
        enqueueUpload(filePath);
      }
    })
    .on('change', (filePath) => {
      if (shouldSync(filePath)) {
        log('DETECT', `File changed: ${path.basename(filePath)}  (${path.dirname(filePath)})`);
        enqueueUpload(filePath);
      }
    })
    .on('error', (err) => {
      log('ERROR', 'Watcher error:', err.message);
    })
    .on('ready', () => {
      log('READY', 'Initial scan complete. Watching for new or updated files...');
    });

  process.on('SIGINT', () => {
    log('SHUTDOWN', 'Stopping watcher gracefully...');
    watcher.close().then(() => {
      saveState();
      process.exit(0);
    });
  });

  process.on('SIGTERM', () => {
    watcher.close().then(() => {
      saveState();
      process.exit(0);
    });
  });
}

startWatcher();
