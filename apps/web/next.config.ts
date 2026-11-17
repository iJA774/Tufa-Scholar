import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: { proxyClientMaxBodySize: "21mb" },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.TUFA_API_URL || "http://127.0.0.1:8000"}/:path*` }];
  },
};
export default nextConfig;
