import { NextRequest, NextResponse } from 'next/server';
import { AUTH_COOKIE_NAME, verifySessionToken } from '@/lib/auth';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

export async function GET(req: NextRequest) {
  const token = req.cookies.get(AUTH_COOKIE_NAME)?.value;
  if (!verifySessionToken(token)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const { searchParams } = new URL(req.url);
  const pathname = searchParams.get('pathname');

  if (!pathname) {
    return NextResponse.json({ error: 'Missing pathname parameter' }, { status: 400 });
  }

  return NextResponse.json(
    { error: 'Videos stay on the local system and are not synced to cloud storage', pathname },
    { status: 404 },
  );
}
