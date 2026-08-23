/** @type {import('next').NextConfig} */
const apiTarget =
  process.env.SCENEWORKS_INTERNAL_API_URL ?? "http://127.0.0.1:8010";

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiTarget}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
