import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  headers: async () => [{
    source: '/(.*)',
    headers: [{ key: 'Strict-Transport-Security', value: 'max-age=63072000' }]
  }]
};

export default nextConfig;
