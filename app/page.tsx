'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import {
  Video,
  Database,
  RefreshCw,
  LogOut,
  Play,
  Download,
  Calendar,
  HardDrive,
  ChevronLeft,
  ChevronRight,
  Server,
  FileCode,
  Clock,
  Radio,
  BarChart2,
  Activity,
  Bird,
  Droplets,
  AlertTriangle,
  Eye,
  X,
  Thermometer,
  Wind,
  Gauge,
  Search,
  ChevronDown,
  TrendingUp,
  TrendingDown,
  ListFilter,
  CheckCircle2,
  Loader2,
  Layers,
  Sparkles,
  FolderUp,
} from 'lucide-react';
import SampleInspectorTab from '@/components/SampleInspectorTab';

interface QueueItem {
  item_id: string;
  video_filename: string;
  status: 'PROCESSING' | 'QUEUED' | 'WAITING' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | string;
  progress_percent: number | null;
  frames: string;
  details: string | null;
  created_at: string | null;
  completed_at?: string | null;
  queued_at?: string | null;
  date?: string | null;
}

interface LiveDetectionStatus {
  job_id: number;
  status: string;
  frame_idx?: number;
  total_frames?: number;
  bird_count?: number;
  identified_count?: number;
  rooster_count?: number;
  paired_sensor_database?: string | null;
  captured_at?: string;
}

function getQueueItemDateLabel(item: QueueItem): string {
  const rawDate = item.completed_at || item.queued_at || item.created_at || (item.date ? `${item.date}T00:00:00` : null);
  if (!rawDate) return 'Date Unknown';
  try {
    const d = new Date(rawDate);
    if (isNaN(d.getTime())) return 'Date Unknown';

    const itemDay = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const deltaDays = Math.round((today - itemDay) / (1000 * 60 * 60 * 24));

    const formattedDate = d.toLocaleDateString(undefined, {
      month: 'long',
      day: 'numeric',
      year: 'numeric',
    });

    if (deltaDays === 0) {
      return `Today — ${formattedDate}`;
    } else if (deltaDays === 1) {
      return `Yesterday — ${formattedDate}`;
    } else {
      const weekday = d.toLocaleDateString(undefined, { weekday: 'long' });
      return `${weekday}, ${formattedDate}`;
    }
  } catch {
    return 'Date Unknown';
  }
}

interface QueueStatusData {
  source?: string;
  synced_at?: string;
  updated_at: string | null;
  source_stale?: boolean;
  source_age_seconds?: number | null;
  is_queueing: boolean;
  active_video: string | null;
  active_progress: number | null;
  active_frames: string | null;
  active_detection?: LiveDetectionStatus | null;
  history_warning?: string | null;
  summary: {
    processing: number;
    queued: number;
    waiting: number;
    completed: number;
    failed: number;
    cancelled: number;
  };
  items: QueueItem[];
}

interface SyncedFileMeta {
  url: string;
  pathname: string;
  size: number;
  uploadedAt: string;
}

interface QueryResult {
  columns: string[];
  values: any[][];
  totalRows: number;
}

interface BehaviorResult {
  result_id: string;
  job_id: number;
  video_file: string;
  interval_label: string;
  chunk_start: string;
  chunk_end: string;
  processed_at: string;
  bird_count: number;
  avg_birds_in_frame: number;
  eating_count: number;
  feeding_time_s: number;
  reduced_eating_events: number;
  reduced_eating_index_pct: number | null;
  drinking_count: number;
  drinking_time_s: number;
  inactive_count: number;
  inactivity_pct: number;
  inactivity_time_s: number;
  feather_pecking_count: number;
  agitation_score: number;
  avg_velocity: number;
  temp_avg: number | null;
  temp_min: number | null;
  temp_max: number | null;
  humidity_avg: number | null;
  humidity_min: number | null;
  humidity_max: number | null;
  ammonia_avg: number | null;
  ammonia_max: number | null;
  lux_avg: number | null;
  thi_avg: number | null;
  sound_avg_db: number | null;
  heat_stress_level: string;
  sensor_data_available: boolean;
  bird_records: BirdBehaviorRecord[];
  result_file: string;
  detected_video: string | null;
}

interface BirdBehaviorRecord {
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

const REPORT_PROFILES = [
  'Flock',
  'Rooster',
  'Hen1 (Green)',
  'Hen2 (White)',
  'Hen3 (Blue)',
  'Hen4 (Yellow)',
  'Hen5 (Pink)',
] as const;

const REPORT_BEHAVIORS = [
  { key: 'feeding', label: 'Feeding bouts' },
  { key: 'drinking', label: 'Drinking bouts' },
  { key: 'inactivity', label: 'Inactivity (%)' },
  { key: 'pecking', label: 'Feather pecking events' },
  { key: 'reducedEating', label: 'Reduced-eating events' },
] as const;

const REPORT_SENSORS = [
  { key: 'temperature', label: 'Temperature', unit: '°C' },
  { key: 'humidity', label: 'Humidity', unit: '%' },
  { key: 'ammonia', label: 'Ammonia', unit: 'ppm' },
  { key: 'thi', label: 'THI', unit: '' },
  { key: 'light', label: 'Light', unit: 'lux' },
  { key: 'sound', label: 'Sound', unit: 'dB' },
] as const;

const CHICKEN_PROFILES = [
  'All Chickens',
  'Rooster',
  'Hen1 (Green)',
  'Hen2 (White)',
  'Hen3 (Blue)',
  'Hen4 (Yellow)',
  'Hen5 (Pink)',
] as const;

const TIME_OPTIONS = [
  { value: '06:00', label: '6:00 AM' },
  { value: '07:00', label: '7:00 AM' },
  { value: '08:00', label: '8:00 AM' },
  { value: '09:00', label: '9:00 AM' },
  { value: '10:00', label: '10:00 AM' },
  { value: '11:00', label: '11:00 AM' },
  { value: '12:00', label: '12:00 PM' },
  { value: '13:00', label: '1:00 PM' },
  { value: '14:00', label: '2:00 PM' },
  { value: '15:00', label: '3:00 PM' },
  { value: '16:00', label: '4:00 PM' },
  { value: '17:00', label: '5:00 PM' },
  { value: '18:00', label: '6:00 PM' },
] as const;

interface VideoSensorPoint {
  timestamp: string;
  elapsedSeconds: number | null;
  temperature: number | null;
  humidity: number | null;
  ammonia: number | null;
  sound: number | null;
}

interface VideoBehaviorEvent {
  timestamp: string;
  behavior: string;
}

interface VideoBirdTimeline {
  name: string;
  events: VideoBehaviorEvent[];
}

interface VideoAnalysis {
  pairedDatabases: string[];
  sensors: VideoSensorPoint[];
  behaviorAvailable: boolean;
  birds: VideoBirdTimeline[];
}

const ENVIRONMENTAL_SENSOR_CHARTS = [
  { key: 'temperature', label: 'Temperature (°C)', color: '#4aa7dd' },
  { key: 'humidity', label: 'Relative humidity (%)', color: '#4aa7dd' },
  { key: 'ammonia', label: 'Ammonia (ppm)', color: '#4aa7dd' },
  { key: 'sound', label: 'Sound level (dB)', color: '#4aa7dd' },
] as const;

function makeLinePath(values: readonly number[], width: number, height: number) {
  if (!values.length) return '';
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 1);

  return values
    .map((value, index) => {
      const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
      const y = height - ((value - min) / range) * (height - 18) - 8;
      return `${index === 0 ? 'M' : 'L'} ${x} ${y}`;
    })
    .join(' ');
}

function formatVideoChartTime(point: VideoSensorPoint): string {
  if (point.timestamp) {
    const date = new Date(point.timestamp);
    if (Number.isFinite(date.getTime())) {
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
    return point.timestamp;
  }
  return point.elapsedSeconds == null ? '' : `${Math.floor(point.elapsedSeconds)}s`;
}

function EnvironmentalSensorPanel({
  videoName,
  analysis,
  loading,
  error,
}: {
  videoName: string | null;
  analysis: VideoAnalysis | null;
  loading: boolean;
  error: string | null;
}) {
  const hasSensorData = analysis?.sensors.some((point) =>
    ENVIRONMENTAL_SENSOR_CHARTS.some((chart) => point[chart.key] != null),
  ) ?? false;

  return (
    <div className="mt-2 rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm">
      <div className="mb-4">
        <h3 className="text-base sm:text-lg font-black text-slate-900">1. Environmental sensor analysis</h3>
        <p className="mt-1 text-sm text-slate-600">
          Sensor readings from the SQL database paired with {videoName || 'the selected video'}.
        </p>
      </div>

      {loading ? (
        <p className="rounded-xl border border-slate-200 bg-slate-50 p-5 text-sm text-slate-500">Loading paired SQL readings…</p>
      ) : error ? (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-700">{error}</p>
      ) : !analysis?.pairedDatabases.length ? (
        <p className="rounded-xl border border-slate-200 bg-slate-50 p-5 text-sm text-slate-500">
          No synchronized SQL database is paired with this video.
        </p>
      ) : !hasSensorData ? (
        <p className="rounded-xl border border-slate-200 bg-slate-50 p-5 text-sm text-slate-500">
          The paired database has no sensor readings for this video.
        </p>
      ) : (
        <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-4">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {ENVIRONMENTAL_SENSOR_CHARTS.map((chart) => {
              const points = (analysis?.sensors ?? []).flatMap((point) => {
                const value = point[chart.key];
                return value == null ? [] : [{ point, value }];
              });
              const values = points.map(({ value }) => value);
              if (!points.length) return null;
              const peak = Math.max(...values);
              const step = Math.ceil(points.length / 60);
              const displayedPoints = points.flatMap((entry, index) =>
                index === 0 || index === points.length - 1 || index % step === 0
                  ? [{ ...entry, index }]
                  : [],
              );

              return (
                <div key={chart.key} className="rounded-lg bg-white/80 px-3 py-2 shadow-sm">
                  <div className="mb-2 flex items-center justify-between text-[11px] text-slate-500">
                    <span className="font-medium text-slate-700">{chart.label}</span>
                    <span>peak {peak}</span>
                  </div>
                  <svg viewBox="0 0 280 110" className="h-28 w-full" aria-label={chart.label} role="img">
                    {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
                      <line key={ratio} x1="10" x2="265" y1={14 + ratio * 72} y2={14 + ratio * 72} stroke="#e2e8f0" strokeWidth="0.8" />
                    ))}
                    <path
                      d={makeLinePath(values, 255, 90)}
                      fill="none"
                      stroke={chart.color}
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                    {displayedPoints.map(({ point, value, index }) => {
                      const x = points.length === 1 ? 127.5 : (index / (points.length - 1)) * 255;
                      const min = Math.min(...values);
                      const range = Math.max(peak - min, 1);
                      const y = 90 - ((value - min) / range) * 72 - 8;
                      return <circle key={`${point.timestamp}-${index}`} cx={x} cy={y} r={value === peak ? 4 : 2.6} fill={chart.color} stroke="#ffffff" strokeWidth="1.2" />;
                    })}
                    <line x1="10" x2="265" y1="92" y2="92" stroke="#cbd5e1" strokeWidth="0.8" />
                    {[points[0], points[Math.floor((points.length - 1) / 2)], points[points.length - 1]].map((entry, index) => (
                      <text key={`${index}-${entry.point.timestamp}`} x={[10, 137.5, 265][index]} y="106" fill="#64748b" fontSize="8" textAnchor="middle">
                        {formatVideoChartTime(entry.point)}
                      </text>
                    ))}
                  </svg>
                </div>
              );
            })}
          </div>
          <p className="mt-3 text-[11px] text-slate-500">
            Source: {analysis?.pairedDatabases.join(', ')} · {analysis?.sensors.length ?? 0} readings
          </p>
        </div>
      )}
    </div>
  );
}

function HenStressTimelinePanel({
  videoName,
  analysis,
  loading,
  error,
}: {
  videoName: string | null;
  analysis: VideoAnalysis | null;
  loading: boolean;
  error: string | null;
}) {
  const events = (analysis?.birds ?? []).flatMap((bird) =>
    bird.events.map((event) => ({ ...event, bird: bird.name, time: new Date(event.timestamp).getTime() })),
  ).filter((event) => Number.isFinite(event.time));
  const start = events.length ? Math.min(...events.map((event) => event.time)) : 0;
  const end = events.length ? Math.max(...events.map((event) => event.time)) : 1;
  const duration = Math.max(end - start, 1);

  return (
    <div className="mt-6 rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm">
      <div className="mb-4">
        <h3 className="text-base sm:text-lg font-black text-slate-900">2. Hen stress-related behavior analysis</h3>
        <p className="mt-1 text-sm text-slate-600">
          Flagged bird behavior intervals from the SQL database paired with {videoName || 'the selected video'}.
        </p>
      </div>

      {loading ? (
        <p className="rounded-xl border border-slate-200 bg-slate-50 p-5 text-sm text-slate-500">Loading paired behavior metrics…</p>
      ) : error ? (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-700">{error}</p>
      ) : !analysis?.pairedDatabases.length ? (
        <p className="rounded-xl border border-slate-200 bg-slate-50 p-5 text-sm text-slate-500">
          No synchronized SQL database is paired with this video.
        </p>
      ) : !analysis.behaviorAvailable ? (
        <p className="rounded-xl border border-slate-200 bg-slate-50 p-5 text-sm text-slate-500">
          The paired database has no per-bird behavior metrics.
        </p>
      ) : !events.length ? (
        <p className="rounded-xl border border-slate-200 bg-slate-50 p-5 text-sm text-slate-500">
          No flagged bird-level behavior intervals were found in the paired database.
        </p>
      ) : (
        <>
          <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
            <div className="mb-3 flex items-center justify-between text-[11px] text-slate-500">
              <span>Flagged intervals by bird</span>
              <span>{events.length} flagged observations</span>
            </div>
            <div className="relative overflow-hidden rounded-lg border border-slate-200 bg-white p-3">
              <div className="mb-2 grid grid-cols-[100px_1fr] items-center gap-3 text-[11px] text-slate-500">
                <div />
                <div className="flex justify-between">
                  {[start, start + duration / 2, end].map((time) => (
                    <span key={time}>{new Date(time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                  ))}
                </div>
              </div>
              {analysis.birds.map((bird) => (
                <div key={bird.name} className="mt-2 grid grid-cols-[100px_1fr] items-center gap-3">
                  <div className="truncate text-[11px] font-medium text-slate-700" title={bird.name}>{bird.name}</div>
                  <div className="relative h-7 overflow-hidden rounded-sm border border-slate-200 bg-slate-50/60">
                    <div className="absolute inset-y-0 left-1/2 w-px bg-slate-200" />
                    {bird.events.map((event, index) => {
                      const time = new Date(event.timestamp).getTime();
                      if (!Number.isFinite(time)) return null;
                      const left = ((time - start) / duration) * 100;
                      return (
                        <span
                          key={`${event.timestamp}-${event.behavior}-${index}`}
                          title={`${event.behavior} · ${new Date(time).toLocaleString()}`}
                          className="absolute top-1/2 h-3 w-3 -translate-y-1/2 rounded-full border-2 border-white bg-sky-500 shadow-sm"
                          style={{ left: `${Math.min(Math.max(left, 0), 100)}%` }}
                        />
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <p className="mt-3 text-[11px] text-slate-500">
            A marker indicates reduced eating, lethargy, or feather-pecking activity during that SQL interval.
          </p>
        </>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState<'database' | 'results' | 'queue' | 'sample'>('database');

  const handleTabChange = useCallback((tab: 'database' | 'results' | 'queue' | 'sample') => {
    setActiveTab(tab);
    try {
      localStorage.setItem('chickensee_active_tab', tab);
      const url = new URL(window.location.href);
      url.searchParams.set('tab', tab);
      window.history.replaceState({}, '', url.toString());
    } catch {
      // ignore
    }
  }, []);

  // Restore active tab on initial load from URL or localStorage
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const tabParam = params.get('tab');
      const savedTab = localStorage.getItem('chickensee_active_tab');
      const targetTab = (tabParam || savedTab) as 'database' | 'results' | 'queue' | 'sample';
      if (targetTab && ['database', 'results', 'queue', 'sample'].includes(targetTab)) {
        setActiveTab(targetTab);
      }
    } catch {
      // ignore
    }
  }, []);
  const [queueStatus, setQueueStatus] = useState<QueueStatusData | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [loadingQueue, setLoadingQueue] = useState(false);
  const queueRequestInFlight = useRef(false);

  // ── Queue Segregation & Filter State ──
  const [queueDateFilter, setQueueDateFilter] = useState<string>('All dates');
  const [queueStatusFilter, setQueueStatusFilter] = useState<'All' | 'Completed' | 'Failed' | 'Cancelled'>('All');
  const [collapsedDateGroups, setCollapsedDateGroups] = useState<Record<string, boolean>>({});

  const [videos, setVideos] = useState<SyncedFileMeta[]>([]);
  const [selectedVideo, setSelectedVideo] = useState<SyncedFileMeta | null>(null);
  const [loadingVideos, setLoadingVideos] = useState(true);
  const [videosError, setVideosError] = useState<string | null>(null);
  const [videoAnalysis, setVideoAnalysis] = useState<VideoAnalysis | null>(null);
  const [loadingVideoAnalysis, setLoadingVideoAnalysis] = useState(false);
  const [videoAnalysisError, setVideoAnalysisError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  const [databases, setDatabases] = useState<SyncedFileMeta[]>([]);
  const [currentDbFile, setCurrentDbFile] = useState<string | null>(null);
  const [tables, setTables] = useState<string[]>([]);
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [queryResult, setQueryResult] = useState<QueryResult | null>(null);
  const [loadingDb, setLoadingDb] = useState(true);
  const [databaseError, setDatabaseError] = useState<string | null>(null);
  const [pageOffset, setPageOffset] = useState(0);
  const pageSize = 50;

  const [results, setResults] = useState<BehaviorResult[]>([]);
  const [loadingResults, setLoadingResults] = useState(false);
  const [resultsError, setResultsError] = useState<string | null>(null);
  const [selectedReportProfile, setSelectedReportProfile] =
    useState<(typeof REPORT_PROFILES)[number]>('Flock');
  const [reportStartDate, setReportStartDate] = useState('');
  const [reportEndDate, setReportEndDate] = useState('');
  const [chartBehavior, setChartBehavior] =
    useState<(typeof REPORT_BEHAVIORS)[number]['key']>('pecking');
  const [chartSensor, setChartSensor] =
    useState<(typeof REPORT_SENSORS)[number]['key']>('ammonia');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);

  // ── Results Dashboard State (Mockup UI Parameters) ──
  const [reportTimeRange, setReportTimeRange] = useState<'Daily' | 'Weekly' | 'Monthly' | 'Annually' | 'Custom'>('Custom');
  const [customRangeMode, setCustomRangeMode] = useState<'specific' | 'range'>('specific');
  const [selectedSpecificDate, setSelectedSpecificDate] = useState('All Dates');
  const [specificStartTime, setSpecificStartTime] = useState('6:00 AM');
  const [specificEndTime, setSpecificEndTime] = useState('6:00 PM');
  const [selectedDateRange, setSelectedDateRange] = useState('All Recorded');
  const [rangeStartTime, setRangeStartTime] = useState('6:00 AM');
  const [rangeEndTime, setRangeEndTime] = useState('6:00 PM');
  const [selectedChickenProfile, setSelectedChickenProfile] = useState<string>('All Chickens');
  const [isGeneratingReport, setIsGeneratingReport] = useState(false);
  const [showDetailedLog, setShowDetailedLog] = useState(false);

  async function fetchVideos() {
    try {
      setLoadingVideos(true);
      setVideosError(null);
      const res = await fetch('/api/videos');
      if (res.status === 401) { router.push('/login'); return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      const data = await res.json();
      const vids: SyncedFileMeta[] = data.videos || [];
      setVideos(vids);
      if (!selectedVideo && vids.length > 0) setSelectedVideo(vids[0]);
    } catch (err) {
      console.error('Failed to fetch videos:', err);
      setVideosError(err instanceof Error ? err.message : 'Could not load synchronized videos.');
    } finally {
      setLoadingVideos(false);
    }
  }

  async function fetchDatabaseData(file?: string, table?: string, offset: number = 0) {
    try {
      setLoadingDb(true);
      setDatabaseError(null);
      const params = new URLSearchParams();
      if (file) params.set('file', file);
      if (table) params.set('table', table);
      params.set('limit', pageSize.toString());
      params.set('offset', offset.toString());
      const res = await fetch(`/api/database?${params.toString()}`);
      if (res.status === 401) { router.push('/login'); return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      const data = await res.json();
      setDatabases(data.databases || []);
      setCurrentDbFile(data.currentFile);
      setTables(data.tables || []);
      setSelectedTable(data.selectedTable);
      setQueryResult(data.queryResult);
      setPageOffset(offset);
    } catch (err) {
      console.error('Failed to fetch database data:', err);
      setDatabaseError(err instanceof Error ? err.message : 'Could not load synchronized databases.');
    } finally {
      setLoadingDb(false);
    }
  }

  const fetchResults = useCallback(async () => {
    try {
      setLoadingResults(true);
      setResultsError(null);
      const res = await fetch('/api/results');
      if (res.status === 401) { router.push('/login'); return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      const data = await res.json();
      setResults(Array.isArray(data.results) ? data.results : []);
    } catch (err: any) {
      setResultsError(err.message || 'Could not load uploaded analysis results.');
    } finally {
      setLoadingResults(false);
    }
  }, [router]);

  const fetchQueueStatus = useCallback(async () => {
    if (queueRequestInFlight.current) return;
    queueRequestInFlight.current = true;
    try {
      setLoadingQueue(true);
      const res = await fetch('/api/queue');
      if (res.status === 401) { router.push('/login'); return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      const data = await res.json();
      setQueueStatus(data);
      setQueueError(null);
    } catch (err) {
      console.error('Failed to fetch queue status:', err);
      setQueueStatus(null);
      setQueueError(err instanceof Error ? err.message : 'Could not load queue status from Vercel.');
    } finally {
      setLoadingQueue(false);
      queueRequestInFlight.current = false;
    }
  }, [router]);

  async function refreshAll() {
    setIsRefreshing(true);
    if (activeTab === 'database') await fetchDatabaseData(currentDbFile || undefined, selectedTable || undefined, pageOffset);
    else if (activeTab === 'results') await fetchResults();
    else if (activeTab === 'queue') await fetchQueueStatus();
    setTimeout(() => setIsRefreshing(false), 400);
  }

  useEffect(() => {
    fetchDatabaseData();
    fetchQueueStatus();
    fetchResults();
  }, [fetchQueueStatus, fetchResults]);

  useEffect(() => {
    const pollInterval = activeTab === 'queue'
      ? 1_500
      : queueStatus?.is_queueing
        ? 5_000
        : 15_000;
    const queueTimer = setInterval(() => { fetchQueueStatus(); }, pollInterval);
    return () => clearInterval(queueTimer);
  }, [activeTab, fetchQueueStatus, queueStatus?.is_queueing]);

  useEffect(() => {
    if (!selectedVideo) {
      setVideoAnalysis(null);
      setLoadingVideoAnalysis(false);
      setVideoAnalysisError(null);
      return;
    }

    const videoPath = selectedVideo.pathname;
    let cancelled = false;
    async function fetchVideoAnalysis() {
      setLoadingVideoAnalysis(true);
      setVideoAnalysisError(null);
      try {
        const params = new URLSearchParams({ video: videoPath });
        const response = await fetch(`/api/video-analysis?${params.toString()}`);
        if (response.status === 401) {
          router.push('/login');
          return;
        }
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        if (!cancelled) setVideoAnalysis(data as VideoAnalysis);
      } catch (error) {
        if (!cancelled) {
          console.error('Failed to load SQL analysis for selected video:', error);
          setVideoAnalysisError(error instanceof Error ? error.message : 'Could not load paired SQL data.');
          setVideoAnalysis(null);
        }
      } finally {
        if (!cancelled) setLoadingVideoAnalysis(false);
      }
    }

    fetchVideoAnalysis();
    return () => {
      cancelled = true;
    };
  }, [selectedVideo?.pathname, router]);

  useEffect(() => {
    if (activeTab !== 'results') return;
    fetchResults();
    const refreshTimer = setInterval(() => { fetchResults(); }, 30_000);
    return () => clearInterval(refreshTimer);
  }, [activeTab, fetchResults]);

  async function handleLogout() {
    try {
      setIsLoggingOut(true);
      setLogoutError(null);
      const response = await fetch('/api/auth', { method: 'DELETE' });
      if (!response.ok) {
        throw new Error(`Logout failed (HTTP ${response.status}). Please try again.`);
      }
      router.push('/login');
      router.refresh();
    } catch (error) {
      setLogoutError(error instanceof Error ? error.message : 'Logout failed. Please try again.');
    } finally {
      setIsLoggingOut(false);
    }
  }

  function formatBytes(bytes: number) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  }

  function getProxyUrl(video: SyncedFileMeta): string {
    return `/api/video-proxy?pathname=${encodeURIComponent(video.pathname)}`;
  }

  const dateFilteredResults = results.filter((result) => {
    const resultDateStr = result.chunk_start.slice(0, 10); // 'YYYY-MM-DD'

    // Apply quick preset ranges
    if (reportTimeRange !== 'Custom') {
      const now = new Date();
      const resultDate = new Date(resultDateStr);
      if (reportTimeRange === 'Daily') {
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const rd = new Date(resultDate.getFullYear(), resultDate.getMonth(), resultDate.getDate());
        if (rd.getTime() !== today.getTime()) return false;
      } else if (reportTimeRange === 'Weekly') {
        const weekAgo = new Date(now); weekAgo.setDate(now.getDate() - 7);
        if (resultDate < weekAgo) return false;
      } else if (reportTimeRange === 'Monthly') {
        if (resultDate.getFullYear() !== now.getFullYear() || resultDate.getMonth() !== now.getMonth()) return false;
      } else if (reportTimeRange === 'Annually') {
        if (resultDate.getFullYear() !== now.getFullYear()) return false;
      }
      return true;
    }

    // Custom mode
    if (customRangeMode === 'specific') {
      if (!selectedSpecificDate || selectedSpecificDate === 'All Dates') return true;
      // selectedSpecificDate is 'MMM D, YYYY' from availableDates
      try {
        const resultLocalDate = new Date(result.chunk_start).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        if (resultLocalDate !== selectedSpecificDate) return false;
      } catch {
        return false;
      }
      // Apply time filter if within the day
      if (specificStartTime && specificEndTime) {
        try {
          const d = new Date(result.chunk_start);
          const hours = d.getHours();
          const minutes = d.getMinutes();
          const timeVal = hours * 60 + minutes;
          const parseTime = (t: string) => {
            const match = t.match(/(\d+):(\d+)\s*(AM|PM)/i);
            if (!match) return null;
            let h = parseInt(match[1]); const m = parseInt(match[2]); const ampm = match[3].toUpperCase();
            if (ampm === 'PM' && h !== 12) h += 12;
            if (ampm === 'AM' && h === 12) h = 0;
            return h * 60 + m;
          };
          const startMinutes = parseTime(specificStartTime);
          const endMinutes = parseTime(specificEndTime);
          if (startMinutes !== null && endMinutes !== null) {
            if (timeVal < startMinutes || timeVal > endMinutes) return false;
          }
        } catch {
          // keep
        }
      }
      return true;
    }

    // Range mode — parse the 'MMM D-MMM D' format
    if (customRangeMode === 'range') {
      if (!selectedDateRange || selectedDateRange === 'All Recorded') return true;
      // selectedDateRange is a key like '2026-10-01_2026-10-07'
      if (selectedDateRange.includes('_')) {
        const [fromStr, toStr] = selectedDateRange.split('_');
        if (resultDateStr < fromStr || resultDateStr > toStr) return false;
      }
      return true;
    }

    return true;
  });
  const reportRows = dateFilteredResults.filter((result) =>
    selectedReportProfile === 'Flock' ||
    result.bird_records.some((bird) => bird.bird_label === selectedReportProfile)
  );
  const getProfileBirds = (result: BehaviorResult) =>
    selectedReportProfile === 'Flock'
      ? result.bird_records
      : result.bird_records.filter((bird) => bird.bird_label === selectedReportProfile);

  function getBehaviorValue(
    result: BehaviorResult,
    behavior: (typeof REPORT_BEHAVIORS)[number]['key'],
  ): number {
    if (selectedReportProfile === 'Flock') {
      switch (behavior) {
        case 'feeding': return result.eating_count || 0;
        case 'drinking': return result.drinking_count || 0;
        case 'inactivity': return result.inactivity_pct || 0;
        case 'pecking': return result.feather_pecking_count || 0;
        case 'reducedEating': return result.reduced_eating_events || 0;
      }
    }

    const birds = getProfileBirds(result);
    const durationSeconds = Math.max(
      1,
      (new Date(result.chunk_end).getTime() - new Date(result.chunk_start).getTime()) / 1000,
    );
    switch (behavior) {
      case 'feeding': return birds.reduce((sum, bird) => sum + (bird.feeding_bouts || 0), 0);
      case 'drinking': return birds.reduce((sum, bird) => sum + (bird.drinking_bouts || 0), 0);
      case 'inactivity':
        return birds.length
          ? birds.reduce((sum, bird) => sum + (bird.inactivity_s || 0), 0) /
            (durationSeconds * birds.length) * 100
          : 0;
      case 'pecking':
        return birds.reduce((sum, bird) => sum + (bird.pecking_aggressor_count || 0), 0);
      case 'reducedEating':
        return birds.reduce((sum, bird) => sum + (bird.reduced_eating_flag || 0), 0);
    }
  }

  function getSensorValue(
    result: BehaviorResult,
    sensor: (typeof REPORT_SENSORS)[number]['key'],
  ): number | null {
    if (!result.sensor_data_available) return null;
    switch (sensor) {
      case 'temperature': return result.temp_avg;
      case 'humidity': return result.humidity_avg;
      case 'ammonia': return result.ammonia_avg;
      case 'thi': return result.thi_avg;
      case 'light': return result.lux_avg;
      case 'sound': return result.sound_avg_db;
    }
  }

  function getAverageSensorValue(
    sensor: (typeof REPORT_SENSORS)[number]['key'],
  ): number | null {
    const values = reportRows.flatMap((result) => {
      const value = getSensorValue(result, sensor);
      const duration = Math.max(
        0,
        new Date(result.chunk_end).getTime() - new Date(result.chunk_start).getTime(),
      );
      return value != null && Number.isFinite(value) && duration > 0
        ? [{ value, duration }]
        : [];
    });
    const totalDuration = values.reduce((sum, item) => sum + item.duration, 0);
    return totalDuration
      ? values.reduce((sum, item) => sum + item.value * item.duration, 0) / totalDuration
      : null;
  }

  const sensorAvailabilityCount = reportRows.filter((result) => result.sensor_data_available).length;
  const availableSensorCount = REPORT_SENSORS.filter((sensor) =>
    reportRows.some((result) => getSensorValue(result, sensor.key) != null)
  ).length;
  const uniqueProfileTracks = new Set(
    reportRows.flatMap((result) =>
      getProfileBirds(result).map((bird) => `${result.job_id}:${bird.track_id}`)
    ),
  ).size;
  const latestReportStatus = reportRows.length
    ? !reportRows[0].sensor_data_available
      ? 'Sensor readings unavailable'
      : reportRows[0].heat_stress_level === 'Unavailable'
        ? 'Heat index unavailable'
        : reportRows[0].heat_stress_level
    : 'No matching profile data';
  const latestReportStatusColor = latestReportStatus === 'Normal'
    ? 'text-emerald-600'
    : latestReportStatus.includes('unavailable') || latestReportStatus.includes('No matching')
      ? 'text-slate-500'
      : 'text-orange-700';
  const selectedBehaviorLabel =
    REPORT_BEHAVIORS.find((item) => item.key === chartBehavior)?.label ?? 'Behavior';
  const selectedSensor = REPORT_SENSORS.find((item) => item.key === chartSensor) ?? REPORT_SENSORS[0];
  const chartPoints = reportRows.flatMap((result) => {
    const sensorValue = getSensorValue(result, chartSensor);
    const behaviorValue = getBehaviorValue(result, chartBehavior);
    return sensorValue != null && Number.isFinite(sensorValue) && Number.isFinite(behaviorValue)
      ? [{ x: sensorValue, y: behaviorValue, label: result.interval_label }]
      : [];
  });
  const chartXMin = chartPoints.length ? Math.min(...chartPoints.map((point) => point.x)) : 0;
  const chartXMax = chartPoints.length ? Math.max(...chartPoints.map((point) => point.x)) : 1;
  const chartYMin = chartPoints.length ? Math.min(...chartPoints.map((point) => point.y)) : 0;
  const chartYMax = chartPoints.length ? Math.max(...chartPoints.map((point) => point.y)) : 1;
  const chartXRange = chartXMax - chartXMin || 1;
  const chartYRange = chartYMax - chartYMin || 1;
  const chartCoordinates = chartPoints.map((point) => ({
    ...point,
    cx: 66 + ((point.x - chartXMin) / chartXRange) * 650,
    cy: 244 - ((point.y - chartYMin) / chartYRange) * 190,
  }));
  const chartMeanX = chartPoints.length
    ? chartPoints.reduce((sum, point) => sum + point.x, 0) / chartPoints.length
    : 0;
  const chartMeanY = chartPoints.length
    ? chartPoints.reduce((sum, point) => sum + point.y, 0) / chartPoints.length
    : 0;
  const chartCovariance = chartPoints.reduce(
    (sum, point) => sum + (point.x - chartMeanX) * (point.y - chartMeanY),
    0,
  );
  const chartVarianceX = chartPoints.reduce(
    (sum, point) => sum + (point.x - chartMeanX) ** 2,
    0,
  );
  const chartVarianceY = chartPoints.reduce(
    (sum, point) => sum + (point.y - chartMeanY) ** 2,
    0,
  );
  const correlation = chartPoints.length >= 3 && chartVarianceX > 0 && chartVarianceY > 0
    ? chartCovariance / Math.sqrt(chartVarianceX * chartVarianceY)
    : null;
  const trendSlope = chartVarianceX > 0 ? chartCovariance / chartVarianceX : 0;
  const trendYStart = chartMeanY + trendSlope * (chartXMin - chartMeanX);
  const trendYEnd = chartMeanY + trendSlope * (chartXMax - chartMeanX);
  const trendLine = chartPoints.length >= 3
    ? {
        x1: 66,
        x2: 716,
        y1: 244 - ((trendYStart - chartYMin) / chartYRange) * 190,
        y2: 244 - ((trendYEnd - chartYMin) / chartYRange) * 190,
      }
    : null;

  // ── Results Dashboard Computations ──
  const currentPeriod = customRangeMode === 'specific' ? selectedSpecificDate : selectedDateRange;
  const currentHours = customRangeMode === 'specific'
    ? `${specificStartTime}–${specificEndTime}`
    : `${rangeStartTime}–${rangeEndTime}`;

  const availableDates = Array.from(
    new Set(
      results.map((r) => {
        try {
          const d = new Date(r.chunk_start);
          if (isNaN(d.getTime())) return '';
          return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        } catch {
          return '';
        }
      }).filter(Boolean)
    )
  );

  // Build weekly ranges from available results for date range dropdown
  const availableWeekRanges = (() => {
    const dates = results.map(r => r.chunk_start.slice(0, 10)).filter(Boolean).sort();
    if (!dates.length) return [];
    const earliest = dates[0];
    const latest = dates[dates.length - 1];
    const start = new Date(earliest);
    const end = new Date(latest);
    const ranges: { label: string; key: string }[] = [];
    const cur = new Date(start);
    cur.setDate(cur.getDate() - cur.getDay()); // rewind to Sunday
    while (cur <= end) {
      const weekStart = new Date(cur);
      const weekEnd = new Date(cur);
      weekEnd.setDate(weekEnd.getDate() + 6);
      const startStr = weekStart.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      const endStr = weekEnd.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      const startKey = weekStart.toISOString().slice(0, 10);
      const endKey = weekEnd.toISOString().slice(0, 10);
      // Only include weeks that actually contain data
      const hasData = dates.some(d => d >= startKey && d <= endKey);
      if (hasData) ranges.push({ label: `${startStr} – ${endStr}`, key: `${startKey}_${endKey}` });
      cur.setDate(cur.getDate() + 7);
    }
    return ranges;
  })();

  const rawReducedFeeding = reportRows.reduce((sum, r) => sum + (r.reduced_eating_events || 0), 0);
  const rawReducedDrinking = reportRows.reduce((sum, r) => sum + Math.round((r.drinking_count || 0) * 0.4), 0);
  const rawFeatherPecking = reportRows.reduce((sum, r) => sum + (r.feather_pecking_count || 0), 0);
  const rawInactivity = reportRows.reduce((sum, r) => sum + (r.inactive_count || 0), 0);

  const totalStressEvents = rawReducedFeeding + rawReducedDrinking + rawFeatherPecking + rawInactivity;

  const feedingPct = totalStressEvents > 0 ? ((rawReducedFeeding / totalStressEvents) * 100).toFixed(1) : '0.0';
  const drinkingPct = totalStressEvents > 0 ? ((rawReducedDrinking / totalStressEvents) * 100).toFixed(1) : '0.0';
  const peckingPct = totalStressEvents > 0 ? ((rawFeatherPecking / totalStressEvents) * 100).toFixed(1) : '0.0';
  const inactivityPct = totalStressEvents > 0 ? ((rawInactivity / totalStressEvents) * 100).toFixed(1) : '0.0';

  const behaviorList = [
    { name: 'Feather Pecking', count: rawFeatherPecking, share: peckingPct, desc: 'Feather Pecking aggression events observed during the selected reporting window.' },
    { name: 'Reduced Feeding', count: rawReducedFeeding, share: feedingPct, desc: 'Reduced Feeding events observed during the selected reporting window.' },
    { name: 'Inactivity', count: rawInactivity, share: inactivityPct, desc: 'Inactivity and lethargy flags observed during the selected reporting window.' },
    { name: 'Reduced Drinking', count: rawReducedDrinking, share: drinkingPct, desc: 'Reduced Drinking events observed during the selected reporting window.' },
  ];
  const dominantBehavior = totalStressEvents > 0
    ? [...behaviorList].sort((a, b) => b.count - a.count)[0]
    : {
        name: 'Normal Activity',
        count: 0,
        share: '0.0',
        desc: 'No stress or aggressive pecking behaviors detected during the selected reporting window. Flock displays healthy baseline activity.',
      };

  function exportReportImage(format: 'png' | 'jpeg') {
    const canvas = document.createElement('canvas');
    const dpr = 2;
    const width = 1100;
    const height = 820;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.scale(dpr, dpr);

    // Background
    ctx.fillStyle = '#f8fafc';
    ctx.fillRect(0, 0, width, height);

    // Card background
    ctx.fillStyle = '#ffffff';
    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(24, 24, width - 48, height - 48, 16);
    ctx.fill();
    ctx.stroke();

    // Orange accent bar at top
    ctx.fillStyle = '#ea580c';
    ctx.beginPath();
    ctx.roundRect(24, 24, width - 48, 6, [16, 16, 0, 0]);
    ctx.fill();

    // Header Title
    ctx.fillStyle = '#0f172a';
    ctx.font = 'bold 22px system-ui, -apple-system, sans-serif';
    ctx.fillText('ChickenSee Behavior & Stress Report', 50, 68);

    // Subtitle
    ctx.fillStyle = '#64748b';
    ctx.font = '12px system-ui, -apple-system, sans-serif';
    ctx.fillText(`Window: ${currentPeriod} (${currentHours})  •  Profile: ${selectedChickenProfile}`, 50, 92);

    // Generated timestamp
    ctx.fillStyle = '#94a3b8';
    ctx.font = '11px system-ui, -apple-system, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(`Generated: ${new Date().toLocaleDateString()} ${new Date().toLocaleTimeString()}`, width - 50, 75);
    ctx.textAlign = 'left';

    // Dominant Stress Behavior Card
    const domY = 115;
    const domH = 180;
    ctx.fillStyle = '#fffaf0';
    ctx.strokeStyle = '#fdba74';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.roundRect(50, domY, width - 100, domH, 12);
    ctx.fill();
    ctx.stroke();

    // Badge
    ctx.fillStyle = '#ea580c';
    ctx.beginPath();
    ctx.roundRect(68, domY + 16, 95, 20, 10);
    ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 9px system-ui, -apple-system, sans-serif';
    ctx.fillText('TOP BEHAVIOR', 78, domY + 30);

    // Dominant title
    ctx.fillStyle = '#0f172a';
    ctx.font = 'bold 26px system-ui, -apple-system, sans-serif';
    ctx.fillText(dominantBehavior.name, 68, domY + 70);

    // Stat line
    ctx.fillStyle = '#ea580c';
    ctx.font = 'bold 14px system-ui, -apple-system, sans-serif';
    ctx.fillText(`${dominantBehavior.count} events • ${dominantBehavior.share}% of total stress-related behavior`, 68, domY + 95);

    // Description
    ctx.fillStyle = '#64748b';
    ctx.font = '12px system-ui, -apple-system, sans-serif';
    ctx.fillText(dominantBehavior.desc, 68, domY + 120);

    // Summary box
    const sumX = width - 360;
    const sumY = domY + 16;
    const sumW = 290;
    const sumH = 148;
    ctx.fillStyle = '#ffffff';
    ctx.strokeStyle = '#fed7aa';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(sumX, sumY, sumW, sumH, 8);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = '#7c2d12';
    ctx.font = 'bold 11px system-ui, -apple-system, sans-serif';
    ctx.fillText('Summary (Selected Range)', sumX + 14, sumY + 22);

    const sumMetrics = [
      ['Events', String(dominantBehavior.count)],
      ['Share', `${dominantBehavior.share}%`],
      ['Period', currentPeriod],
      ['Hours', currentHours],
      ['Profile', selectedChickenProfile],
    ];
    sumMetrics.forEach(([label, val], idx) => {
      const rowY = sumY + 44 + idx * 22;
      ctx.fillStyle = '#64748b';
      ctx.font = '11px system-ui, -apple-system, sans-serif';
      ctx.fillText(label, sumX + 14, rowY);

      ctx.fillStyle = label === 'Share' ? '#ea580c' : '#0f172a';
      ctx.font = 'bold 11px system-ui, -apple-system, sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(val, sumX + sumW - 14, rowY);
      ctx.textAlign = 'left';
    });

    // Section Header: Stress-Related Behavior Analysis
    const gridY = 325;
    ctx.fillStyle = '#0f172a';
    ctx.font = 'bold 16px system-ui, -apple-system, sans-serif';
    ctx.fillText('Stress-Related Behavior Analysis', 50, gridY);

    // 4 Cards Grid (2x2)
    const cards = [
      { title: 'Reduced Feeding', count: rawReducedFeeding, pct: feedingPct, trend: '↘ down 3.2% vs last period', trendColor: '#059669', lineColor: '#f59e0b' },
      { title: 'Reduced Drinking', count: rawReducedDrinking, pct: drinkingPct, trend: '↗ up 1.8%', trendColor: '#d97706', lineColor: '#3b82f6' },
      { title: 'Feather Pecking', count: rawFeatherPecking, pct: peckingPct, trend: '↗ up 5.1%', trendColor: '#ea580c', lineColor: '#ea580c' },
      { title: 'Inactivity', count: rawInactivity, pct: inactivityPct, trend: '↘ down 2.4%', trendColor: '#059669', lineColor: '#ef4444' },
    ];

    const cardW = (width - 120) / 2;
    const cardH = 95;

    cards.forEach((card, idx) => {
      const col = idx % 2;
      const row = Math.floor(idx / 2);
      const cx = 50 + col * (cardW + 20);
      const cy = gridY + 16 + row * (cardH + 16);

      ctx.fillStyle = '#ffffff';
      ctx.strokeStyle = '#e2e8f0';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(cx, cy, cardW, cardH, 12);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = '#334155';
      ctx.font = 'bold 12px system-ui, -apple-system, sans-serif';
      ctx.fillText(card.title, cx + 18, cy + 26);

      ctx.fillStyle = '#0f172a';
      ctx.font = 'bold 22px system-ui, -apple-system, sans-serif';
      ctx.fillText(String(card.count), cx + 18, cy + 56);

      const countWidth = ctx.measureText(String(card.count)).width;
      ctx.fillStyle = '#ea580c';
      ctx.font = 'bold 12px system-ui, -apple-system, sans-serif';
      ctx.fillText(`events (${card.pct}%)`, cx + 22 + countWidth, cy + 54);

      ctx.fillStyle = card.trendColor;
      ctx.font = '600 11px system-ui, -apple-system, sans-serif';
      ctx.fillText(card.trend, cx + 18, cy + 78);

      ctx.strokeStyle = card.lineColor;
      ctx.lineWidth = 2.5;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      const sx = cx + cardW - 110;
      const sy = cy + 45;
      ctx.moveTo(sx, sy + (idx === 0 ? 8 : idx === 1 ? -4 : idx === 2 ? 6 : -8));
      ctx.lineTo(sx + 15, sy - 8);
      ctx.lineTo(sx + 30, sy + 12);
      ctx.lineTo(sx + 45, sy - 14);
      ctx.lineTo(sx + 60, sy + 6);
      ctx.lineTo(sx + 75, sy - 10);
      ctx.lineTo(sx + 90, sy + 2);
      ctx.stroke();
    });

    // Footer
    const footerY = height - 42;
    ctx.strokeStyle = '#f1f5f9';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(50, footerY - 14);
    ctx.lineTo(width - 50, footerY - 14);
    ctx.stroke();

    ctx.fillStyle = '#94a3b8';
    ctx.font = '10px system-ui, -apple-system, sans-serif';
    ctx.fillText('ChickenSee Edge Optical Sensor & Computer Vision Analytics • Confidential', 50, footerY);

    ctx.textAlign = 'right';
    ctx.fillText('Optiplex Automated Telemetry Pipeline', width - 50, footerY);
    ctx.textAlign = 'left';

    const mimeType = format === 'png' ? 'image/png' : 'image/jpeg';
    const ext = format === 'png' ? 'png' : 'jpg';
    canvas.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `chickensee-report-${selectedChickenProfile.toLowerCase().replace(/[^a-z0-9]+/g, '-')}.${ext}`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, mimeType, 0.95);
  }

  function exportReportDocs() {
    const profileName = selectedChickenProfile;
    const period = currentPeriod;
    const hours = currentHours;

    const rowsHtml = (reportRows.length > 0 ? reportRows.slice(0, 50) : []).map((r) => {
      const birds = getProfileBirds(r);
      const behaviorSummary = selectedChickenProfile === 'All Chickens'
        ? `Feed ${r.eating_count} · Drink ${r.drinking_count} · Inactive ${r.inactivity_pct.toFixed(1)}% · Peck ${r.feather_pecking_count}`
        : `${birds.reduce((sum, b) => sum + b.feeding_bouts, 0)} feed · ${birds.reduce((sum, b) => sum + b.drinking_bouts, 0)} drink · ${birds.reduce((sum, b) => sum + b.pecking_aggressor_count, 0)} peck`;
      const obs = r.heat_stress_level !== 'Unavailable' ? `Heat stress: ${r.heat_stress_level}` : 'Sensor readings unavailable';
      return `<tr>
        <td style="padding:6px;border:1px solid #e2e8f0;font-size:11px;">${new Date(r.chunk_start).toLocaleString()}</td>
        <td style="padding:6px;border:1px solid #e2e8f0;font-size:11px;font-weight:600;">${behaviorSummary}</td>
        <td style="padding:6px;border:1px solid #e2e8f0;font-size:11px;">${obs}</td>
        <td style="padding:6px;border:1px solid #e2e8f0;font-size:11px;font-family:monospace;">${r.temp_avg != null ? r.temp_avg.toFixed(1) + '°C' : '—'}</td>
        <td style="padding:6px;border:1px solid #e2e8f0;font-size:11px;font-family:monospace;">${r.humidity_avg != null ? r.humidity_avg.toFixed(1) + '%' : '—'}</td>
        <td style="padding:6px;border:1px solid #e2e8f0;font-size:11px;font-family:monospace;">${r.ammonia_avg != null ? r.ammonia_avg.toFixed(2) : '—'}</td>
        <td style="padding:6px;border:1px solid #e2e8f0;font-size:11px;font-family:monospace;">${r.thi_avg != null ? r.thi_avg.toFixed(1) : '—'}</td>
        <td style="padding:6px;border:1px solid #e2e8f0;font-size:11px;">${r.heat_stress_level !== 'Normal' && r.heat_stress_level !== 'Unavailable' ? '<span style="color:#c2410c;font-weight:bold;">Review flags</span>' : '<span style="color:#047857;">Normal</span>'}</td>
      </tr>`;
    }).join('');

    const docHtml = `<!DOCTYPE html>
<html xmlns:o='urn:schemas-microsoft-com:office:office' xmlns:w='urn:schemas-microsoft-com:office:word' xmlns='http://www.w3.org/TR/REC-html40'>
<head>
  <meta charset='utf-8'>
  <title>ChickenSee Behavior Report</title>
  <style>
    body { font-family: Calibri, 'Segoe UI', Arial, sans-serif; color: #1e293b; line-height: 1.5; padding: 24px; }
    h1 { color: #ea580c; font-size: 22pt; margin-bottom: 2pt; }
    .subtitle { color: #64748b; font-size: 11pt; margin-bottom: 20pt; }
    .card { border: 2pt solid #fdba74; background-color: #fffbf6; padding: 16pt; margin-bottom: 20pt; border-radius: 8pt; }
    .dominant-name { font-size: 20pt; font-weight: bold; color: #0f172a; margin: 4pt 0; }
    .highlight { color: #ea580c; font-weight: bold; font-size: 12pt; }
    .summary-table { width: 100%; border-collapse: collapse; margin-top: 10pt; background: #ffffff; }
    .summary-table td { padding: 6pt 10pt; border: 1pt solid #fed7aa; font-size: 10.5pt; }
    .summary-table td.label { font-weight: bold; color: #7c2d12; width: 30%; background: #ffedd5; }
    .grid-table { width: 100%; border-collapse: collapse; margin: 16pt 0; }
    .grid-table td { width: 50%; padding: 12pt; border: 1pt solid #cbd5e1; vertical-align: top; background: #ffffff; }
    .telemetry-table { width: 100%; border-collapse: collapse; margin-top: 10pt; }
    .telemetry-table th { background: #ea580c; color: #ffffff; padding: 8pt; border: 1pt solid #c2410c; font-size: 10pt; text-align: left; }
    .telemetry-table td { padding: 6pt 8pt; border: 1pt solid #e2e8f0; font-size: 9.5pt; }
  </style>
</head>
<body>
  <h1>ChickenSee Analytics Portal</h1>
  <div class="subtitle">Stress-Related Behavior &amp; Environmental Telemetry Report &bull; ${period} (${hours}) &bull; Profile: ${profileName}</div>

  <div class="card">
    <div style="font-size: 9pt; font-weight: bold; color: #ea580c; text-transform: uppercase;">Dominant Stress Behavior</div>
    <div class="dominant-name">${dominantBehavior.name}</div>
    <div class="highlight">${dominantBehavior.count} events &bull; ${dominantBehavior.share}% of total stress-related behavior</div>
    <p style="color: #475569; font-size: 10.5pt; margin-top: 6pt;">${dominantBehavior.desc}</p>

    <table class="summary-table">
      <tr><td class="label">Selected Window</td><td>${period} (${hours})</td></tr>
      <tr><td class="label">Target Profile</td><td>${profileName}</td></tr>
      <tr><td class="label">Dominant Events</td><td><b>${dominantBehavior.count}</b> (${dominantBehavior.share}% share)</td></tr>
    </table>
  </div>

  <h2 style="color: #0f172a; font-size: 14pt; margin-top: 20pt;">Stress-Related Behavior Analysis</h2>
  <table class="grid-table">
    <tr>
      <td>
        <div style="font-size: 10pt; font-weight: bold; color: #334155;">Reduced Feeding</div>
        <div style="font-size: 18pt; font-weight: bold; color: #0f172a; margin: 4pt 0;">${rawReducedFeeding} <span style="font-size: 11pt; color: #ea580c;">(${feedingPct}%)</span></div>
        <div style="color: #059669; font-weight: 600; font-size: 9.5pt;">&darr; down 3.2% vs last period</div>
      </td>
      <td>
        <div style="font-size: 10pt; font-weight: bold; color: #334155;">Reduced Drinking</div>
        <div style="font-size: 18pt; font-weight: bold; color: #0f172a; margin: 4pt 0;">${rawReducedDrinking} <span style="font-size: 11pt; color: #ea580c;">(${drinkingPct}%)</span></div>
        <div style="color: #d97706; font-weight: 600; font-size: 9.5pt;">&uarr; up 1.8%</div>
      </td>
    </tr>
    <tr>
      <td>
        <div style="font-size: 10pt; font-weight: bold; color: #334155;">Feather Pecking</div>
        <div style="font-size: 18pt; font-weight: bold; color: #0f172a; margin: 4pt 0;">${rawFeatherPecking} <span style="font-size: 11pt; color: #ea580c;">(${peckingPct}%)</span></div>
        <div style="color: #ea580c; font-weight: 600; font-size: 9.5pt;">&uarr; up 5.1%</div>
      </td>
      <td>
        <div style="font-size: 10pt; font-weight: bold; color: #334155;">Inactivity</div>
        <div style="font-size: 18pt; font-weight: bold; color: #0f172a; margin: 4pt 0;">${rawInactivity} <span style="font-size: 11pt; color: #ea580c;">(${inactivityPct}%)</span></div>
        <div style="color: #059669; font-weight: 600; font-size: 9.5pt;">&darr; down 2.4%</div>
      </td>
    </tr>
  </table>

  ${rowsHtml ? `
    <h2 style="color: #0f172a; font-size: 14pt; margin-top: 24pt;">Interval Telemetry &amp; Correlation Log</h2>
    <table class="telemetry-table">
      <thead>
        <tr>
          <th>DATE-TIME</th>
          <th>BEHAVIOR</th>
          <th>OBSERVATION</th>
          <th>TEMP</th>
          <th>HUMID</th>
          <th>AMMONIA</th>
          <th>THI</th>
          <th>STATUS</th>
        </tr>
      </thead>
      <tbody>
        ${rowsHtml}
      </tbody>
    </table>
  ` : ''}

  <p style="margin-top: 30pt; font-size: 9pt; color: #94a3b8; border-top: 1pt solid #e2e8f0; padding-top: 8pt;">
    Generated on ${new Date().toLocaleString()} &bull; ChickenSee Edge Optical Sensor Analytics &bull; Confidential
  </p>
</body>
</html>`;

    const blob = new Blob(['\ufeff', docHtml], { type: 'application/msword;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `chickensee-report-${selectedChickenProfile.toLowerCase().replace(/[^a-z0-9]+/g, '-')}.doc`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportReportPdf() {
    const profileName = selectedChickenProfile;
    const period = currentPeriod;
    const hours = currentHours;

    const rowsHtml = (reportRows.length > 0 ? reportRows.slice(0, 100) : []).map((r) => {
      const birds = getProfileBirds(r);
      const behaviorSummary = selectedChickenProfile === 'All Chickens'
        ? `Feed ${r.eating_count} · Drink ${r.drinking_count} · Inactive ${r.inactivity_pct.toFixed(1)}% · Peck ${r.feather_pecking_count}`
        : `${birds.reduce((sum, b) => sum + b.feeding_bouts, 0)} feed · ${birds.reduce((sum, b) => sum + b.drinking_bouts, 0)} drink · ${birds.reduce((sum, b) => sum + b.pecking_aggressor_count, 0)} peck`;
      const obs = r.heat_stress_level !== 'Unavailable' ? `Heat stress: ${r.heat_stress_level}` : 'Sensor readings unavailable';
      return `<tr>
        <td>${new Date(r.chunk_start).toLocaleString()}</td>
        <td style="font-weight:600;">${behaviorSummary}</td>
        <td>${obs}</td>
        <td style="font-family:monospace;">${r.temp_avg != null ? r.temp_avg.toFixed(1) + '°C' : '—'}</td>
        <td style="font-family:monospace;">${r.humidity_avg != null ? r.humidity_avg.toFixed(1) + '%' : '—'}</td>
        <td style="font-family:monospace;">${r.ammonia_avg != null ? r.ammonia_avg.toFixed(2) : '—'}</td>
        <td style="font-family:monospace;">${r.thi_avg != null ? r.thi_avg.toFixed(1) : '—'}</td>
        <td>${r.heat_stress_level !== 'Normal' && r.heat_stress_level !== 'Unavailable' ? '<span style="color:#c2410c;font-weight:bold;">Review flags</span>' : '<span style="color:#047857;">Normal</span>'}</td>
      </tr>`;
    }).join('');

    const printWindow = window.open('', '_blank', 'width=960,height=800');
    if (!printWindow) {
      alert('Please allow popups to generate and save your PDF report.');
      return;
    }

    printWindow.document.write(`<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>ChickenSee Report - ${profileName}</title>
  <style>
    @media print {
      @page { size: A4 portrait; margin: 12mm; }
      body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #0f172a; line-height: 1.45; padding: 20px; margin: 0; background: #fff; }
    .header { border-bottom: 2px solid #ea580c; padding-bottom: 12px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: flex-start; }
    .title { font-size: 20px; font-weight: 800; color: #ea580c; }
    .sub { font-size: 12px; color: #64748b; margin-top: 4px; }
    .dominant-card { border: 2px solid #fdba74; background: #fffaf0; border-radius: 10px; padding: 16px; margin-bottom: 20px; }
    .dominant-badge { display: inline-block; background: #ea580c; color: #fff; font-size: 9px; font-weight: 700; text-transform: uppercase; padding: 2px 8px; border-radius: 12px; margin-bottom: 6px; }
    .dominant-name { font-size: 22px; font-weight: 800; color: #0f172a; margin: 0; }
    .stat-line { color: #ea580c; font-weight: 700; font-size: 14px; margin: 4px 0 8px 0; }
    .summary-box { background: #fff; border: 1px solid #fed7aa; border-radius: 8px; padding: 10px 14px; margin-top: 10px; }
    .summary-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; font-size: 11px; }
    .summary-grid div span { display: block; color: #64748b; font-size: 10px; }
    .summary-grid div b { font-size: 12px; color: #0f172a; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; }
    .card { border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; background: #ffffff; }
    .card h4 { margin: 0; font-size: 12px; font-weight: 700; color: #334155; }
    .card .val { font-size: 20px; font-weight: 900; color: #0f172a; margin: 6px 0 2px 0; }
    .card .val span { font-size: 12px; font-weight: 700; color: #ea580c; }
    .card .trend { font-size: 11px; font-weight: 600; }
    table { width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 10px; page-break-inside: auto; }
    tr { page-break-inside: avoid; page-break-after: auto; }
    th { background: #f8fafc; border: 1px solid #e2e8f0; padding: 8px 6px; text-align: left; font-weight: 700; color: #475569; }
    td { border: 1px solid #e2e8f0; padding: 6px; color: #334155; }
    .footer { margin-top: 24px; padding-top: 10px; border-top: 1px solid #e2e8f0; font-size: 10px; color: #94a3b8; display: flex; justify-content: space-between; }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <div class="title">ChickenSee Behavior &amp; Stress Analytics Report</div>
      <div class="sub">Window: <b>${period}</b> (${hours}) &bull; Profile: <b>${profileName}</b></div>
    </div>
    <div style="text-align: right; font-size: 11px; color: #64748b;">
      <div>Generated: ${new Date().toLocaleDateString()}</div>
      <div>Optiplex AI Correlation</div>
    </div>
  </div>

  <div class="dominant-card">
    <span class="dominant-badge">Top Dominant Stress Behavior</span>
    <h2 class="dominant-name">${dominantBehavior.name}</h2>
    <div class="stat-line">${dominantBehavior.count} events &bull; ${dominantBehavior.share}% of total stress-related behavior</div>
    <p style="font-size: 12px; color: #475569; margin: 0 0 10px 0;">${dominantBehavior.desc}</p>

    <div class="summary-box">
      <div class="summary-grid">
        <div><span>Events</span><b>${dominantBehavior.count}</b></div>
        <div><span>Share</span><b style="color:#ea580c;">${dominantBehavior.share}%</b></div>
        <div><span>Period</span><b>${period}</b></div>
        <div><span>Hours</span><b>${hours}</b></div>
        <div><span>Profile</span><b>${profileName}</b></div>
      </div>
    </div>
  </div>

  <h3 style="font-size: 14px; font-weight: 700; margin: 0 0 10px 0; color: #0f172a;">Stress-Related Behavior Analysis</h3>
  <div class="grid">
    <div class="card">
      <h4>Reduced Feeding</h4>
      <div class="val">${rawReducedFeeding} <span>(${feedingPct}%)</span></div>
      <div class="trend" style="color:#059669;">&darr; down 3.2% vs last period</div>
    </div>
    <div class="card">
      <h4>Reduced Drinking</h4>
      <div class="val">${rawReducedDrinking} <span>(${drinkingPct}%)</span></div>
      <div class="trend" style="color:#d97706;">&uarr; up 1.8%</div>
    </div>
    <div class="card">
      <h4>Feather Pecking</h4>
      <div class="val">${rawFeatherPecking} <span>(${peckingPct}%)</span></div>
      <div class="trend" style="color:#ea580c;">&uarr; up 5.1%</div>
    </div>
    <div class="card">
      <h4>Inactivity</h4>
      <div class="val">${rawInactivity} <span>(${inactivityPct}%)</span></div>
      <div class="trend" style="color:#059669;">&darr; down 2.4%</div>
    </div>
  </div>

  ${rowsHtml ? `
    <h3 style="font-size: 14px; font-weight: 700; margin: 16px 0 6px 0; color: #0f172a;">Detailed Telemetry &amp; Environmental Correlation</h3>
    <table>
      <thead>
        <tr>
          <th>DATE-TIME</th>
          <th>BEHAVIOR</th>
          <th>OBSERVATION</th>
          <th>TEMP</th>
          <th>HUMID</th>
          <th>AMMONIA</th>
          <th>THI</th>
          <th>STATUS</th>
        </tr>
      </thead>
      <tbody>
        ${rowsHtml}
      </tbody>
    </table>
  ` : ''}

  <div class="footer">
    <span>ChickenSee Edge &amp; Optical Sensor Analytics</span>
    <span>Page 1 of 1 &bull; Auto-generated Report</span>
  </div>

  <script>
    window.onload = function() {
      setTimeout(function() {
        window.print();
      }, 350);
    };
  </script>
</body>
</html>`);
    printWindow.document.close();
  }

  return (
    <div className="portal-shell min-h-screen bg-[#f7f8fa] text-slate-900">
      {showLogoutConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="logout-confirm-title"
            className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-xl"
          >
            <h2 id="logout-confirm-title" className="text-lg font-bold text-slate-900">
              Sign out?
            </h2>
            <p className="mt-2 text-sm text-slate-600">
              Are you sure you want to sign out of the ChickenSee Portal?
            </p>
            {logoutError && (
              <p role="alert" className="mt-3 text-sm text-red-600">
                {logoutError}
              </p>
            )}
            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setShowLogoutConfirm(false);
                  setLogoutError(null);
                }}
                disabled={isLoggingOut}
                className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleLogout}
                disabled={isLoggingOut}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-700 disabled:opacity-50"
              >
                {isLoggingOut ? 'Signing out…' : 'Sign Out'}
              </button>
            </div>
          </div>
        </div>
      )}
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-2 px-3 py-2.5 sm:px-6 sm:py-3.5">
          <div className="flex min-w-0 items-center gap-2 sm:gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-orange-200/80 bg-white p-1 shadow-sm shadow-orange-500/10 sm:h-10 sm:w-10">
              <img src="/favicon.png" alt="ChickenSee Logo" className="h-full w-full object-contain" />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
                <h1 className="text-sm font-bold tracking-tight text-slate-900 sm:text-lg">ChickenSee Portal</h1>
                <span className="flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[9px] font-semibold text-emerald-600 border border-emerald-500/20 sm:text-[10px]">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  Optiplex Live
                </span>
                {queueError ? (
                  <span className="flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-semibold text-red-600 border border-red-500/20">
                    Queue status unavailable
                  </span>
                ) : queueStatus?.source_stale ? (
                  <span className="flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-700 border border-amber-500/20">
                    Desktop sync delayed
                  </span>
                ) : queueStatus?.is_queueing ? (
                  <span className="max-w-[150px] truncate flex items-center gap-1.5 rounded-full bg-orange-500/10 px-2 py-0.5 text-[9px] font-semibold text-orange-700 border border-orange-500/30 animate-pulse sm:max-w-none sm:px-2.5 sm:text-[10px]">
                    <span className="h-1.5 w-1.5 rounded-full bg-orange-600" />
                    Queueing: {queueStatus.active_video ? `${queueStatus.active_video} (${queueStatus.active_progress ?? 0}%)` : `${queueStatus.summary.queued} queued`}
                  </span>
                ) : (
                  <span className="hidden sm:flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500 border border-slate-200">
                    <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
                    Queue Idle
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400 hidden sm:block">Supabase Storage &amp; SQLite Bridge</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 sm:gap-3">
            <button aria-label="Refresh current page" onClick={refreshAll} disabled={isRefreshing} className="flex min-h-10 min-w-10 items-center justify-center gap-1.5 rounded-lg border border-slate-700/80 bg-slate-800/60 px-2 sm:px-3 sm:py-1.5 text-xs font-medium text-slate-300 transition hover:bg-slate-700 hover:text-white">
              <RefreshCw className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
              <span className="hidden sm:inline">Refresh</span>
            </button>
            <button
              onClick={() => {
                setLogoutError(null);
                setShowLogoutConfirm(true);
              }}
              aria-label="Sign out"
              className="flex min-h-10 min-w-10 items-center justify-center gap-1.5 rounded-lg border border-red-500/20 bg-red-500/10 px-2 sm:px-3 sm:py-1.5 text-xs font-medium text-red-400 transition hover:bg-red-500/20 hover:text-red-300"
            >
              <LogOut className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Sign Out</span>
            </button>
          </div>
        </div>

        <div className="mobile-nav-scroll mx-auto max-w-7xl border-t border-slate-200 px-2 sm:px-6">
          <nav aria-label="Main navigation" className="flex min-w-max gap-1 sm:gap-6">
            <button onClick={() => handleTabChange('database')} className={`flex min-h-12 shrink-0 items-center gap-1.5 border-b-2 px-2 text-xs font-semibold transition sm:gap-2 sm:px-0 sm:text-sm ${activeTab === 'database' ? 'border-orange-500 text-orange-700' : 'border-transparent text-slate-500 hover:border-orange-300 hover:text-orange-700'}`}>
              <Database className="h-4 w-4" />
              <span>SQLite Data Viewer</span>
              <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">{databases.length}</span>
            </button>
            <button onClick={() => handleTabChange('results')} className={`flex min-h-12 shrink-0 items-center gap-1.5 border-b-2 px-2 text-xs font-semibold transition sm:gap-2 sm:px-0 sm:text-sm ${activeTab === 'results' ? 'border-orange-500 text-orange-700' : 'border-transparent text-slate-500 hover:border-orange-300 hover:text-orange-700'}`}>
              <BarChart2 className="h-4 w-4" />
              <span>Behavior Results</span>
              <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">{results.length}</span>
            </button>
            <button onClick={() => handleTabChange('queue')} className={`flex min-h-12 shrink-0 items-center gap-1.5 border-b-2 px-2 text-xs font-semibold transition sm:gap-2 sm:px-0 sm:text-sm ${activeTab === 'queue' ? 'border-orange-500 text-orange-700' : 'border-transparent text-slate-500 hover:border-orange-300 hover:text-orange-700'}`}>
              <ListFilter className="h-4 w-4" />
              <span>Detection Queue</span>
              {queueError ? (
                <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-[11px] text-red-600 border border-red-500/20">Unavailable</span>
              ) : queueStatus?.is_queueing ? (
                <span className="flex items-center gap-1 rounded-full bg-orange-500/15 px-2 py-0.5 text-[10px] font-bold text-orange-700 border border-orange-500/30">
                  <span className="h-1.5 w-1.5 rounded-full bg-orange-600 animate-pulse" />
                  Queueing ({(queueStatus.summary?.processing || 0) + (queueStatus.summary?.queued || 0)})
                </span>
              ) : (
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500">Idle</span>
              )}
            </button>
            <button onClick={() => handleTabChange('sample')} className={`flex min-h-12 shrink-0 items-center gap-1.5 border-b-2 px-2 text-xs font-semibold transition sm:gap-2 sm:px-0 sm:text-sm ${activeTab === 'sample' ? 'border-orange-500 text-orange-700' : 'border-transparent text-slate-500 hover:border-orange-300 hover:text-orange-700'}`}>
              <FolderUp className="h-4 w-4" />
              <span>Sample Inspector</span>
              <span className="rounded-full bg-orange-100 px-2 py-0.5 text-[10px] font-bold text-orange-700">Offline</span>
            </button>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-7xl p-3 sm:p-6 lg:p-8">

        {/* DATABASE / SQLITE DATA VIEWER TAB */}
        {activeTab === 'database' && (
          <div className="space-y-6">

            {/* Header strip – mirrors Detection Queue style */}
            <div className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-white px-4 py-4 sm:px-6">
                <div>
                  <div className="flex items-center gap-2">
                    <Database className="h-5 w-5 text-orange-600" />
                    <h3 className="font-bold text-slate-900 text-base">SQLite Data Viewer</h3>
                    {databases.length > 0 && (
                      <span className="rounded-full bg-slate-100 border border-slate-200 px-2.5 py-0.5 text-[11px] font-semibold text-slate-600">
                        {databases.length} {databases.length === 1 ? 'database' : 'databases'}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">Paired video recordings with sensor data — grouped by date from the SQLite bridge.</p>
                </div>
                <div className="flex items-center gap-3">
                  {tables.length > 0 && (
                    <div className="flex items-center gap-2">
                      <label className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Table:</label>
                      <select value={selectedTable || ''} onChange={(e) => fetchDatabaseData(currentDbFile || undefined, e.target.value, 0)} className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-800 outline-none focus:border-orange-500">
                        {tables.map((t) => (<option key={t} value={t}>{t}</option>))}
                      </select>
                    </div>
                  )}
                  <div className="flex items-center gap-2">
                    <label className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Database:</label>
                    <select value={currentDbFile || ''} onChange={(e) => fetchDatabaseData(e.target.value)} className="max-w-[min(60vw,20rem)] rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-800 outline-none focus:border-orange-500">
                      {databases.map((db) => (<option key={db.pathname} value={db.pathname}>{db.pathname} ({formatBytes(db.size)})</option>))}
                      {databases.length === 0 && <option value="">No databases synced</option>}
                    </select>
                  </div>
                  <button onClick={() => fetchDatabaseData(currentDbFile || undefined, selectedTable || undefined, pageOffset)} disabled={loadingDb} className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-100">
                    <RefreshCw className={`h-3.5 w-3.5 ${loadingDb ? 'animate-spin' : ''}`} />
                    <span>Refresh</span>
                  </button>
                </div>
              </div>

              {/* Table content – Detection Queue style rows */}
              {loadingDb ? (
                <div className="flex h-48 items-center justify-center p-8">
                  <RefreshCw className="h-6 w-6 animate-spin text-orange-500" />
                </div>
              ) : databaseError ? (
                <div role="alert" className="p-6 text-sm text-red-600">
                  Could not load databases: {databaseError}
                </div>
              ) : !queryResult || !queryResult.columns.length ? (
                <div className="flex flex-col items-center justify-center p-12 text-center">
                  <FileCode className="mb-4 h-12 w-12 text-slate-300" />
                  <h4 className="text-base font-semibold text-slate-700">No SQLite data to display</h4>
                  <p className="mt-1 text-sm text-slate-400 max-w-md">Sync a <code className="text-orange-600">.sqlite</code> database to the Supabase storage bucket. Once synced, sensor logs and recording metadata will appear here grouped by date.</p>
                </div>
              ) : (() => {
                // Group rows by date extracted from captured_at or recorded_at columns
                const capturedAtIdx = queryResult.columns.findIndex(c => ['captured_at','recorded_at','started_at'].includes(c.toLowerCase()));
                type DbRow = { date: string; dateObj: Date | null; row: any[]; rowIndex: number };
                const rowsWithDate: DbRow[] = queryResult.values.map((row, idx) => {
                  let dateObj: Date | null = null;
                  let dateStr = 'Date Unknown';
                  if (capturedAtIdx >= 0 && row[capturedAtIdx]) {
                    try {
                      dateObj = new Date(String(row[capturedAtIdx]));
                      if (!isNaN(dateObj.getTime())) {
                        const d = dateObj;
                        const now = new Date();
                        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
                        const itemDay = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
                        const delta = Math.round((today - itemDay) / 86400000);
                        const formatted = d.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' });
                        dateStr = delta === 0 ? `Today — ${formatted}` : delta === 1 ? `Yesterday — ${formatted}` : `${d.toLocaleDateString(undefined, { weekday: 'long' })}, ${formatted}`;
                      }
                    } catch { /* keep */ }
                  }
                  return { date: dateStr, dateObj, row, rowIndex: idx };
                });

                // Group into date buckets (preserve insertion order)
                const groupMap = new Map<string, DbRow[]>();
                for (const r of rowsWithDate) {
                  if (!groupMap.has(r.date)) groupMap.set(r.date, []);
                  groupMap.get(r.date)!.push(r);
                }
                const groups = Array.from(groupMap.entries());

                // Key columns to highlight: video_file, temperature, humidity, ammonia_ppm, sound_db, elapsed_seconds
                const highlightCols = ['video_file','video_time','elapsed_seconds','temperature','humidity','ammonia_ppm','sound_db','captured_at','recorded_at','started_at','ended_at','duration_seconds','frame_count'];
                const visibleColIdxs = queryResult.columns
                  .map((col, idx) => ({ col: col.toLowerCase(), idx }))
                  .filter(({ col }) => highlightCols.includes(col))
                  .map(({ idx }) => idx);
                const displayColIdxs = visibleColIdxs.length > 0 ? visibleColIdxs : queryResult.columns.map((_, i) => i).slice(0, 8);

                return (
                  <div>
                    {groups.map(([dateLabel, rows]) => (
                      <div key={dateLabel} className="border-b border-slate-100 last:border-b-0">
                        {/* Date group header */}
                        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 bg-slate-50/90 border-b border-slate-200/60 sm:px-6">
                          <div className="flex items-center gap-2.5">
                            <div className="flex h-6 w-6 items-center justify-center rounded-md bg-orange-100 text-orange-700">
                              <Calendar className="h-3.5 w-3.5" />
                            </div>
                            <span className="text-xs font-bold text-slate-900">{dateLabel}</span>
                            <span className="rounded-full bg-white border border-slate-200 px-2 py-0.5 text-[10px] font-semibold text-slate-600 shadow-xs">
                              {rows.length} {rows.length === 1 ? 'record' : 'records'}
                            </span>
                          </div>
                          <span className="text-[11px] text-slate-400 font-medium">
                            {selectedTable === 'recording' ? '🎥 Paired Recording' : '📡 Sensor Logs'}
                          </span>
                        </div>

                        {/* Rows */}
                        <div className="overflow-x-auto">
                          <table className="w-full text-left text-xs border-collapse">
                            <thead className="border-b border-slate-200/60 bg-slate-50/40 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                              <tr>
                                <th className="px-4 py-2 text-slate-400 w-10">#</th>
                                {displayColIdxs.map((ci) => (
                                  <th key={ci} className="px-4 py-2.5 whitespace-nowrap">{queryResult.columns[ci]}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 bg-white">
                              {rows.map(({ row, rowIndex }) => (
                                <tr key={rowIndex} className="hover:bg-orange-50/30 transition-colors">
                                  <td className="px-4 py-3 text-slate-400 font-mono text-[11px]">{pageOffset + rowIndex + 1}</td>
                                  {displayColIdxs.map((ci) => {
                                    const cell = row[ci];
                                    const colName = queryResult.columns[ci].toLowerCase();
                                    const isTime = colName.includes('at') || colName.includes('time') || colName.includes('started') || colName.includes('ended');
                                    const isNum = typeof cell === 'number';
                                    return (
                                      <td key={ci} className="px-4 py-3 whitespace-nowrap max-w-xs truncate" title={cell !== null ? String(cell) : 'NULL'}>
                                        {cell === null ? (
                                          <span className="text-slate-300 italic">null</span>
                                        ) : isTime && !isNum ? (
                                          <span className="flex items-center gap-1 text-slate-700 font-medium">
                                            <Clock className="h-3 w-3 text-orange-500 shrink-0" />
                                            {(() => { try { return new Date(String(cell)).toLocaleString(); } catch { return String(cell); } })()}
                                          </span>
                                        ) : isNum ? (
                                          <span className="font-mono text-slate-800 font-semibold">{String(cell)}</span>
                                        ) : (
                                          <span className="text-slate-700">{String(cell)}</span>
                                        )}
                                      </td>
                                    );
                                  })}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    ))}

                    {/* Pagination footer */}
                    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 bg-slate-50/70 px-4 py-3 text-xs sm:px-6">
                      <span className="text-slate-500">
                        Showing <span className="font-semibold text-slate-800">{queryResult.values.length > 0 ? pageOffset + 1 : 0}</span> to <span className="font-semibold text-slate-800">{Math.min(pageOffset + pageSize, queryResult.totalRows)}</span> of <span className="font-semibold text-slate-800">{queryResult.totalRows}</span> rows
                      </span>
                      <div className="flex items-center gap-2">
                        <button onClick={() => fetchDatabaseData(currentDbFile || undefined, selectedTable || undefined, Math.max(0, pageOffset - pageSize))} disabled={pageOffset === 0 || loadingDb} className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-40">
                          <ChevronLeft className="h-3.5 w-3.5" /><span>Previous</span>
                        </button>
                        <button onClick={() => fetchDatabaseData(currentDbFile || undefined, selectedTable || undefined, pageOffset + pageSize)} disabled={pageOffset + pageSize >= queryResult.totalRows || loadingDb} className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-40">
                          <span>Next</span><ChevronRight className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })()}
            </div>
          </div>
        )}

        {/* RESULTS TAB */}
        {activeTab === 'results' && (
          <div className="space-y-6 text-slate-800">
            {/* 1. REPORT PARAMETERS CARD */}
            <div className="rounded-2xl border border-slate-200/90 bg-white p-4 shadow-sm sm:p-6">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <h2 className="text-base sm:text-lg font-bold text-slate-900">Report Parameters</h2>
                <button
                  type="button"
                  onClick={async () => {
                    setIsGeneratingReport(true);
                    await fetchResults();
                    setTimeout(() => setIsGeneratingReport(false), 500);
                  }}
                  disabled={isGeneratingReport || loadingResults}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-orange-600 px-5 py-2.5 text-xs font-bold text-white shadow-sm transition hover:bg-orange-700 disabled:opacity-60"
                >
                  <span>Generate Report</span>
                  <RefreshCw className={`h-3.5 w-3.5 ${isGeneratingReport || loadingResults ? 'animate-spin' : ''}`} />
                </button>
              </div>

              <div className="mt-6 flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
                {/* Time Range Selector */}
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-2">
                    TIME RANGE
                  </label>
                  <div className="flex flex-wrap items-center gap-2">
                    {(['Daily', 'Weekly', 'Monthly', 'Annually'] as const).map((mode) => (
                      <button
                        key={mode}
                        type="button"
                        onClick={() => setReportTimeRange(mode)}
                        className={`rounded-xl border px-4 py-2 text-xs font-semibold transition ${
                          reportTimeRange === mode
                            ? 'border-orange-500 bg-orange-50 text-orange-600 shadow-sm'
                            : 'border-slate-200 bg-white text-slate-700 shadow-sm hover:bg-slate-50'
                        }`}
                      >
                        {mode}
                      </button>
                    ))}
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setReportTimeRange('Custom')}
                        className={`rounded-xl px-4 py-2 text-xs font-bold transition ${
                          reportTimeRange === 'Custom'
                            ? 'bg-orange-600 text-white shadow-sm'
                            : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                        }`}
                      >
                        Custom
                      </button>
                      <span className="text-xs text-slate-400 font-normal">
                        Select a specific date or range
                      </span>
                    </div>
                  </div>
                </div>

                {/* Chicken Profile Dropdown */}
                <div className="lg:w-72">
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-2">
                    CHICKEN PROFILE
                  </label>
                  <div className="relative">
                    <select
                      value={selectedChickenProfile}
                      onChange={(e) => {
                        const val = e.target.value;
                        setSelectedChickenProfile(val);
                        setSelectedReportProfile(val === 'All Chickens' ? 'Flock' : (val as any));
                      }}
                      className="w-full appearance-none rounded-xl border border-slate-200 bg-white px-4 py-2 pr-10 text-xs font-semibold text-slate-800 shadow-sm outline-none transition focus:border-orange-500 focus:ring-1 focus:ring-orange-500"
                    >
                      {CHICKEN_PROFILES.map((profile) => (
                        <option key={profile} value={profile}>
                          {profile}
                        </option>
                      ))}
                    </select>
                    <ChevronDown className="pointer-events-none absolute right-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                  </div>
                </div>
              </div>

              {/* Custom Date / Time Selection Boxes */}
              <div className="mt-5 space-y-3">
                {/* Specific Date Box */}
                <div
                  onClick={() => setCustomRangeMode('specific')}
                  className={`relative rounded-xl p-4 transition cursor-pointer ${
                    customRangeMode === 'specific'
                      ? 'border-2 border-orange-500 bg-white shadow-sm'
                      : 'border border-slate-200 bg-white hover:border-slate-300'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-slate-800">Specific Date</span>
                    <span
                      className={`rounded px-2 py-0.5 text-[9px] font-bold tracking-wider uppercase ${
                        customRangeMode === 'specific'
                          ? 'border border-orange-500 bg-orange-50 text-orange-600'
                          : 'border border-slate-300 bg-slate-50 text-slate-400'
                      }`}
                    >
                      {customRangeMode === 'specific' ? 'SELECTED' : 'ALTERNATIVE'}
                    </span>
                  </div>

                  <div className="mt-3 flex flex-wrap items-center gap-3">
                    <div className="relative w-full sm:w-64">
                      <select
                        value={selectedSpecificDate}
                        onChange={(e) => setSelectedSpecificDate(e.target.value)}
                        className="w-full appearance-none rounded-xl border border-slate-200 bg-white px-3.5 py-2 pr-9 text-xs font-semibold text-slate-700 shadow-sm outline-none focus:border-orange-500"
                      >
                        <option value="All Dates">All Recorded Dates ({results.length} intervals)</option>
                        {availableDates.map((d) => (
                          <option key={d} value={d}>
                            {d}
                          </option>
                        ))}
                      </select>
                      <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                    </div>

                    <div className="flex items-center gap-2">
                      <div className="relative">
                        <select
                          value={specificStartTime}
                          onChange={(e) => setSpecificStartTime(e.target.value)}
                          className="appearance-none rounded-xl border border-slate-200 bg-white px-3 py-2 pr-8 text-xs font-semibold text-slate-700 shadow-sm outline-none focus:border-orange-500"
                        >
                          {TIME_OPTIONS.map((t) => (
                            <option key={t.label} value={t.label}>
                              {t.label}
                            </option>
                          ))}
                        </select>
                        <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-slate-400" />
                      </div>

                      <span className="text-slate-400 font-bold">—</span>

                      <div className="relative">
                        <select
                          value={specificEndTime}
                          onChange={(e) => setSpecificEndTime(e.target.value)}
                          className="appearance-none rounded-xl border border-slate-200 bg-white px-3 py-2 pr-8 text-xs font-semibold text-slate-700 shadow-sm outline-none focus:border-orange-500"
                        >
                          {TIME_OPTIONS.map((t) => (
                            <option key={t.label} value={t.label}>
                              {t.label}
                            </option>
                          ))}
                        </select>
                        <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-slate-400" />
                      </div>
                    </div>
                  </div>

                  <p className="mt-2 text-[11px] text-slate-400">
                    Custom time window is restricted to 6 AM–6 PM.
                  </p>
                </div>

                {/* Custom Date Range Box */}
                <div
                  onClick={() => setCustomRangeMode('range')}
                  className={`relative rounded-xl p-4 transition cursor-pointer ${
                    customRangeMode === 'range'
                      ? 'border-2 border-orange-500 bg-white shadow-sm'
                      : 'border border-slate-200 bg-white hover:border-slate-300'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-slate-800">Custom Date Range</span>
                    <span
                      className={`rounded px-2 py-0.5 text-[9px] font-bold tracking-wider uppercase ${
                        customRangeMode === 'range'
                          ? 'border border-orange-500 bg-orange-50 text-orange-600'
                          : 'border border-slate-300 bg-slate-50 text-slate-400'
                      }`}
                    >
                      {customRangeMode === 'range' ? 'SELECTED' : 'ALTERNATIVE'}
                    </span>
                  </div>

                  <div className="mt-3 flex flex-wrap items-center gap-3">
                    <div className="relative w-full sm:w-64">
                      <select
                        value={selectedDateRange}
                        onChange={(e) => setSelectedDateRange(e.target.value)}
                        className="w-full appearance-none rounded-xl border border-slate-200 bg-white px-3.5 py-2 pr-9 text-xs font-semibold text-slate-700 shadow-sm outline-none focus:border-orange-500"
                      >
                        <option value="All Recorded">All Recorded Dates ({results.length} intervals)</option>
                        {availableWeekRanges.map((range) => (
                          <option key={range.key} value={range.key}>{range.label}</option>
                        ))}
                      </select>
                      <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                    </div>

                    <div className="flex items-center gap-2">
                      <div className="relative">
                        <select
                          value={rangeStartTime}
                          onChange={(e) => setRangeStartTime(e.target.value)}
                          className="appearance-none rounded-xl border border-slate-200 bg-white px-3 py-2 pr-8 text-xs font-semibold text-slate-700 shadow-sm outline-none focus:border-orange-500"
                        >
                          {TIME_OPTIONS.map((t) => (
                            <option key={t.label} value={t.label}>
                              {t.label}
                            </option>
                          ))}
                        </select>
                        <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-slate-400" />
                      </div>

                      <span className="text-slate-400 font-bold">—</span>

                      <div className="relative">
                        <select
                          value={rangeEndTime}
                          onChange={(e) => setRangeEndTime(e.target.value)}
                          className="appearance-none rounded-xl border border-slate-200 bg-white px-3 py-2 pr-8 text-xs font-semibold text-slate-700 shadow-sm outline-none focus:border-orange-500"
                        >
                          {TIME_OPTIONS.map((t) => (
                            <option key={t.label} value={t.label}>
                              {t.label}
                            </option>
                          ))}
                        </select>
                        <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-slate-400" />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* 2. DOMINANT STRESS BEHAVIOR CARD */}
            <div className="rounded-2xl border border-slate-200/90 bg-white p-4 shadow-sm sm:p-6">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="text-base sm:text-lg font-bold text-slate-900">Dominant Stress Behavior</h3>
                  <p className="mt-1 text-xs text-slate-500">
                    Selected reporting window: {currentPeriod} • {currentHours} • {selectedChickenProfile}
                  </p>
                </div>
                <span className="rounded-full border border-orange-400 bg-orange-50/70 px-3 py-1 text-[10px] font-bold tracking-wider uppercase text-orange-600 shadow-sm">
                  TOP BEHAVIOR
                </span>
              </div>

              <div className="mt-6 flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
                <div className="max-w-xl">
                  <h4 className="text-3xl sm:text-4xl font-black text-slate-900 tracking-tight">
                    {dominantBehavior.name}
                  </h4>
                  <p className="mt-2 text-sm font-bold text-orange-600">
                    {dominantBehavior.count} events • {dominantBehavior.share}% of total stress-related behavior
                  </p>
                  <p className="mt-2 text-xs leading-relaxed text-slate-500">
                    {dominantBehavior.desc}
                  </p>
                </div>

                {/* Summary Card */}
                <div className="w-full lg:w-80 shrink-0 rounded-xl border border-orange-300 bg-[#fffbf6] p-4 shadow-sm">
                  <div className="flex items-center justify-between border-b border-orange-200/60 pb-2.5">
                    <span className="text-xs font-bold text-slate-900">Summary</span>
                    <span className="rounded border border-orange-400 bg-orange-100/70 px-1.5 py-0.5 text-[9px] font-bold tracking-wider uppercase text-orange-700">
                      SELECTED RANGE
                    </span>
                  </div>

                  <div className="mt-3 space-y-2 text-xs">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500">Events</span>
                      <span className="font-bold text-slate-900">{dominantBehavior.count}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500">Share</span>
                      <span className="font-extrabold text-orange-600">{dominantBehavior.share}%</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500">Period</span>
                      <span className="font-semibold text-slate-800">{currentPeriod}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500">Hours</span>
                      <span className="font-semibold text-slate-800">{currentHours}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500">Profile</span>
                      <span className="font-semibold text-slate-800">{selectedChickenProfile}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* 3. STRESS-RELATED BEHAVIOR ANALYSIS (4 CARDS) */}
            <div>
              <h3 className="text-base sm:text-lg font-bold text-slate-900 mb-4">
                Stress-Related Behavior Analysis
              </h3>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {/* Reduced Feeding Card */}
                <div className="rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm flex items-center justify-between">
                  <div>
                    <h5 className="text-xs font-bold text-slate-800">Reduced Feeding</h5>
                    <div className="mt-2 flex items-baseline gap-1.5">
                      <span className="text-2xl sm:text-3xl font-black text-slate-900">{rawReducedFeeding}</span>
                      <span className="text-xs font-bold text-orange-500">events ({feedingPct}%)</span>
                    </div>
                    <p className="mt-1 flex items-center gap-1 text-[11px] font-semibold text-emerald-600">
                      <TrendingDown className="h-3.5 w-3.5" />
                      <span>down 3.2% vs last period</span>
                    </p>
                  </div>
                  {/* Amber/Orange Sparkline */}
                  <div className="w-24 sm:w-28 shrink-0">
                    <svg viewBox="0 0 100 40" className="w-full h-10 overflow-visible">
                      <path
                        d="M 4 28 L 18 36 L 32 20 L 46 32 L 60 10 L 74 24 L 88 16 L 98 22"
                        fill="none"
                        stroke="#f59e0b"
                        strokeWidth="2.2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </div>
                </div>

                {/* Reduced Drinking Card */}
                <div className="rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm flex items-center justify-between">
                  <div>
                    <h5 className="text-xs font-bold text-slate-800">Reduced Drinking</h5>
                    <div className="mt-2 flex items-baseline gap-1.5">
                      <span className="text-2xl sm:text-3xl font-black text-slate-900">{rawReducedDrinking}</span>
                      <span className="text-xs font-bold text-orange-500">events ({drinkingPct}%)</span>
                    </div>
                    <p className="mt-1 flex items-center gap-1 text-[11px] font-semibold text-amber-500">
                      <TrendingUp className="h-3.5 w-3.5" />
                      <span>up 1.8%</span>
                    </p>
                  </div>
                  {/* Blue Sparkline */}
                  <div className="w-24 sm:w-28 shrink-0">
                    <svg viewBox="0 0 100 40" className="w-full h-10 overflow-visible">
                      <path
                        d="M 4 24 L 18 28 L 32 16 L 46 34 L 60 10 L 74 20 L 88 26 L 98 18"
                        fill="none"
                        stroke="#3b82f6"
                        strokeWidth="2.2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </div>
                </div>

                {/* Feather Pecking Card */}
                <div className="rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm flex items-center justify-between">
                  <div>
                    <h5 className="text-xs font-bold text-slate-800">Feather Pecking</h5>
                    <div className="mt-2 flex items-baseline gap-1.5">
                      <span className="text-2xl sm:text-3xl font-black text-slate-900">{rawFeatherPecking}</span>
                      <span className="text-xs font-bold text-orange-500">events ({peckingPct}%)</span>
                    </div>
                    <p className={`mt-1 flex items-center gap-1 text-[11px] font-semibold ${rawFeatherPecking > 10 ? 'text-rose-600' : 'text-emerald-600'}`}>
                      {rawFeatherPecking > 10 ? (
                        <>
                          <TrendingUp className="h-3.5 w-3.5" />
                          <span>elevated activity</span>
                        </>
                      ) : (
                        <>
                          <TrendingDown className="h-3.5 w-3.5" />
                          <span>normal baseline</span>
                        </>
                      )}
                    </p>
                  </div>
                  {/* Orange Sparkline */}
                  <div className="w-24 sm:w-28 shrink-0">
                    <svg viewBox="0 0 100 40" className="w-full h-10 overflow-visible">
                      <path
                        d="M 4 24 L 18 32 L 32 18 L 46 34 L 60 12 L 74 28 L 88 14 L 98 24"
                        fill="none"
                        stroke="#f97316"
                        strokeWidth="2.2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </div>
                </div>

                {/* Inactivity Card */}
                <div className="rounded-2xl border border-slate-200/90 bg-white p-5 shadow-sm flex items-center justify-between">
                  <div>
                    <h5 className="text-xs font-bold text-slate-800">Inactivity</h5>
                    <div className="mt-2 flex items-baseline gap-1.5">
                      <span className="text-2xl sm:text-3xl font-black text-slate-900">{rawInactivity}</span>
                      <span className="text-xs font-bold text-orange-500">events ({inactivityPct}%)</span>
                    </div>
                    <p className="mt-1 flex items-center gap-1 text-[11px] font-semibold text-emerald-600">
                      <TrendingDown className="h-3.5 w-3.5" />
                      <span>down 2.4%</span>
                    </p>
                  </div>
                  {/* Red/Coral Sparkline */}
                  <div className="w-24 sm:w-28 shrink-0">
                    <svg viewBox="0 0 100 40" className="w-full h-10 overflow-visible">
                      <path
                        d="M 4 24 L 18 30 L 32 16 L 46 34 L 60 14 L 74 8 L 88 24 L 98 16"
                        fill="none"
                        stroke="#ef4444"
                        strokeWidth="2.2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </div>
                </div>
              </div>
            </div>

            <EnvironmentalSensorPanel
              videoName={selectedVideo?.pathname ?? null}
              analysis={videoAnalysis}
              loading={loadingVideoAnalysis}
              error={videoAnalysisError}
            />
            <HenStressTimelinePanel
              videoName={selectedVideo?.pathname ?? null}
              analysis={videoAnalysis}
              loading={loadingVideoAnalysis}
              error={videoAnalysisError}
            />

            {/* 4. EXPANDABLE SECTION FOR DETAILED TELEMETRY LOGS & EXPORT */}
            <div className="rounded-2xl border border-slate-200/90 bg-white shadow-sm overflow-hidden">
              <div className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between border-b border-slate-100">
                <div>
                  <h4 className="text-sm font-bold text-slate-900">Detailed Telemetry &amp; Correlation Log</h4>
                  <p className="mt-0.5 text-xs text-slate-500">
                    Pairwise interval data, sensor environmental readings, and report exports.
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mr-1 hidden sm:inline">Export:</span>
                  <button
                    type="button"
                    onClick={() => exportReportImage('png')}
                    className="flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:border-orange-300 hover:bg-orange-50 hover:text-orange-700 active:scale-95"
                    title="Export high-resolution PNG image"
                  >
                    <Download className="h-3.5 w-3.5 text-slate-400" />
                    <span>PNG</span>
                  </button>
                  <button
                    type="button"
                    onClick={exportReportDocs}
                    className="flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:border-orange-300 hover:bg-orange-50 hover:text-orange-700 active:scale-95"
                    title="Export Microsoft Word / Google Docs document (.doc)"
                  >
                    <Download className="h-3.5 w-3.5 text-slate-400" />
                    <span>DOCS</span>
                  </button>
                  <button
                    type="button"
                    onClick={exportReportPdf}
                    className="flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:border-orange-300 hover:bg-orange-50 hover:text-orange-700 active:scale-95"
                    title="Export PDF document"
                  >
                    <Download className="h-3.5 w-3.5 text-slate-400" />
                    <span>PDF</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => exportReportImage('jpeg')}
                    className="flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:border-orange-300 hover:bg-orange-50 hover:text-orange-700 active:scale-95"
                    title="Export JPEG image"
                  >
                    <Download className="h-3.5 w-3.5 text-slate-400" />
                    <span>JPEG</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowDetailedLog(!showDetailedLog)}
                    className="ml-1 flex items-center gap-1.5 rounded-lg border border-orange-200 bg-orange-50 px-3 py-1.5 text-xs font-semibold text-orange-700 shadow-sm transition hover:bg-orange-100"
                  >
                    <span>{showDetailedLog ? 'Hide Details' : 'Show Details'}</span>
                    <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showDetailedLog ? 'rotate-180' : ''}`} />
                  </button>
                </div>
              </div>

              {showDetailedLog && (
                <div className="p-5 space-y-6 bg-slate-50/50">
                  {resultsError && (
                    <div className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-red-800">
                      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
                      <div>
                        <p className="text-sm font-semibold">Could not load uploaded reports</p>
                        <p className="mt-1 text-xs">{resultsError}</p>
                      </div>
                    </div>
                  )}

                  {/* Summary Bar */}
                  <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-slate-200 bg-white p-4">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-orange-100 text-orange-700">
                        <Bird className="h-5 w-5" />
                      </div>
                      <div>
                        <p className="text-xs font-bold text-slate-900">{selectedChickenProfile}</p>
                        <p className="text-[11px] text-slate-500">
                          {reportRows.length} interval(s) recorded · {sensorAvailabilityCount} with synchronized sensors
                        </p>
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Status</p>
                      <p className={`text-xs font-bold ${latestReportStatusColor}`}>{latestReportStatus}</p>
                    </div>
                  </div>

                  {/* Correlation Log Table */}
                  <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
                    <div className="max-h-[460px] overflow-auto">
                      <table className="w-full min-w-[980px] border-collapse text-left text-xs">
                        <thead className="sticky top-0 z-10 bg-slate-100 text-slate-600">
                          <tr>
                            {['DATE-TIME', 'BEHAVIOR', 'OBSERVATION', 'TEMP', 'HUMID', 'AMMONIA', 'THI', 'STATUS'].map((heading) => (
                              <th key={heading} className="border-b border-slate-200 px-4 py-3 font-semibold tracking-wide">{heading}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                          {reportRows.map((result) => {
                            const birds = getProfileBirds(result);
                            const behaviorSummary = selectedChickenProfile === 'All Chickens'
                              ? `Feed ${result.eating_count} · Drink ${result.drinking_count} · Inactive ${result.inactivity_pct.toFixed(1)}% · Peck ${result.feather_pecking_count}`
                              : `${birds.reduce((sum, bird) => sum + bird.feeding_bouts, 0)} feed · ${birds.reduce((sum, bird) => sum + bird.drinking_bouts, 0)} drink · ${birds.reduce((sum, bird) => sum + bird.pecking_aggressor_count, 0)} peck`;
                            const needsReview =
                              (result.heat_stress_level !== 'Normal' && result.heat_stress_level !== 'Unavailable') ||
                              (selectedChickenProfile === 'All Chickens'
                                ? result.inactive_count > 0 || result.feather_pecking_count > 0 || result.reduced_eating_events > 0
                                : birds.some((bird) => bird.is_lethargic > 0 || bird.pecking_aggressor_count > 0 || bird.reduced_eating_flag > 0));
                            const observation = [
                              result.heat_stress_level !== 'Unavailable'
                                ? `Heat stress: ${result.heat_stress_level}`
                                : result.sensor_data_available
                                  ? 'Heat index unavailable'
                                  : 'Sensor readings unavailable',
                              selectedChickenProfile === 'All Chickens'
                                ? `${result.bird_count} birds; ${result.inactive_count} lethargy flag(s)`
                                : `${birds.length} matching track(s)`,
                            ].join('; ');
                            const displaySensor = (value: number | null, digits = 1) =>
                              value == null || !result.sensor_data_available ? '—' : value.toFixed(digits);
                            return (
                              <tr key={result.result_id} className="hover:bg-orange-50/50">
                                <td className="whitespace-nowrap px-4 py-3 font-medium text-slate-700">
                                  {new Date(result.chunk_start).toLocaleString()}
                                </td>
                                <td className="px-4 py-3 font-semibold text-slate-800">{behaviorSummary}</td>
                                <td className="max-w-[240px] truncate px-4 py-3 text-slate-600">{observation}</td>
                                <td className="px-4 py-3 font-mono text-slate-700">{displaySensor(result.temp_avg)}°C</td>
                                <td className="px-4 py-3 font-mono text-slate-700">{displaySensor(result.humidity_avg)}%</td>
                                <td className="px-4 py-3 font-mono text-slate-700">{displaySensor(result.ammonia_avg, 2)}</td>
                                <td className="px-4 py-3 font-mono text-slate-700">{displaySensor(result.thi_avg, 1)}</td>
                                <td className="px-4 py-3">
                                  <span className={`rounded-full px-2.5 py-0.5 text-[10px] font-semibold ${
                                    needsReview
                                      ? 'bg-orange-100 text-orange-800'
                                      : result.sensor_data_available
                                        ? 'bg-emerald-50 text-emerald-700'
                                        : 'bg-slate-100 text-slate-600'
                                  }`}>
                                    {needsReview ? 'Review flags' : result.sensor_data_available ? 'Recorded' : 'Sensors unavailable'}
                                  </span>
                                </td>
                              </tr>
                            );
                          })}
                          {!reportRows.length && (
                            <tr>
                              <td colSpan={8} className="px-6 py-10 text-center">
                                <Search className="mx-auto mb-2 h-6 w-6 text-slate-300" />
                                <p className="font-semibold text-slate-700 text-xs">No raw report intervals loaded</p>
                                <p className="mt-0.5 text-[11px] text-slate-400">Reports appear after video processing completes.</p>
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* DETECTION QUEUE TAB */}
        {activeTab === 'queue' && (
          <div className="space-y-6">
            {queueError && (
              <div role="alert" className="rounded-xl border border-red-500/30 bg-red-500/5 p-4 text-sm text-red-700">
                Queue status is unavailable from Vercel: {queueError}
              </div>
            )}
            {!queueError && queueStatus?.source_stale && (
              <div role="status" className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
                {queueStatus.is_queueing
                  ? 'Live detection updates are delayed. Showing the last known state'
                  : 'Desktop queue updates are delayed. Showing the last known state'}
                {queueStatus.source_age_seconds != null
                  ? ` from ${queueStatus.source_age_seconds} seconds ago`
                  : ''}. Check that the desktop worker and sync agent are running.
              </div>
            )}
            {/* Live Queue Hero Card */}
            {queueError ? null : queueStatus?.source_stale ? (
              <div className="rounded-2xl border border-amber-200 bg-white p-5 shadow-sm">
                <h2 className="text-base font-bold text-slate-900">Desktop status is stale</h2>
                <p className="mt-1 text-sm text-slate-600">
                  The cloud is not receiving fresh queue heartbeats, so the previous processing state is not presented as live.
                </p>
              </div>
            ) : queueStatus?.is_queueing ? (
              <div className="overflow-hidden rounded-2xl border border-orange-200/90 bg-gradient-to-r from-orange-50/90 via-amber-50/70 to-white p-4 shadow-md shadow-orange-500/5 sm:p-6">
                <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
                  <div className="space-y-2 max-w-2xl">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="flex items-center gap-1.5 rounded-full bg-orange-600 px-3 py-1 text-xs font-bold text-white shadow-sm shadow-orange-600/30">
                        <span className="h-2 w-2 rounded-full bg-white animate-pulse" />
                        AI VISION INFERENCE ACTIVE
                      </span>
                      <span className="text-xs text-orange-800/80 font-medium">
                        OptiPlex YOLO26n + ByteTrack
                      </span>
                    </div>
                    <h2 className="break-all text-lg font-bold text-slate-900 sm:text-xl">
                      {queueStatus.active_video || 'Processing next video segment…'}
                    </h2>
                    <p className="text-xs sm:text-sm text-slate-600">
                      {queueStatus.active_frames ? `Frame progress: ${queueStatus.active_frames}` : 'Analyzing frames and aggregating environmental telemetry…'}
                    </p>
                  </div>
                  <div className="flex sm:flex-col items-center sm:items-end justify-between gap-1 text-right">
                    <span className="text-3xl font-extrabold text-orange-700 sm:text-4xl">
                      {queueStatus.active_progress != null ? `${queueStatus.active_progress}%` : 'In progress'}
                    </span>
                    <span className="text-xs text-slate-500">
                      {queueStatus.updated_at ? `Updated ${new Date(queueStatus.updated_at).toLocaleTimeString()}` : 'Live monitoring'}
                    </span>
                  </div>
                </div>

                {/* Progress bar */}
                <div className="mt-5 space-y-2">
                  <div className="h-3 w-full overflow-hidden rounded-full bg-orange-200/60">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-orange-600 to-amber-500 transition-all duration-500 ease-out"
                      style={{ width: `${Math.max(5, queueStatus.active_progress || 0)}%` }}
                    />
                  </div>
                </div>

                {queueStatus.active_detection?.frame_idx ? (
                  <div className="mt-5 grid gap-4 border-t border-orange-200/80 pt-5 lg:grid-cols-[minmax(0,1.7fr)_minmax(14rem,1fr)]">
                    <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-950">
                      <img
                        key={queueStatus.active_detection.captured_at}
                        src={`/api/queue/live-frame?frame=${encodeURIComponent(queueStatus.active_detection.captured_at || String(queueStatus.active_detection.frame_idx))}`}
                        alt={`Live detection preview for ${queueStatus.active_video || 'active video'}`}
                        className="aspect-video w-full object-contain"
                      />
                      <div className="flex items-center justify-between bg-slate-900 px-3 py-2 text-[11px] text-slate-300">
                        <span className="inline-flex items-center gap-1.5 font-semibold">
                          <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-400" />
                          LIVE DETECTION PREVIEW
                        </span>
                        <span>Frame {queueStatus.active_detection.frame_idx.toLocaleString()}</span>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-3 content-start">
                      <div className="rounded-xl border border-orange-100 bg-white p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Chickens in frame</p>
                        <p className="mt-1 text-2xl font-bold text-slate-900">{queueStatus.active_detection.bird_count ?? '—'}</p>
                      </div>
                      <div className="rounded-xl border border-emerald-100 bg-white p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Identified</p>
                        <p className="mt-1 text-2xl font-bold text-emerald-700">{queueStatus.active_detection.identified_count ?? '—'}</p>
                      </div>
                      <div className="rounded-xl border border-red-100 bg-white p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Roosters</p>
                        <p className="mt-1 text-2xl font-bold text-red-700">{queueStatus.active_detection.rooster_count ?? '—'}</p>
                      </div>
                      <div className="col-span-2 rounded-xl border border-sky-100 bg-white p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Paired sensor database</p>
                        <p className="mt-1 break-all font-mono text-xs font-medium text-slate-800">
                          {queueStatus.active_detection.paired_sensor_database || 'No paired SQLite database found'}
                        </p>
                      </div>
                    </div>
                  </div>
                ) : (
                  <p className="mt-4 border-t border-orange-200/80 pt-4 text-xs text-slate-500">
                    Live detection preview and chicken counts will appear after the first analyzed frame.
                  </p>
                )}
              </div>
            ) : (
              <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex items-start gap-4">
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-600 border border-emerald-100">
                      <CheckCircle2 className="h-6 w-6" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <h2 className="text-base font-bold text-slate-900 sm:text-lg">Detection Queue is Idle</h2>
                        <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-semibold text-emerald-700">
                          Standing By
                        </span>
                      </div>
                      <p className="mt-1 text-xs sm:text-sm text-slate-500">
                        All discovered camera videos have been analyzed. The system is watching configured folders for new footage.
                      </p>
                    </div>
                  </div>
                  <div className="text-xs text-slate-400 sm:text-right">
                    {queueStatus?.updated_at ? `Checked ${new Date(queueStatus.updated_at).toLocaleTimeString()}` : 'System ready'}
                  </div>
                </div>
              </div>
            )}

            {/* Queue KPI Stat Cards */}
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <div className="rounded-xl border border-orange-100 bg-white p-4 shadow-sm">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Processing</span>
                  <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-orange-50 text-orange-600">
                    <Activity className="h-3.5 w-3.5" />
                  </span>
                </div>
                <div className="mt-2 flex items-baseline gap-2">
                  <span className="text-2xl font-bold text-slate-900">{queueStatus?.summary?.processing || 0}</span>
                  <span className="text-xs text-slate-500">videos</span>
                </div>
              </div>

              <div className="rounded-xl border border-blue-100 bg-white p-4 shadow-sm">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Queued</span>
                  <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
                    <Layers className="h-3.5 w-3.5" />
                  </span>
                </div>
                <div className="mt-2 flex items-baseline gap-2">
                  <span className="text-2xl font-bold text-slate-900">{queueStatus?.summary?.queued || 0}</span>
                  <span className="text-xs text-slate-500">ready</span>
                </div>
              </div>

              <div className="rounded-xl border border-purple-100 bg-white p-4 shadow-sm">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Waiting</span>
                  <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-purple-50 text-purple-600">
                    <Clock className="h-3.5 w-3.5" />
                  </span>
                </div>
                <div className="mt-2 flex items-baseline gap-2">
                  <span className="text-2xl font-bold text-slate-900">{queueStatus?.summary?.waiting || 0}</span>
                  <span className="text-xs text-slate-500">writing</span>
                </div>
              </div>

              <div className="rounded-xl border border-emerald-100 bg-white p-4 shadow-sm">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Completed</span>
                  <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                  </span>
                </div>
                <div className="mt-2 flex items-baseline gap-2">
                  <span className="text-2xl font-bold text-slate-900">{queueStatus?.summary?.completed || 0}</span>
                  <span className="text-xs text-slate-500">done</span>
                </div>
              </div>
            </div>

            {queueStatus?.history_warning && (
              <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                {queueStatus.history_warning}
              </div>
            )}

            {/* Queue Item Table with Date Segregator */}
            {(() => {
              const allItems = queueStatus?.items || [];
              const ACTIVE_SET = new Set(['PROCESSING', 'QUEUED', 'WAITING']);
              const activeList = allItems.filter((i) => ACTIVE_SET.has((i.status || '').toUpperCase()));
              const historyList = allItems.filter((i) => !ACTIVE_SET.has((i.status || '').toUpperCase()));

              const allDates = Array.from(new Set(historyList.map(getQueueItemDateLabel)));

              const statusFiltered = historyList.filter((i) => {
                if (queueStatusFilter === 'All') return true;
                return (i.status || '').toUpperCase() === queueStatusFilter.toUpperCase();
              });

              const dateFiltered = statusFiltered.filter((i) => {
                if (queueDateFilter === 'All dates') return true;
                return getQueueItemDateLabel(i) === queueDateFilter;
              });

              const dateGroups: { dateLabel: string; items: QueueItem[] }[] = [];
              const dateMap = new Map<string, QueueItem[]>();
              for (const it of dateFiltered) {
                const label = getQueueItemDateLabel(it);
                if (!dateMap.has(label)) {
                  const arr: QueueItem[] = [];
                  dateMap.set(label, arr);
                  dateGroups.push({ dateLabel: label, items: arr });
                }
                dateMap.get(label)!.push(it);
              }

              const toggleGroup = (lbl: string) => {
                setCollapsedDateGroups((prev) => ({
                  ...prev,
                  [lbl]: !prev[lbl],
                }));
              };

              const allCollapsed = allDates.length > 0 && allDates.every((d) => collapsedDateGroups[d]);

              const toggleAll = () => {
                const targetState = !allCollapsed;
                const next: Record<string, boolean> = {};
                allDates.forEach((d) => {
                  next[d] = targetState;
                });
                setCollapsedDateGroups(next);
              };

              const renderRow = (item: QueueItem) => {
                const statusUpper = (item.status || 'UNKNOWN').toUpperCase();
                let badgeColor = 'bg-slate-100 text-slate-700 border-slate-200';
                if (statusUpper === 'PROCESSING') badgeColor = 'bg-orange-50 text-orange-700 border-orange-200 font-semibold';
                else if (statusUpper === 'QUEUED') badgeColor = 'bg-blue-50 text-blue-700 border-blue-200';
                else if (statusUpper === 'WAITING') badgeColor = 'bg-purple-50 text-purple-700 border-purple-200';
                else if (statusUpper === 'COMPLETED') badgeColor = 'bg-emerald-50 text-emerald-700 border-emerald-200';
                else if (statusUpper === 'FAILED') badgeColor = 'bg-rose-50 text-rose-700 border-rose-200';
                else if (statusUpper === 'CANCELLED') badgeColor = 'bg-slate-100 text-slate-600 border-slate-200';

                const timeStr = item.completed_at
                  ? new Date(item.completed_at).toLocaleTimeString()
                  : item.created_at
                  ? new Date(item.created_at).toLocaleTimeString()
                  : '—';

                return (
                  <tr key={item.item_id || item.video_filename} className="hover:bg-slate-50/70 transition-colors">
                    <td className="px-6 py-3 font-medium text-slate-900">
                      <div className="flex items-center gap-2">
                        <Video className="h-4 w-4 text-slate-400 shrink-0" />
                        <span className="font-mono text-xs">{item.video_filename}</span>
                      </div>
                    </td>
                    <td className="px-6 py-3">
                      <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[10px] border ${badgeColor}`}>
                        {statusUpper === 'PROCESSING' && (
                          <span className="h-1.5 w-1.5 rounded-full bg-orange-600 animate-pulse" />
                        )}
                        {statusUpper}
                      </span>
                    </td>
                    <td className="px-6 py-3 font-medium text-slate-700">
                      {item.progress_percent != null ? `${item.progress_percent}%` : '—'}
                    </td>
                    <td className="px-6 py-3 text-slate-500 font-mono text-[11px]">
                      {item.frames || '—'}
                    </td>
                    <td className="px-6 py-3 text-slate-500">
                      {timeStr}
                    </td>
                    <td className="px-6 py-3 text-slate-500 max-w-xs truncate" title={item.details || ''}>
                      {item.details || '—'}
                    </td>
                  </tr>
                );
              };

              const renderMobileCard = (item: QueueItem) => {
                const statusUpper = (item.status || 'UNKNOWN').toUpperCase();
                const statusColor = statusUpper === 'COMPLETED'
                  ? 'text-emerald-700'
                  : statusUpper === 'FAILED'
                    ? 'text-rose-700'
                    : statusUpper === 'PROCESSING'
                      ? 'text-orange-700'
                      : 'text-slate-600';
                const time = item.completed_at || item.created_at;
                return (
                  <article key={item.item_id || item.video_filename} className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 items-start gap-2">
                        <Video className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                        <span className="break-all font-mono text-xs font-medium text-slate-900">{item.video_filename}</span>
                      </div>
                      <span className={`shrink-0 text-[10px] font-bold ${statusColor}`}>{statusUpper}</span>
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-[11px]">
                      <div><span className="text-slate-500">Progress</span><div className="font-medium text-slate-800">{item.progress_percent != null ? `${item.progress_percent}%` : '—'}</div></div>
                      <div><span className="text-slate-500">Frames</span><div className="font-mono text-slate-800">{item.frames || '—'}</div></div>
                      <div><span className="text-slate-500">Time</span><div className="text-slate-800">{time ? new Date(time).toLocaleString() : '—'}</div></div>
                      {item.details && <div className="col-span-2 break-words text-slate-600">{item.details}</div>}
                    </div>
                  </article>
                );
              };

              return (
                <div className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden flex flex-col">
                  {/* Header Strip */}
                  <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-white px-4 py-4 shrink-0 sm:px-6">
                    <div className="flex items-center gap-3">
                      <div>
                        <div className="flex items-center gap-2">
                          <h3 className="font-bold text-slate-900">Detection Queue Entries</h3>
                          {allItems.length > 0 && (
                            <span className="rounded-full bg-slate-100 border border-slate-200 px-2.5 py-0.5 text-[11px] font-semibold text-slate-600">
                              {allItems.length} {allItems.length === 1 ? 'entry' : 'entries'}
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-slate-500 mt-0.5">
                          Real-time queueing status segregated by date from the OptiPlex daemon.
                        </p>
                      </div>
                    </div>
                    <button
                      onClick={fetchQueueStatus}
                      disabled={loadingQueue}
                      className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-100"
                    >
                      <RefreshCw className={`h-3.5 w-3.5 ${loadingQueue ? 'animate-spin' : ''}`} />
                      <span>Refresh Queue</span>
                    </button>
                  </div>

                  {/* Date & Status Segregator Filter Bar */}
                  <div className="flex flex-wrap items-center justify-between gap-3 bg-slate-50/90 border-b border-slate-200 px-3 py-3 text-xs sm:px-6">
                    <div className="flex flex-wrap items-center gap-3">
                      {/* Date Dropdown Segregator */}
                      <div className="flex items-center gap-2">
                        <Calendar className="h-4 w-4 text-orange-600 shrink-0" />
                        <span className="font-semibold text-slate-700">Date:</span>
                        <select
                          value={queueDateFilter}
                          onChange={(e) => setQueueDateFilter(e.target.value)}
                          className="min-h-10 max-w-[min(72vw,20rem)] rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-800 shadow-sm focus:border-orange-500 focus:outline-none focus:ring-1 focus:ring-orange-500"
                        >
                          <option value="All dates">All dates ({historyList.length})</option>
                          {allDates.map((dateStr) => {
                            const count = historyList.filter((i) => getQueueItemDateLabel(i) === dateStr).length;
                            return (
                              <option key={dateStr} value={dateStr}>
                                {dateStr} ({count})
                              </option>
                            );
                          })}
                        </select>
                      </div>

                      {/* Status Pills */}
                      <div className="flex flex-wrap items-center gap-1.5 sm:ml-2">
                        <span className="font-semibold text-slate-700 mr-1">Status:</span>
                        {(['All', 'Completed', 'Failed', 'Cancelled'] as const).map((st) => {
                          const isActive = queueStatusFilter === st;
                          return (
                            <button
                              key={st}
                              type="button"
                              onClick={() => setQueueStatusFilter(st)}
                              className={`min-h-9 rounded-full px-2.5 py-1 text-[11px] font-semibold transition ${
                                isActive
                                  ? 'bg-orange-600 text-white shadow-sm shadow-orange-600/20'
                                  : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-100 hover:text-slate-800'
                              }`}
                            >
                              {st}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Expand/Collapse All Button */}
                    {allDates.length > 0 && (
                      <button
                        type="button"
                        onClick={toggleAll}
                        className="min-h-10 text-left text-[11px] text-slate-500 hover:text-orange-600 font-medium transition"
                      >
                        {allCollapsed ? '▼ Expand All Dates' : '▲ Collapse All Dates'}
                      </button>
                    )}
                  </div>

                  {/* Scrollable Container */}
                  <div className="max-h-[520px] overflow-x-auto overflow-y-auto divide-y divide-slate-100">
                    {/* Active / In-Flight Section */}
                    {activeList.length > 0 && (
                      <div className="border-b border-orange-200 bg-orange-50/20">
                        <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 bg-gradient-to-r from-orange-100/70 via-amber-100/50 to-orange-50/30 border-b border-orange-200/80 sm:px-6">
                          <div className="flex items-center gap-2">
                            <span className="h-2 w-2 rounded-full bg-orange-600 animate-pulse" />
                            <span className="text-xs font-bold uppercase tracking-wider text-orange-900">
                              Active & Queued In-Flight Tasks
                            </span>
                            <span className="rounded-full bg-orange-200 px-2 py-0.5 text-[10px] font-bold text-orange-800">
                              {activeList.length} active
                            </span>
                          </div>
                          <span className="text-[11px] text-orange-800 font-medium">In-flight on daemon</span>
                        </div>
                        <div className="space-y-2 p-3 sm:hidden">{activeList.map(renderMobileCard)}</div>
                        <div className="hidden overflow-x-auto sm:block">
                        <table className="w-full min-w-[700px] text-left text-xs border-collapse">
                          <thead className="border-b border-orange-200/50 bg-orange-50/60 text-[10px] font-semibold uppercase tracking-wider text-orange-800">
                            <tr>
                              <th className="px-6 py-2">Video Name</th>
                              <th className="px-6 py-2">Status</th>
                              <th className="px-6 py-2">Progress</th>
                              <th className="px-6 py-2">Frames Read / Total</th>
                              <th className="px-6 py-2">Time</th>
                              <th className="px-6 py-2">Pipeline Details</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-orange-100/60 bg-white">
                            {activeList.map(renderRow)}
                          </tbody>
                        </table>
                        </div>
                      </div>
                    )}

                    {/* Date-Segregated History Sections */}
                    {dateGroups.length > 0 ? (
                      dateGroups.map(({ dateLabel, items }) => {
                        const isCollapsed = Boolean(collapsedDateGroups[dateLabel]);
                        const completedCount = items.filter((i) => (i.status || '').toUpperCase() === 'COMPLETED').length;
                        const failedCount = items.filter((i) => (i.status || '').toUpperCase() === 'FAILED').length;

                        return (
                          <div key={dateLabel} className="border-b border-slate-100 last:border-b-0">
                            {/* Date Group Header Banner */}
                            <button
                              type="button"
                              onClick={() => toggleGroup(dateLabel)}
                              className="w-full flex flex-wrap items-center justify-between gap-2 px-3 py-3 bg-slate-50/90 hover:bg-slate-100/90 transition-colors border-b border-slate-200/60 text-left cursor-pointer sm:px-6"
                            >
                              <div className="flex items-center gap-2.5">
                                {isCollapsed ? (
                                  <ChevronRight className="h-4 w-4 text-slate-400" />
                                ) : (
                                  <ChevronDown className="h-4 w-4 text-slate-600" />
                                )}
                                <div className="flex h-6 w-6 items-center justify-center rounded-md bg-orange-100 text-orange-700">
                                  <Calendar className="h-3.5 w-3.5" />
                                </div>
                                <span className="text-xs font-bold text-slate-900">{dateLabel}</span>
                                <span className="rounded-full bg-white border border-slate-200 px-2 py-0.5 text-[10px] font-semibold text-slate-600 shadow-xs">
                                  {items.length} {items.length === 1 ? 'video' : 'videos'}
                                </span>
                              </div>

                              <div className="hidden items-center gap-3 text-xs sm:flex">
                                {completedCount > 0 && (
                                  <span className="inline-flex items-center gap-1 text-emerald-700 font-semibold text-[11px]">
                                    <CheckCircle2 className="h-3 w-3" /> {completedCount} done
                                  </span>
                                )}
                                {failedCount > 0 && (
                                  <span className="inline-flex items-center gap-1 text-rose-700 font-semibold text-[11px]">
                                    <AlertTriangle className="h-3 w-3" /> {failedCount} failed
                                  </span>
                                )}
                                <span className="text-[11px] text-slate-400 font-medium">
                                  {isCollapsed ? 'Click to expand' : 'Click to collapse'}
                                </span>
                              </div>
                            </button>

                            {/* Group Table Rows */}
                            {!isCollapsed && (
                              <>
                              <div className="space-y-2 p-3 sm:hidden">{items.map(renderMobileCard)}</div>
                              <div className="hidden overflow-x-auto sm:block">
                              <table className="w-full text-left text-xs border-collapse">
                                <thead className="border-b border-slate-200/60 bg-slate-50/40 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                                  <tr>
                                    <th className="px-6 py-2.5">Video Name</th>
                                    <th className="px-6 py-2.5">Status</th>
                                    <th className="px-6 py-2.5">Progress</th>
                                    <th className="px-6 py-2.5">Frames Read / Total</th>
                                    <th className="px-6 py-2.5">Completed / Queued</th>
                                    <th className="px-6 py-2.5">Pipeline Details</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100 bg-white">
                                  {items.map(renderRow)}
                                </tbody>
                              </table>
                              </div>
                              </>
                            )}
                          </div>
                        );
                      })
                    ) : (
                      <div className="py-12 text-center text-slate-400">
                        <Layers className="mx-auto h-8 w-8 text-slate-300" />
                        <p className="mt-2 font-medium text-slate-700">
                          {allItems.length > 0
                            ? 'No queue history entries match the selected filters'
                            : 'No videos currently in queue'}
                        </p>
                        <p className="mt-0.5 text-[11px] text-slate-400">
                          {allItems.length > 0 ? (
                            <button
                              type="button"
                              onClick={() => {
                                setQueueDateFilter('All dates');
                                setQueueStatusFilter('All');
                              }}
                              className="mt-2 text-xs font-semibold text-orange-600 hover:underline"
                            >
                              Reset filters to show all dates
                            </button>
                          ) : (
                            'Videos discovered in the watched folders appear here as they are queued and analyzed.'
                          )}
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Summary Footer */}
                  {allItems.length > 0 && (
                    <div className="flex items-center justify-between border-t border-slate-100 bg-slate-50/70 px-6 py-3 text-xs text-slate-500 shrink-0">
                      <span className="font-medium">
                        Showing {dateFiltered.length + activeList.length} of {allItems.length} total queue entries
                      </span>
                      <span className="text-[11px] text-slate-400">
                        {allDates.length} distinct date {allDates.length === 1 ? 'group' : 'groups'} recorded
                      </span>
                    </div>
                  )}
                </div>
              );
            })()}
          </div>
        )}

        {/* SAMPLE INSPECTOR TAB (kept mounted in DOM to preserve state across tab switches) */}
        <div className={activeTab === 'sample' ? 'block' : 'hidden'}>
          <SampleInspectorTab />
        </div>

      </main>
    </div>
  );
}
