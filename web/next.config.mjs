// The browser calls /api/* on the Next.js server, which forwards to FastAPI (no CORS setup needed).
const API_URL = process.env.API_URL || 'http://127.0.0.1:8000';
export default {
  reactStrictMode: false,
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${API_URL}/api/:path*` }];
  },
};
