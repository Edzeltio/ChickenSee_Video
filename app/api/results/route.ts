import { NextRequest, NextResponse } from 'next/server';
import { AUTH_COOKIE_NAME, verifySessionToken } from '@/lib/auth';
import { downloadStorageFile, listStorageFiles } from '@/lib/supabase-storage';
import fs from 'fs';
import path from 'path';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

interface AnalysisChunk {
  chunk_index: number;
  chunk_start: string;
  chunk_end: string;
  unique_birds_tracked: number;
  avg_birds_in_frame: number;
  feeding_bouts: number;
  feeding_time_total_s: number;
  reduced_eating_events: number;
  reduced_eating_index: number;
  drinking_bouts: number;
  drinking_time_total_s: number;
  inactivity_pct: number;
  inactivity_time_total_s: number;
  lethargic_birds_flagged: number;
  feather_pecking_events: number;
  agitation_score: number;
  flock_mobility_px_s: number;
  temp_min_c: number | null;
  temp_avg_c: number | null;
  temp_max_c: number | null;
  humidity_min_pct: number | null;
  humidity_avg_pct: number | null;
  humidity_max_pct: number | null;
  ammonia_ppm_avg: number | null;
  ammonia_ppm_max: number | null;
  lux_avg: number | null;
  thi_avg: number | null;
  heat_stress_level: string | null;
  sensor_data_available?: boolean;
  sound_avg_db?: number | null;
  sound_db_avg?: number | null;
  bird_records?: BirdRecord[];
}

interface BirdRecord {
  track_id: number;
  bird_label: string;
  bird_type: string;
  feeding_s: number;
  feeding_bouts: number;
  reduced_eating_flag: number;
  drinking_s: number;
  drinking_bouts: number;
  inactivity_s: number;
  is_lethargic: number;
  pecking_aggressor_count: number;
  pecking_victim_count: number;
  avg_velocity_px_s: number;
}

interface AnalysisFile {
  job_id: number;
  source_video: string;
  processed_at: string;
  detected_video: string | null;
  chunks: AnalysisChunk[];
  bird_records?: BirdRecord[][];
}

function parseAnalysisChunks(analysis: AnalysisFile, fileName: string, detectedVideoName: string | null = null) {
  return (analysis.chunks || []).map((chunk, index) => {
    const soundValue = chunk.sound_avg_db ?? chunk.sound_db_avg;
    const sensorValues = [
      chunk.temp_avg_c,
      chunk.temp_min_c,
      chunk.temp_max_c,
      chunk.humidity_avg_pct,
      chunk.humidity_min_pct,
      chunk.humidity_max_pct,
      chunk.ammonia_ppm_avg,
      chunk.ammonia_ppm_max,
      chunk.lux_avg,
      chunk.thi_avg,
      soundValue,
    ];
    const hasSensorMeasurements = sensorValues.some(
      (value) => typeof value === 'number' && Number.isFinite(value) && value !== 0,
    );
    const sensorsAvailable = hasSensorMeasurements
      && chunk.sensor_data_available !== false;
    const sensorValue = (value: number | null | undefined) =>
      sensorsAvailable && typeof value === 'number' && Number.isFinite(value)
        ? value
        : null;

    return {
      result_id: `${fileName}:${chunk.chunk_index}`,
      job_id: analysis.job_id,
      video_file: analysis.source_video,
      interval_label: `${new Date(chunk.chunk_start).toLocaleString()} – ${new Date(chunk.chunk_end).toLocaleTimeString()}`,
      chunk_start: chunk.chunk_start,
      chunk_end: chunk.chunk_end,
      processed_at: analysis.processed_at,
      bird_count: chunk.unique_birds_tracked,
      avg_birds_in_frame: chunk.avg_birds_in_frame,
      eating_count: chunk.feeding_bouts,
      feeding_time_s: chunk.feeding_time_total_s,
      reduced_eating_events: chunk.reduced_eating_events,
      reduced_eating_index_pct: chunk.reduced_eating_index == null
        ? null
        : Number((chunk.reduced_eating_index * 100).toFixed(1)),
      drinking_count: chunk.drinking_bouts,
      drinking_time_s: chunk.drinking_time_total_s,
      inactive_count: chunk.lethargic_birds_flagged,
      inactivity_pct: chunk.inactivity_pct,
      inactivity_time_s: chunk.inactivity_time_total_s,
      feather_pecking_count: chunk.feather_pecking_events,
      agitation_score: chunk.agitation_score,
      avg_velocity: chunk.flock_mobility_px_s,
      temp_avg: sensorValue(chunk.temp_avg_c),
      temp_min: sensorValue(chunk.temp_min_c),
      temp_max: sensorValue(chunk.temp_max_c),
      humidity_avg: sensorValue(chunk.humidity_avg_pct),
      humidity_min: sensorValue(chunk.humidity_min_pct),
      humidity_max: sensorValue(chunk.humidity_max_pct),
      ammonia_avg: sensorValue(chunk.ammonia_ppm_avg),
      ammonia_max: sensorValue(chunk.ammonia_ppm_max),
      lux_avg: sensorValue(chunk.lux_avg),
      thi_avg: sensorValue(chunk.thi_avg),
      sound_avg_db: sensorValue(soundValue),
      heat_stress_level: sensorsAvailable
        ? chunk.heat_stress_level
        : 'Unavailable',
      sensor_data_available: sensorsAvailable,
      bird_records: chunk.bird_records ?? analysis.bird_records?.[index] ?? [],
      result_file: fileName,
      detected_video: detectedVideoName,
    };
  });
}

function resolveLocalStorageRoot(): string {
  const configured = process.env.CHICKENSEE_LOCAL_STORAGE?.trim();
  if (configured) return configured;
  return path.join(process.cwd(), 'local-storage');
}

function loadLocalResults(): any[] {
  const candidateDirs = [
    path.join(resolveLocalStorageRoot(), 'results'),
    '/tmp/results',
    path.join(process.cwd(), 'data', 'results'),
  ];

  const filesFound = new Map<string, string>(); // filename -> fullPath
  for (const dir of candidateDirs) {
    try {
      if (fs.existsSync(dir)) {
        const entries = fs.readdirSync(dir);
        for (const entry of entries) {
          if (entry.toLowerCase().endsWith('.json') && !entry.includes('queue_status')) {
            if (!filesFound.has(entry)) {
              filesFound.set(entry, path.join(dir, entry));
            }
          }
        }
      }
    } catch {
      // Continue to next directory
    }
  }

  const allChunks: any[] = [];
  filesFound.forEach((fullPath, filename) => {
    try {
      const raw = fs.readFileSync(fullPath, 'utf-8');
      const analysis = JSON.parse(raw) as AnalysisFile;
      const expectedVideoName = analysis.source_video?.replace(/\.[^.]+$/, '') + '_detected.mp4';
      const chunks = parseAnalysisChunks(analysis, filename, expectedVideoName);
      allChunks.push(...chunks);
    } catch (parseErr) {
      console.warn(`Failed to parse local result file ${filename}:`, parseErr);
    }
  });

  return allChunks;
}

export async function GET(req: NextRequest) {
  const token = req.cookies.get(AUTH_COOKIE_NAME)?.value;
  if (!verifySessionToken(token)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  if (!process.env.VERCEL) {
    try {
      const localResults = loadLocalResults();
      if (localResults.length > 0) {
        localResults.sort(
          (first, second) =>
            new Date(second.chunk_start).getTime() - new Date(first.chunk_start).getTime(),
        );
        return NextResponse.json({ results: localResults });
      }
    } catch (error) {
      console.error('Failed to load local analysis results:', error);
    }
  }

  // 1. Try Supabase Storage first
  try {
    const resultFiles = await listStorageFiles('results');
    const resultObjects = resultFiles
      .filter((file) => file.pathname.toLowerCase().endsWith('.json') && file.pathname !== 'queue_status.json')
      .sort((a, b) => new Date(b.uploadedAt).getTime() - new Date(a.uploadedAt).getTime())
      .slice(0, 50);

    if (resultObjects.length > 0) {
      const results = await Promise.all(resultObjects.map(async (file) => {
        const content = await downloadStorageFile(file.storagePath);
        const analysis = JSON.parse(await content.text()) as AnalysisFile;
        const fileName = file.pathname;
        return parseAnalysisChunks(analysis, fileName);
      }));

      const flattenedResults = results.flat().sort(
        (first, second) =>
          new Date(second.chunk_start).getTime() - new Date(first.chunk_start).getTime(),
      );

      return NextResponse.json({ results: flattenedResults });
    }
  } catch (error) {
    console.error('Supabase Storage results check failed:', error);
    return NextResponse.json(
      { error: 'Failed to load analysis results from Supabase Storage' },
      { status: 503 },
    );
  }

  if (process.env.VERCEL) {
    return NextResponse.json({ results: [] });
  }

  // 2. Fallback to bundled / local results
  try {
    const localResults = loadLocalResults();
    localResults.sort(
      (first, second) =>
        new Date(second.chunk_start).getTime() - new Date(first.chunk_start).getTime(),
    );

    return NextResponse.json({ results: localResults });
  } catch (fallbackError) {
    console.error('Failed to load fallback analysis results:', fallbackError);
    return NextResponse.json({ results: [] });
  }
}
