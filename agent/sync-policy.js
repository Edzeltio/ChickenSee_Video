import fs from 'fs';
import path from 'path';

function isWithinDirectory(filePath, directory) {
  const relativePath = path.relative(directory, filePath);
  return relativePath !== ''
    && relativePath !== '..'
    && !relativePath.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relativePath);
}

export function isSyncCandidate(filePath, localStorageRoot) {
  const resolvedPath = path.resolve(filePath);
  const filename = path.basename(resolvedPath);
  const extension = path.extname(filename).toLowerCase();
  const resultsDir = path.resolve(localStorageRoot, 'results');

  if (extension === '.mp4') return false;
  if (
    extension === '.jpg'
    && filename.toLowerCase() === 'live_detection.jpg'
    && isWithinDirectory(resolvedPath, resultsDir)
  ) return true;

  if (extension === '.sqlite' || extension === '.db') return true;
  return extension === '.json' && isWithinDirectory(resolvedPath, resultsDir);
}

export function isRealtimeSyncCandidate(filePath, localStorageRoot) {
  const resolvedPath = path.resolve(filePath);
  const resultsDir = path.resolve(localStorageRoot, 'results');
  return [
    path.join(resultsDir, 'queue_status.json'),
    path.join(resultsDir, 'live_detection.jpg'),
  ].some((candidate) => resolvedPath === path.resolve(candidate));
}

export function createUploadBody(filePath, localStorageRoot) {
  return isRealtimeSyncCandidate(filePath, localStorageRoot)
    ? fs.readFileSync(filePath)
    : fs.createReadStream(filePath);
}

export function isUploadCurrent(previousUpload, stats) {
  return Boolean(
    previousUpload?.storage === 'supabase'
    && previousUpload?.storagePath
    && previousUpload.size === stats.size
    && previousUpload.mtime === stats.mtimeMs,
  );
}
