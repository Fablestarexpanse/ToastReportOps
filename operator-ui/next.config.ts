import type { NextConfig } from "next";

const API = process.env.TOAST_API_URL ?? "http://127.0.0.1:8400";

const nextConfig: NextConfig = {
  async rewrites() {
    // Proxy API calls through Next so the browser only ever talks to the localhost UI.
    return [{ source: "/api/:path*", destination: `${API}/:path*` }];
  },
};

export default nextConfig;
