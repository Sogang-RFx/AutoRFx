import type { NextConfig } from "next";

// cacheComponents/partialPrefetching are off on purpose: the mock API must stay
// fully dynamic (team filter per request, INV-8). Revisit with OQ-13.
const nextConfig: NextConfig = {
  outputFileTracingIncludes: {
    "/api/**": ["./src/fixtures/**/*"],
  },
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
