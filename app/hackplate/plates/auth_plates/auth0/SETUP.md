# Auth0 Setup for Hackplate

This walks through setting up Auth0 to work with Hackplate's `auth0` plate, including the client grants that aren't obvious from the dashboard alone.

## 1. Create the Auth0 resources

You need **three** things in your Auth0 tenant: a Regular Web Application, a custom API, and a Machine-to-Machine application.

### A. Regular Web Application (handles login/callback)

Applications → Create Application → **Regular Web Application**

Under Settings:

| Field | Value |
|---|---|
| Allowed Callback URLs | `http://localhost:8000/auth/callback` |
| Allowed Logout URLs | `http://localhost:8000/docs` |
| Allowed Web Origins | `http://localhost:8000` (optional — only matters for browser-JS-initiated silent auth/logout) |
| Application Login URI | Not required (only relevant for third-party-initiated SSO) |
| Allowed Origins (CORS) | Leave empty — this plate does server-side redirects, never cross-origin browser JS calls to Auth0 |

Grab this app's **Domain**, **Client ID**, **Client Secret**.

### B. Custom API (becomes your `audience`)

Applications → APIs → Create API

- Identifier: e.g. `https://hackplate-api` — this exact string becomes `AUTH0_AUDIENCE`
- Signing algorithm: **RS256** (required — the plate verifies access tokens against JWKS)

### C. Machine-to-Machine Application (server-side Management API calls)

Applications → Create Application → **Machine to Machine**

- Authorize it against the **Auth0 Management API**
- Grab its **Client ID** / **Client Secret** — separate from the Regular Web App's credentials

## 2. Authorize the M2M app — two separate grants

This is the step that's easy to miss, because "Application Access" tabs exist on *every* API, and each is a distinct authorization.

### Grant 1: your custom API

Applications → APIs → your API (`https://hackplate-api`) → **Application Access** tab

- Find your **Regular Web Application** (not the M2M app!) in the list and toggle it **Authorized**
- Without this, `/auth/login` → `/auth/callback` fails with:
  ```
  invalid_request: Client "<client_id>" is not authorized to access resource server "https://hackplate-api"
  ```
- No scopes needed here (your custom API defines none by default) — 0/0 permissions granted is fine.

### Grant 2: the Management API

Applications → APIs → **Auth0 Management API** (built-in, not something you create) → **Application Access** tab

- Find your **M2M application** by its client ID and toggle it **Authorized**
- Grant scopes:
  - `update:users` — needed for profile sync on `PATCH /users/me`
  - `delete:users` — needed for account cleanup on `DELETE /users/me`
- Without this, updates/deletes still succeed locally, but Auth0-side sync silently 403s:
  ```
  POST https://<domain>/oauth/token "HTTP/1.1 403 Forbidden"
  Failed to sync user <id> to Auth0: Client error '403 Forbidden' ...
  ```
  This is caught and logged, not raised — the request still returns 200, so watch your server logs for this one.

## 3. Set your `.env`

```bash
HACKPLATE_AUTH=auth0

AUTH0_DOMAIN=dev-xxxx.us.auth0.com
AUTH0_CLIENT_ID=<Regular Web App client id>
AUTH0_CLIENT_SECRET=<Regular Web App client secret>
AUTH0_AUDIENCE=https://hackplate-api

AUTH0_M2M_CLIENT_ID=<M2M client id>
AUTH0_M2M_CLIENT_SECRET=<M2M client secret>

AUTH0_REDIRECT_URI=http://localhost:8000/docs
AUTH0_CALLBACK_URL=http://localhost:8000/auth/callback
AUTH0_SECURE_COOKIES=false
```

`AUTH0_SECURE_COOKIES=false` is correct for local HTTP dev — cookies are set `httponly`, and `secure=True` on non-HTTPS origins gets silently dropped by the browser.

## 4. Switch the plate and test

```bash
hackplate setplate auth auth0
hackplate run
```

- `GET /auth/login` → redirects to Auth0's `/authorize`
- Log in → redirects to `/auth/callback` → exchanges code for tokens, upserts user by `sub`/email, sets `id_token` + `access_token` cookies, redirects to `AUTH0_REDIRECT_URI`
- `GET /auth/logout` → clears cookies, redirects through Auth0's `/v2/logout`
- `GET /users/me`, `PATCH /users/me`, `DELETE /users/me` → authenticated via the `access_token` cookie

## Notes on token handling

- **`get_current_user`/`authenticate` verify the `access_token` cookie, not `id_token`.** The `id_token`'s `aud` is always your Client ID — it identifies the user to your app, not to your API. The `access_token` is what carries your API's audience and is the one meant for authorization.
- **Don't use `auth0.authentication.AsyncTokenVerifier`/`AsyncAsymmetricSignatureVerifier` to verify the access token.** That verifier is built specifically for OIDC **ID tokens** — it enforces an `azp`-must-equal-audience check that access tokens (which carry `azp=client_id` and a list of audiences) will always fail. Verify access tokens as plain RS256 JWTs instead, using `PyJWT` + `PyJWKClient` against the JWKS endpoint, checking `iss` and `aud` (=your API identifier) directly.
- **`Auth0SyncMixin`'s `on_after_update`/`on_after_delete` hooks run synchronously, inside the request.** They execute *before* the response is sent (they're awaited as part of `user_manager.update()`/`.delete()`), so a Management API failure won't show up as a failed request — only in your server logs — but it does add Management-API round-trip latency to every `PATCH`/`DELETE /users/me`.
- **`make_delete_me_router()` is shared across all three auth plates** (local, keycloak, auth0). If you touch its signature (e.g. adding cookie-clearing support), update the call sites in all three plates' `config.py`, not just the one you're working on. Local's Bearer-token flow doesn't need cookie clearing; Keycloak's cookie-based flow does, same as Auth0.

## Known gaps (not yet addressed)

- No Bearer-token fallback for testing protected routes via curl/Postman — Auth0 auth is cookie-only right now.
- `AUTH0_M2M_CLIENT_ID`/`SECRET` have no defaults, so a fresh clone can't do a basic login without setting up the full M2M app + both grants above.
