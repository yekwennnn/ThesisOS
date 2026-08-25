declare namespace Cloudflare {
  interface Env {
    DB: D1Database;
    FILES: R2Bucket;
    CUSTOMER_HTTP_THESISOS_CORE?: Fetcher;
    THESISOS_CORE_URL?: string;
    THESISOS_CORE_TOKEN?: string;
  }
}
