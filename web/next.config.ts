import type { NextConfig } from "next";

// cacheComponents/partialPrefetching are off on purpose: every screen reads the
// backend per request for the signed-in account (INV-8). Revisit with OQ-13.
const nextConfig: NextConfig = {
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
