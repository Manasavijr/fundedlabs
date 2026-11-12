'use client';
import { useEffect, useRef } from 'react';
import MARKUP from '../lib/markup';

// The page keeps the design prototype's markup and CSS; lib/fundedlabs.js wires it to the API.
export default function Home() {
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    import('../lib/fundedlabs').then((m) => m.default());
  }, []);
  return <div style={{ display: 'contents' }} dangerouslySetInnerHTML={{ __html: MARKUP }} />;
}
