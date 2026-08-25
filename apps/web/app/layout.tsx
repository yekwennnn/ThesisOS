import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  metadataBase: new URL('https://thesisos-diff-client.yekwennnn867052.chatgpt.site'),
  title: 'ThesisOS · 让投资逻辑保持诚实',
  description: '每次财报后，用十五分钟判断你的持有理由有没有改变。',
  openGraph: {
    title: 'ThesisOS · 让投资逻辑保持诚实',
    description: '每次财报后，用十五分钟判断你的持有理由有没有改变。',
    images: [{ url: '/og.png', width: 1200, height: 630, alt: 'ThesisOS 投资逻辑版本管理器' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'ThesisOS · 让投资逻辑保持诚实',
    description: '每次财报后，用十五分钟判断你的持有理由有没有改变。',
    images: ['/og.png'],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
