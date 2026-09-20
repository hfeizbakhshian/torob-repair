import type { NextConfig } from "next";

/**
 * The browser only ever talks to this origin. `/api/...` is rewritten straight through to
 * FastAPI, which keeps Set-Cookie and Cookie intact — so no open CORS policy and no direct
 * browser access to the API port are needed. Next.js applies no product rule of its own
 * here and never becomes an independent source of session or role.
 */
const backendOrigin = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendOrigin}/api/:path*` }];
  },
};

export default nextConfig;
