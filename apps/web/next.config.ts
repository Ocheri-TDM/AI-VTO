import type { NextConfig } from "next";

function apiOrigin() {
  const configured = process.env.API_BASE_URL?.trim();
  if (!configured) {
    if (process.env.NODE_ENV !== "production") return "http://127.0.0.1:8000";
    console.warn(
      "API_BASE_URL is not configured. The frontend will render, but /api requests will return 503.",
    );
    return null;
  }
  const url = new URL(configured);
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("API_BASE_URL must use http:// or https://");
  }
  if (url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error(
      "API_BASE_URL must be an origin without credentials, a path, query parameters, or a fragment",
    );
  }
  if (process.env.VERCEL && url.protocol !== "https:") {
    throw new Error("API_BASE_URL must use https:// for a Vercel deployment");
  }
  return url.toString().replace(/\/$/, "");
}

const backend = apiOrigin();

const nextConfig: NextConfig = {
  poweredByHeader: false,
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "kz.artegifts.by" },
      { protocol: "https", hostname: "files.gifts.ru" },
      { protocol: "https", hostname: "happygifts.ru" },
      { protocol: "https", hostname: "s.a-5.ru" },
      { protocol: "https", hostname: "cdn.portobello.ru" },
      { protocol: "https", hostname: "cdn.insales-shop.ru" },
    ],
  },
  async rewrites() {
    return [{
      source: "/api/:path*",
      destination: backend ? `${backend}/api/:path*` : "/api-configuration-error",
    }];
  },
};

export default nextConfig;
