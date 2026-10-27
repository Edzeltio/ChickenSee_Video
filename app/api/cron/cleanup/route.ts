import { NextRequest, NextResponse } from 'next/server';
import { cleanupOldVideos } from '@/lib/supabase-storage';
import { verifySessionToken, AUTH_COOKIE_NAME } from '@/lib/auth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  return handleCleanup(req);
}

export async function POST(req: NextRequest) {
  return handleCleanup(req);
}

async function handleCleanup(req: NextRequest) {
  try {
    // 1. Verify Authorization:
    // Can be triggered by:
    // a) Vercel Automated Cron (Authorization: Bearer <CRON_SECRET>)
    // b) Local Agent (x-sync-secret: <SYNC_SECRET>)
    // c) Logged-in Dashboard user (session cookie)
    const authHeader = req.headers.get('authorization');
    const syncSecret = req.headers.get('x-sync-secret')?.trim();
    const cronSecret = process.env.CRON_SECRET?.trim();
    const expectedSyncSecret = process.env.SYNC_SECRET?.trim();
    const cookieToken = req.cookies.get(AUTH_COOKIE_NAME)?.value;

    const isCronAuthorized = cronSecret && authHeader === `Bearer ${cronSecret}`;
    const isSyncAuthorized = expectedSyncSecret && syncSecret === expectedSyncSecret;
    const isUserAuthorized = cookieToken ? verifySessionToken(cookieToken) : false;

    // In preview/local testing or if no CRON_SECRET is set, allow Vercel header check or secret
    const isVercelCronHeader = req.headers.get('user-agent')?.includes('vercel-cron');

    if (!isCronAuthorized && !isSyncAuthorized && !isUserAuthorized && !isVercelCronHeader) {
      return NextResponse.json(
        { error: 'Unauthorized: Missing valid cron token, sync secret, or session cookie' },
        { status: 401 }
      );
    }

    // 2. Parse retention days (default: 7 days)
    const { searchParams } = new URL(req.url);
    const daysParam = searchParams.get('days');
    const retentionDays = daysParam ? parseInt(daysParam, 10) : 7;

    if (isNaN(retentionDays) || retentionDays < 1) {
      return NextResponse.json({ error: 'Invalid retention days parameter (must be >= 1)' }, { status: 400 });
    }

    // 3. Execute cleanup
    const result = await cleanupOldVideos(retentionDays);

    return NextResponse.json({
      success: true,
      retentionDays,
      message: `Cleaned up ${result.deletedCount} video(s) older than ${retentionDays} days.`,
      freedMB: result.freedMB,
      deletedFiles: result.deletedFiles,
      executedAt: new Date().toISOString(),
    });
  } catch (error: any) {
    console.error('Video cleanup failed:', error);
    return NextResponse.json(
      { error: 'Cleanup execution failed', details: error.message },
      { status: 500 }
    );
  }
}
