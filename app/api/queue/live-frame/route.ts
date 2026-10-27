import { NextRequest, NextResponse } from 'next/server';
import { AUTH_COOKIE_NAME, verifySessionToken } from '@/lib/auth';
import { downloadStorageFile, StorageDownloadError } from '@/lib/supabase-storage';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

export async function GET(request: NextRequest) {
  const token = request.cookies.get(AUTH_COOKIE_NAME)?.value;
  if (!verifySessionToken(token)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  if (!process.env.VERCEL) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1200);
    try {
      const localResponse = await fetch('http://127.0.0.1:8000/queue/live-frame', {
        signal: controller.signal,
        cache: 'no-store',
      });
      if (localResponse.ok) {
        return new NextResponse(localResponse.body, {
          headers: {
            'Content-Type': 'image/jpeg',
            'Cache-Control': 'no-store, max-age=0',
          },
        });
      }
    } catch {
      // The local analytics service is unavailable when the portal runs remotely.
    } finally {
      clearTimeout(timeout);
    }
  }

  try {
    const image = await downloadStorageFile('results/live_detection.jpg');
    return new NextResponse(image.stream(), {
      headers: {
        'Content-Type': 'image/jpeg',
        'Cache-Control': 'no-store, max-age=0',
      },
    });
  } catch (error) {
    if (error instanceof StorageDownloadError && error.statusCode === 404) {
      return new NextResponse(null, { status: 404 });
    }

    console.error('Failed to load synchronized live detection preview:', error);
    return NextResponse.json(
      { error: 'Failed to load live detection preview' },
      { status: 503 },
    );
  }
}
