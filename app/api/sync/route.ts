import { NextRequest, NextResponse } from 'next/server';
import { createStorageUploadUrl } from '@/lib/supabase-storage';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  try {
    const syncSecret = req.headers.get('x-sync-secret')?.trim();
    const expectedSecret = process.env.SYNC_SECRET?.trim();

    if (!expectedSecret) {
      return NextResponse.json({ error: 'Server configuration error: SYNC_SECRET is not set in Vercel environment variables' }, { status: 500 });
    }

    if (syncSecret !== expectedSecret) {
      return NextResponse.json({ error: 'Unauthorized: Invalid sync secret' }, { status: 401 });
    }

    const rawFilename = req.headers.get('x-filename');
    if (!rawFilename) {
      return NextResponse.json({ error: 'Missing x-filename header' }, { status: 400 });
    }

    let filename: string;
    try {
      filename = decodeURIComponent(rawFilename);
    } catch {
      return NextResponse.json({ error: 'Invalid filename encoding' }, { status: 400 });
    }
    const ext = filename.split('.').pop()?.toLowerCase();

    if (
      !ext
      || !['sqlite', 'db', 'json', 'jpg'].includes(ext)
      || (ext === 'jpg' && filename !== 'live_detection.jpg')
    ) {
      return NextResponse.json(
        { error: `Unsupported file type: .${ext}. Only .sqlite, .db, analysis .json, and the live_detection.jpg preview are allowed.` },
        { status: 400 }
      );
    }

    if (filename.includes('/') || filename.includes('\\') || filename === '.' || filename === '..') {
      return NextResponse.json({ error: 'Filename must not contain a path' }, { status: 400 });
    }

    const folder = ext === 'json' || ext === 'jpg' ? 'results' : 'databases';
    const storagePath = `${folder}/${filename}`;

    if (req.headers.get('x-action') !== 'get-upload-url') {
      return NextResponse.json({ error: 'Unsupported sync action' }, { status: 400 });
    }

    const uploadUrl = await createStorageUploadUrl(storagePath);

    return NextResponse.json({
      success: true,
      message: `Upload URL created for ${filename}`,
      storagePath,
      uploadUrl,
    });
  } catch (error) {
    console.error('Error creating Supabase Storage upload URL:', error);
    return NextResponse.json(
      {
        error: 'Failed to create Supabase Storage upload URL',
        details: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    );
  }
}
