import { NextRequest, NextResponse } from 'next/server';
import { AUTH_COOKIE_NAME, verifySessionToken } from '@/lib/auth';
import { downloadStorageFile, listSyncedDatabases } from '@/lib/supabase-storage';
import { openDatabaseFromBytes } from '@/lib/db-viewer';
import type { Database, SqlValue } from 'sql.js';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

interface SensorPoint {
  timestamp: string;
  elapsedSeconds: number | null;
  temperature: number | null;
  humidity: number | null;
  ammonia: number | null;
  sound: number | null;
}

interface BehaviorEvent {
  timestamp: string;
  behavior: string;
}

interface BirdTimeline {
  name: string;
  events: BehaviorEvent[];
}

function normalizeVideoName(value: string): string {
  const basename = value.split(/[\\/]/).pop() || value;
  return basename
    .replace(/\.[^.]+$/, '')
    .replace(/_(?:detected|annotated)$/i, '')
    .toLowerCase();
}

function tableColumns(db: Database, table: string): Set<string> {
  const rows = db.exec(`PRAGMA table_info("${table}");`)[0]?.values ?? [];
  return new Set(rows.map((row) => String(row[1])));
}

function numericValue(value: SqlValue | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function textValue(value: SqlValue | undefined): string {
  return typeof value === 'string' ? value : '';
}

function getMatchedSensorPoints(db: Database, videoName: string): SensorPoint[] | null {
  const tables = new Set(
    (db.exec("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")[0]?.values ?? [])
      .map((row) => String(row[0])),
  );
  if (!tables.has('recording') || !tables.has('sensor_logs')) return null;
  if (!tableColumns(db, 'recording').has('video_file')) return null;

  const recordings = db.exec('SELECT video_file FROM recording;')[0]?.values ?? [];
  if (!recordings.some((row) => normalizeVideoName(textValue(row[0])) === videoName)) return null;

  const required = ['elapsed_seconds', 'video_time', 'captured_at', 'temperature', 'humidity', 'ammonia_ppm', 'sound_db'];
  if (!required.every((column) => tableColumns(db, 'sensor_logs').has(column))) return [];

  const rows = db.exec(
    `SELECT video_time, captured_at, elapsed_seconds, temperature, humidity, ammonia_ppm, sound_db
     FROM sensor_logs ORDER BY elapsed_seconds ASC LIMIT 5000;`,
  )[0]?.values ?? [];

  return rows.map((row) => ({
    timestamp: textValue(row[0]) || textValue(row[1]),
    elapsedSeconds: numericValue(row[2]),
    temperature: numericValue(row[3]),
    humidity: numericValue(row[4]),
    ammonia: numericValue(row[5]),
    sound: numericValue(row[6]),
  }));
}

function getMatchedBehavior(
  db: Database,
  videoName: string,
): { matched: boolean; birds: BirdTimeline[] } {
  const tables = new Set(
    (db.exec("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")[0]?.values ?? [])
      .map((row) => String(row[0])),
  );
  if (!tables.has('processing_jobs') || !tables.has('half_hour_metrics') || !tables.has('bird_track_metrics')) {
    return { matched: false, birds: [] };
  }

  const jobColumns = tableColumns(db, 'processing_jobs');
  const metricColumns = tableColumns(db, 'half_hour_metrics');
  const birdColumns = tableColumns(db, 'bird_track_metrics');
  if (
    !jobColumns.has('id') ||
    !jobColumns.has('video_filename') ||
    !metricColumns.has('id') ||
    !metricColumns.has('job_id') ||
    !metricColumns.has('chunk_start') ||
    !birdColumns.has('chunk_metric_id') ||
    !birdColumns.has('track_id') ||
    !birdColumns.has('bird_label') ||
    !birdColumns.has('reduced_eating_flag') ||
    !birdColumns.has('is_lethargic') ||
    !birdColumns.has('pecking_aggressor_count') ||
    !birdColumns.has('pecking_victim_count')
  ) {
    return { matched: false, birds: [] };
  }

  const jobs = db.exec('SELECT id, video_filename FROM processing_jobs;')[0]?.values ?? [];
  const matchedJobs = jobs.filter((row) => normalizeVideoName(textValue(row[1])) === videoName);
  if (!matchedJobs.length) return { matched: false, birds: [] };

  const birdMap = new Map<string, BirdTimeline>();
  for (const job of matchedJobs) {
    const jobId = numericValue(job[0]);
    if (jobId === null || !Number.isSafeInteger(jobId)) continue;

    const statement = db.prepare(
      `SELECT m.chunk_start, b.track_id, b.bird_label, b.reduced_eating_flag, b.is_lethargic,
              b.pecking_aggressor_count, b.pecking_victim_count
       FROM bird_track_metrics b
       JOIN half_hour_metrics m ON m.id = b.chunk_metric_id
       WHERE m.job_id = ?
       ORDER BY m.chunk_start ASC;`,
    );
    statement.bind([jobId]);
    try {
      while (statement.step()) {
        const row = statement.get();
        const trackId = typeof row[1] === 'number' || typeof row[1] === 'string' ? row[1] : 'unknown';
        const label = textValue(row[2]) || `Bird ${trackId}`;
        let bird = birdMap.get(label);
        if (!bird) {
          bird = { name: label, events: [] };
          birdMap.set(label, bird);
        }

        const timestamp = textValue(row[0]);
        const flags = [
          [numericValue(row[3]), 'Reduced eating'],
          [numericValue(row[4]), 'Lethargy'],
          [
            (numericValue(row[5]) ?? 0) + (numericValue(row[6]) ?? 0),
            'Feather pecking',
          ],
        ] as const;
        for (const [count, behavior] of flags) {
          if (count !== null && count > 0 && timestamp) {
            bird.events.push({ timestamp, behavior });
          }
        }
      }
    } finally {
      statement.free();
    }
  }

  return { matched: true, birds: Array.from(birdMap.values()) };
}

export async function GET(req: NextRequest) {
  const token = req.cookies.get(AUTH_COOKIE_NAME)?.value;
  if (!verifySessionToken(token)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const requestedVideo = new URL(req.url).searchParams.get('video')?.trim();
  if (!requestedVideo) {
    return NextResponse.json({ error: 'A video filename is required' }, { status: 400 });
  }

  try {
    const target = normalizeVideoName(requestedVideo);
    const databases = await listSyncedDatabases();
    const matchedDatabases: string[] = [];
    const sensors: SensorPoint[] = [];
    const birds = new Map<string, BirdTimeline>();
    let behaviorAvailable = false;

    for (const file of databases) {
      const content = await downloadStorageFile(file.storagePath);
      let db: Database;
      try {
        db = await openDatabaseFromBytes(await content.arrayBuffer());
      } catch (error) {
        console.warn(`Skipping unreadable SQLite database "${file.pathname}":`, error);
        continue;
      }

      try {
        const sensorPoints = getMatchedSensorPoints(db, target);
        const behaviorData = getMatchedBehavior(db, target);
        if (sensorPoints !== null || behaviorData.matched) {
          matchedDatabases.push(file.pathname);
        }
        behaviorAvailable ||= behaviorData.matched;
        if (sensorPoints) sensors.push(...sensorPoints);
        for (const bird of behaviorData.birds) {
          const existing = birds.get(bird.name);
          if (existing) existing.events.push(...bird.events);
          else birds.set(bird.name, bird);
        }
      } finally {
        db.close();
      }
    }

    return NextResponse.json({
      video: requestedVideo,
      pairedDatabases: matchedDatabases,
      sensors,
      behaviorAvailable,
      birds: Array.from(birds.values()).map((bird) => ({
        ...bird,
        events: bird.events.sort(
          (first, second) => new Date(first.timestamp).getTime() - new Date(second.timestamp).getTime(),
        ),
      })),
    });
  } catch (error) {
    console.error('Failed to load video-linked SQL analysis:', error);
    return NextResponse.json(
      { error: 'Failed to load SQL analysis for this video' },
      { status: 500 },
    );
  }
}
