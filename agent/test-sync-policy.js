import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';
import test from 'node:test';

import {
  createUploadBody,
  isRealtimeSyncCandidate,
  isSyncCandidate,
  isUploadCurrent,
} from './sync-policy.js';

test('video files are never synced, even when legacy video-sync setting is enabled', () => {
  const storage = path.join(os.tmpdir(), 'chickensee-sync-policy');
  const previousSetting = process.env.SYNC_VIDEOS_TO_CLOUD;
  process.env.SYNC_VIDEOS_TO_CLOUD = '1';

  try {
    assert.equal(
      isSyncCandidate(path.join(storage, 'videos', 'sample_detected.mp4'), storage),
      false,
    );
    assert.equal(
      isSyncCandidate(path.join(storage, 'incoming', 'sample.mp4'), storage),
      false,
    );
  } finally {
    if (previousSetting === undefined) {
      delete process.env.SYNC_VIDEOS_TO_CLOUD;
    } else {
      process.env.SYNC_VIDEOS_TO_CLOUD = previousSetting;
    }
  }

  assert.equal(isSyncCandidate(path.join(storage, 'results', 'sample.json'), storage), true);
  assert.equal(isSyncCandidate(path.join(storage, 'results', 'queue_status.json'), storage), true);
  assert.equal(isSyncCandidate(path.join(storage, 'results', 'live_detection.jpg'), storage), true);
  assert.equal(isSyncCandidate(path.join(storage, 'results', 'other.jpg'), storage), false);
  assert.equal(isSyncCandidate(path.join(storage, 'other', 'live_detection.jpg'), storage), false);
  assert.equal(isSyncCandidate(path.join(storage, 'telemetry.sqlite'), storage), true);
  assert.equal(isRealtimeSyncCandidate(path.join(storage, 'results', 'queue_status.json'), storage), true);
  assert.equal(isRealtimeSyncCandidate(path.join(storage, 'results', 'live_detection.jpg'), storage), true);
  assert.equal(isRealtimeSyncCandidate(path.join(storage, 'results', 'other.json'), storage), false);
});

test('realtime uploads buffer the source so cloud requests do not lock queue files', (t) => {
  const storage = fs.mkdtempSync(path.join(os.tmpdir(), 'chickensee-sync-policy-'));
  t.after(() => fs.rmSync(storage, { recursive: true, force: true }));

  const results = path.join(storage, 'results');
  fs.mkdirSync(results);
  const queuePath = path.join(results, 'queue_status.json');
  fs.writeFileSync(queuePath, '{"updated_at":"first"}');

  const body = createUploadBody(queuePath, storage);
  assert.ok(Buffer.isBuffer(body));
  assert.equal(body.toString(), '{"updated_at":"first"}');

  const replacementPath = `${queuePath}.replacement`;
  fs.writeFileSync(replacementPath, '{"updated_at":"second"}');
  fs.renameSync(queuePath, `${queuePath}.old`);
  fs.renameSync(replacementPath, queuePath);
  assert.equal(fs.readFileSync(queuePath, 'utf8'), '{"updated_at":"second"}');
});

test('source watch folders do not sync original MP4s', () => {
  const storage = path.join(os.tmpdir(), 'chickensee-sync-policy');
  const watchFolder = path.join(os.tmpdir(), 'camera-recordings');
  assert.equal(
    isSyncCandidate(path.join(watchFolder, 'uploaded.mp4'), storage),
    false,
  );
  assert.equal(
    isSyncCandidate(path.join(watchFolder, 'telemetry.db'), storage),
    true,
  );
});

test('only files with a confirmed Supabase Storage path count as uploaded', () => {
  const stats = { size: 100, mtimeMs: 200 };
  assert.equal(isUploadCurrent({ storagePath: 'results/file.json', size: 100, mtime: 200 }, stats), false);
  assert.equal(isUploadCurrent({
    storage: 'supabase',
    storagePath: 'results/file.json',
    size: 100,
    mtime: 200,
  }, stats), true);
  assert.equal(isUploadCurrent({
    storage: 'supabase',
    storagePath: 'results/file.json',
    size: 99,
    mtime: 200,
  }, stats), false);
});
