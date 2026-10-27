import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { AUTH_COOKIE_NAME } from './lib/auth';

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Allow static files, favicons, login page, and sync API endpoint
  if (
    pathname.startsWith('/_next') ||
    pathname.startsWith('/favicon.') ||
    pathname.startsWith('/icon.') ||
    pathname.startsWith('/apple-touch-icon') ||
    pathname.startsWith('/apple-icon') ||
    pathname.startsWith('/logo.') ||
    /\.(?:png|jpg|jpeg|gif|webp|ico|svg)$/i.test(pathname) ||
    pathname.startsWith('/login') ||
    pathname.startsWith('/api/detect-sample') ||
    pathname.startsWith('/api/sync') ||
    pathname === '/api/queue' ||
    pathname.startsWith('/api/cron') ||
    pathname.startsWith('/api/auth')
  ) {
    return NextResponse.next();
  }

  const token = request.cookies.get(AUTH_COOKIE_NAME)?.value;

  // If no auth token, redirect to login
  if (!token) {
    const loginUrl = new URL('/login', request.url);
    loginUrl.searchParams.set('redirect', pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|favicon.png|icon.png|apple-touch-icon.png|logo.png).*)'],
};
