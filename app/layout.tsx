import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'ChickenSee Synced Portal | Supabase Storage',
  description: 'Vercel portal for synchronized poultry behavior reports, environmental sensor readings, and SQLite data',
  icons: {
    icon: [
      { url: '/favicon.ico' },
      { url: '/favicon.png', type: 'image/png' },
      { url: '/icon.png', type: 'image/png' },
    ],
    apple: [
      { url: '/apple-touch-icon.png' },
      { url: '/apple-icon.png' },
    ],
    shortcut: ['/favicon.ico'],
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-[#f7f8fa] text-slate-900 antialiased selection:bg-orange-200 selection:text-orange-950">
        {children}
      </body>
    </html>
  );
}
