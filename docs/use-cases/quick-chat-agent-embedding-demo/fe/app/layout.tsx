import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Amazon Quick Suite Embedded Chat',
  description: 'Quick Suite Chat with IAM Identity Center',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
