import jwt from 'jsonwebtoken';

export const AUTH_COOKIE_NAME = 'chickensee_auth_session';

const JWT_SECRET = process.env.JWT_SECRET || 'default-fallback-dev-secret-key-12345';
const DASHBOARD_PASSWORD = process.env.DASHBOARD_PASSWORD || 'admin';

export function verifyPassword(inputPassword: string): boolean {
  if (!inputPassword || !DASHBOARD_PASSWORD) return false;
  return inputPassword === DASHBOARD_PASSWORD;
}

export function createSessionToken(): string {
  return jwt.sign({ user: 'single_admin', role: 'admin' }, JWT_SECRET, {
    expiresIn: '7d',
  });
}

export function verifySessionToken(token: string | undefined): boolean {
  if (!token) return false;
  try {
    jwt.verify(token, JWT_SECRET);
    return true;
  } catch {
    return false;
  }
}
