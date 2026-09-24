# Frontend deployment to Vercel

Only the Next.js frontend is deployed. FastAPI, PostgreSQL, Ollama, Chromium,
supplier adapters, the scheduler, and background workers remain outside Vercel.

## Import and project settings

1. Push the existing repository to GitHub without committing `.env` or `.env.local`.
2. In Vercel choose **Add New → Project** and import that repository.
3. Use these project settings:

| Setting | Value |
|---|---|
| Framework Preset | Next.js |
| Root Directory | `apps/web` |
| Install Command | `npm ci` |
| Build Command | `npm run build` |
| Output Directory | leave empty; the Next.js preset manages `.next` |
| Node.js Version | 22.x |

No `vercel.json` is required.

## Environment

The frontend uses one server-side build/deployment variable:

```text
API_BASE_URL=https://your-future-fastapi-host.example
```

The value is the public FastAPI origin without `/api`, credentials, query parameters,
or a fragment. A trailing slash is accepted and normalized. Use HTTPS in Vercel to
avoid mixed-content and insecure-cookie problems. Do not use `NEXT_PUBLIC_*` for
secrets; this project does not expose the backend origin in the browser bundle.

Add `API_BASE_URL` separately to **Production**, **Preview**, and **Development** when
a public backend exists. The same backend may be used for all three scopes, or Preview
may use a separate backend. Until then, leave it unset: the deployment renders the UI
and `/api/*` returns an explicit HTTP 503 configuration response. It never falls back
to the visitor's localhost in production.

After adding or changing the variable, open **Deployments**, select the latest
deployment, and choose **Redeploy**. Environment values are applied to the new build.

For local development, `API_BASE_URL` may be omitted; `next dev` uses
`http://127.0.0.1:8000`. For a local production-equivalent build use:

```powershell
$env:API_BASE_URL='http://127.0.0.1:8000'
npm.cmd --prefix apps/web run build
npm.cmd --prefix apps/web run start
```

## Request, SSE, and cookie path

The browser always calls same-origin `/api/*`. Next.js rewrites those requests to
`API_BASE_URL/api/*`; normal fetch, streamed chat responses, and `EventSource` share
the same route. Requests include credentials. This avoids browser CORS for the normal
Vercel architecture and lets the browser store the FastAPI session cookie for the
frontend origin.

The production backend must set secure cookies. Do not add a backend cookie `Domain`
for the Vercel hostname; allow the proxy response to create a host-only cookie. If a
future frontend bypasses the proxy and calls FastAPI directly, FastAPI must explicitly
allow the production Vercel origin and the required Preview origins with credentials,
and the cookie must use `Secure` plus an appropriate cross-site `SameSite` policy.
Never combine credentialed CORS with `Access-Control-Allow-Origin: *`.

## Images and external links

Next Image Optimization allows the image hosts observed in the current supplier
index: `kz.artegifts.by`, `files.gifts.ru`, `happygifts.ru`, `s.a-5.ru`,
`cdn.portobello.ru`, and `cdn.insales-shop.ru`. Supplier source links remain direct
external HTTPS links and are not proxied through Vercel.

## Verification after deployment

Check that `/` loads, CSS and icons render, the login/register form appears, and the
browser console has no hydration or fatal runtime errors. When `API_BASE_URL` is set,
verify login, registration, research, pagination, product details, selection, source
links, SSE updates, and logout. Without a public backend, API-dependent actions are
expected to show the configuration error.
