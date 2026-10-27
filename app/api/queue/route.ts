import { NextRequest, NextResponse } from 'next/server';
import { AUTH_COOKIE_NAME, verifySessionToken } from '@/lib/auth';
import { downloadStorageFile, listStorageFiles } from '@/lib/supabase-storage';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

let recoveredHistoryCache: { expiresAt: number; items: any[] } | null = null;
const MAX_QUEUE_STALENESS_SECONDS = 15;

function normalizeVideoName(value: string): string {
  return (value.split(/[\\/]/).pop() || value)
    .replace(/\.[^.]+$/, '')
    .replace(/_(?:detected|annotated)$/i, '')
    .toLowerCase();
}

function getQueueFreshness(
  updatedAt: string | null,
  activeDetection: { captured_at?: string } | null,
  isProcessing: boolean,
) {
  const freshnessTimestamp = isProcessing && activeDetection?.captured_at
    ? activeDetection.captured_at
    : updatedAt;
  const timestamp = Date.parse(String(freshnessTimestamp || ''));
  const ageSeconds = Number.isFinite(timestamp)
    ? Math.max(0, Math.floor((Date.now() - timestamp) / 1000))
    : null;
  return {
    source_stale: ageSeconds === null || ageSeconds > MAX_QUEUE_STALENESS_SECONDS,
    source_age_seconds: ageSeconds,
  };
}

async function recoverCompletedHistory(items: any[]) {
  if (!recoveredHistoryCache || recoveredHistoryCache.expiresAt <= Date.now()) {
    const resultFiles = (await listStorageFiles('results'))
      .filter((file) => file.pathname.toLowerCase().endsWith('.json') && file.pathname !== 'queue_status.json')
      .sort((first, second) => new Date(second.uploadedAt).getTime() - new Date(first.uploadedAt).getTime());
    const recovered: any[] = [];

    for (let offset = 0; offset < resultFiles.length; offset += 20) {
      const batch = resultFiles.slice(offset, offset + 20);
      const reports = await Promise.all(batch.map(async (file) => {
        try {
          const content = await downloadStorageFile(file.storagePath);
          const report = JSON.parse(await content.text());
          const videoFilename = typeof report.source_video === 'string' ? report.source_video : '';
          const jobId = Number.isInteger(report.job_id) ? report.job_id : null;
          const completedAt = typeof report.processed_at === 'string'
            ? report.processed_at
            : file.uploadedAt;
          return videoFilename
            ? {
              item_id: `recovered-${file.pathname}`,
              job_id: jobId,
              video_filename: videoFilename.split(/[\\/]/).pop() || videoFilename,
              status: 'COMPLETED',
              progress_percent: 100,
              frames: '—',
              details: 'Recovered from the synchronized analysis report.',
              created_at: completedAt,
              completed_at: completedAt,
            }
            : null;
        } catch (error) {
          console.warn(`Could not recover queue history from "${file.pathname}":`, error);
          return null;
        }
      }));

      for (const report of reports) {
        if (!report) continue;
        recovered.push(report);
      }
    }

    recoveredHistoryCache = {
      expiresAt: Date.now() + 60_000,
      items: recovered,
    };
  }

  const knownJobIds = new Set<number>();
  const knownVideoNames = new Set<string>();
  for (const item of items) {
    const jobId = Number.isInteger(item.job_id)
      ? item.job_id
      : /^job-(\d+)$/.exec(String(item.item_id || ''))?.[1];
    if (jobId !== undefined && jobId !== null) {
      knownJobIds.add(Number(jobId));
    } else {
      const normalizedName = normalizeVideoName(String(item.video_filename || ''));
      if (normalizedName) knownVideoNames.add(normalizedName);
    }
  }
  return recoveredHistoryCache.items.filter((item) => {
    if (Number.isInteger(item.job_id)) {
      if (knownJobIds.has(item.job_id)) return false;
      knownJobIds.add(item.job_id);
      return true;
    }
    const normalizedName = normalizeVideoName(item.video_filename);
    if (!normalizedName || knownVideoNames.has(normalizedName)) return false;
    knownVideoNames.add(normalizedName);
    return true;
  });
}

async function getLocalQueueResponse() {
  if (process.env.VERCEL) return null;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1200);
  try {
    const localRes = await fetch('http://127.0.0.1:8000/queue?limit=500', {
      signal: controller.signal,
      cache: 'no-store',
    });
    if (!localRes.ok) return null;

    const q = await localRes.json();
    const items = q.items || [];
    const summary = q.summary || {};
    const active = items.find((item: any) => item.status === 'PROCESSING');
    const activeDetection = q.active_detection || null;
    const freshness = getQueueFreshness(
      new Date().toISOString(),
      activeDetection,
      Boolean(active),
    );
    const isQueueing = (summary.processing || 0) + (summary.queued || 0) + (summary.waiting || 0) > 0;
    return NextResponse.json({
      source: 'local-api',
      updated_at: new Date().toISOString(),
      ...freshness,
      is_queueing: isQueueing,
      active_video: active?.video_filename || null,
      active_progress: active?.progress_percent ?? null,
      active_frames: active?.progress_frames != null && active?.total_frames
        ? `${active.progress_frames} / ${active.total_frames}`
        : null,
      active_detection: activeDetection,
      summary: {
        processing: summary.processing || 0,
        queued: summary.queued || 0,
        waiting: summary.waiting || 0,
        completed: summary.completed || 0,
        failed: summary.failed || 0,
        cancelled: summary.cancelled || 0,
      },
      items: items.slice(0, 500).map((item: any) => ({
        item_id: item.item_id || item.id,
        video_filename: item.video_filename,
        status: item.status,
        progress_percent: item.progress_percent,
        frames: item.progress_frames != null && item.total_frames
          ? `${item.progress_frames} / ${item.total_frames}`
          : '—',
        details: item.details || item.error_message || null,
        created_at: item.created_at,
        completed_at: item.completed_at || null,
        queued_at: item.queued_at || null,
        date: item.date || null,
      })),
    }, { headers: { 'Cache-Control': 'no-store, max-age=0' } });
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

export async function GET(req: NextRequest) {
  const token = req.cookies.get(AUTH_COOKIE_NAME)?.value;
  if (!verifySessionToken(token)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const localQueue = await getLocalQueueResponse();
  if (localQueue) return localQueue;

  // The queue has a fixed storage path, so fetch it directly instead of relying on listing.
  try {
    const content = await downloadStorageFile('results/queue_status.json');
    const storedData = JSON.parse(await content.text());
    const storedSummary = storedData.summary && typeof storedData.summary === 'object'
      ? storedData.summary
      : {};
    const items = Array.isArray(storedData.items) ? [...storedData.items] : [];
    const summary = {
      processing: Number(storedSummary.processing) || 0,
      queued: Number(storedSummary.queued) || 0,
      waiting: Number(storedSummary.waiting) || 0,
      completed: Number(storedSummary.completed) || 0,
      failed: Number(storedSummary.failed) || 0,
      cancelled: Number(storedSummary.cancelled) || 0,
    };
    let historyWarning: string | null = null;
    try {
      const recoveredHistory = await recoverCompletedHistory(items);
      items.push(...recoveredHistory);
      const completedItems = items.filter(
        (item) => String(item.status || '').toUpperCase() === 'COMPLETED',
      ).length;
      summary.completed = Math.max(summary.completed, completedItems);
    } catch (error) {
      console.error('Failed to recover queue history from synchronized analysis reports:', error);
      historyWarning = 'Some queue history could not be recovered from synchronized analysis reports.';
    }
    const activeAndPendingItems = items.filter((item) =>
      ['PROCESSING', 'QUEUED', 'WAITING'].includes(String(item.status || '').toUpperCase()),
    );
    const historyItems = items
      .filter((item) =>
        !['PROCESSING', 'QUEUED', 'WAITING'].includes(String(item.status || '').toUpperCase()),
      )
      .sort((first, second) =>
        Date.parse(String(second.completed_at || second.created_at || ''))
        - Date.parse(String(first.completed_at || first.created_at || '')),
      );
    const mergedItems = [...activeAndPendingItems, ...historyItems];
    const activeVideo = storedData.active_video || null;
    const activeDetection = storedData.active_detection || null;
    const freshness = getQueueFreshness(
      typeof storedData.updated_at === 'string' ? storedData.updated_at : null,
      activeDetection,
      Boolean(activeVideo || summary.processing > 0),
    );

    if (activeVideo && summary.processing === 0) {
      summary.processing = 1;
    }

    return NextResponse.json({
      ...storedData,
      source: 'supabase',
      updated_at: storedData.updated_at || null,
      ...freshness,
      is_queueing: (summary.processing + summary.queued + summary.waiting) > 0
        || storedData.is_queueing === true,
      active_video: activeVideo,
      active_progress: storedData.active_progress ?? null,
      active_frames: storedData.active_frames ?? null,
      active_detection: activeDetection,
      history_warning: historyWarning,
      summary,
      items: mergedItems.length > 0 || !activeVideo
        ? mergedItems
        : [{
          item_id: 'active-sync',
          video_filename: activeVideo,
          status: 'PROCESSING',
          progress_percent: storedData.active_progress ?? null,
          frames: storedData.active_frames ?? '—',
          details: null,
          created_at: storedData.updated_at || null,
        }],
    }, {
      headers: { 'Cache-Control': 'no-store, max-age=0' },
    });
  } catch (storageError) {
    console.error('Supabase Storage queue status check failed:', storageError);
    if (process.env.VERCEL) {
      return NextResponse.json(
        {
          error: 'Failed to load queue status from Supabase Storage',
          details: storageError instanceof Error ? storageError.message : 'Unknown storage error',
        },
        { status: 503 },
      );
    }
  }

  return NextResponse.json({
    source: 'idle',
    updated_at: null,
    source_stale: true,
    source_age_seconds: null,
    is_queueing: false,
    active_video: null,
    active_progress: null,
    active_frames: null,
    summary: {
      processing: 0,
      queued: 0,
      waiting: 0,
      completed: 0,
      failed: 0,
      cancelled: 0,
    },
    items: [],
  });
}
