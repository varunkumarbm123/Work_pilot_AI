/** @type {import('next').NextConfig} */
const backendBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "");
const nextConfig = {
  async rewrites() {
    if (!backendBaseUrl) return [];
    return [{
      source: "/backend/:path*",
      destination: `${backendBaseUrl}/:path*`,
    }];
  },
};

export default nextConfig;
