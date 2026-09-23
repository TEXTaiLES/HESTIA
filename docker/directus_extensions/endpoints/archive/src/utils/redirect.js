// Allow same-origin paths, or absolute http(s) URLs whose host sits under the
// shared cookie domain (the TEEXTaiLES tools). Anything else falls back to
// /archive, ?redirect_url= can't be used as an open redirect.
export const sanitizeRedirect = (raw) => {
    if (!raw || typeof raw !== 'string') return '/archive';
    if (raw.startsWith('//') || raw.startsWith('/\\')) return '/archive';
    if (raw.startsWith('/')) return raw;

    try {
        const url = new URL(raw);
        if (url.protocol !== 'https:' && url.protocol !== 'http:') return '/archive';
        const cookieDomain = (process.env.REFRESH_TOKEN_COOKIE_DOMAIN || '').replace(/^\./, '');
        if (!cookieDomain) return '/archive';
        const host = url.hostname.toLowerCase();
        if (host === cookieDomain || host.endsWith('.' + cookieDomain)) return raw;
        return '/archive';
    } catch {
        return '/archive';
    }
};
