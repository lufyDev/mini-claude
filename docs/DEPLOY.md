# Deploying to Railway

## Serverless or always-on?

**Serverless (sleep on).** A demo idles almost all the time; always-on burns
roughly $2.50–3/mo of a $5 Hobby credit to serve nobody. The cold start is a few
seconds, and the page itself loads before the agent runs, so a visitor mostly
doesn't notice.

Railway sleeps a service after ~10 minutes with **no outbound traffic** — note
outbound, not inbound. This app makes no background calls when idle, so it
sleeps properly. An app that pings a database on a timer never would.

Turn it off only if you're demoing live to someone and want the first message
instant. You can flip it either way in seconds.

### The catch nobody mentions

Railway's filesystem is ephemeral. Every sleep/wake or redeploy wipes it, which
means `usage.jsonl` — and therefore `DAILY_USD_CAP` — **resets on every wake**.
The in-memory `SESSION_MSG_CAP` counters reset too. Your cost protection
evaporates exactly when you're relying on it.

Two things fix it, and you want both:

1. **Attach a volume** (below). The ledger survives restarts and the daily cap
   becomes real.
2. **Set a hard monthly budget in the OpenAI dashboard.** This is the only
   control that is genuinely enforced no matter what your container does —
   Settings → Limits → monthly budget. The in-app caps are good UX; the
   provider-side budget is the actual guarantee.

Worth knowing the scale: on `gpt-6-luna` a 10-turn session costs about $0.006.
Even a hundred cold starts a day with every session maxed is well under a
dollar. The volume is correctness, not panic.

---

## Deploy

**1. Create the service**

Railway → New Project → Deploy from GitHub repo → `lufyDev/mini-claude`.

It finds the `Dockerfile` and `railway.toml` on its own. No build config needed.

**2. Set variables** (Service → Variables)

| Variable | Value | Why |
|---|---|---|
| `OPENAI_API_KEY` | `sk-proj-...` | injected at runtime; never in the image |
| `DEMO_MODE` | `1` | already the image default — set it explicitly so it's visible |
| `OPENAI_MODEL` | `gpt-6-luna` | the cheap one |
| `DAILY_USD_CAP` | `1.00` | across all visitors |
| `SESSION_MSG_CAP` | `10` | per visitor |
| `RATE_PER_MIN` | `6` | per IP |

Don't set `PORT` — Railway injects it and the Dockerfile reads it.

**3. Attach a volume** (Service → Settings → Volumes)

Mount path: `/data`. The image already sets `STATE_DIR=/data`.

If the mounted volume comes up root-owned, the container runs as a non-root user
and can't write to it. The app probes it at startup and logs:

```
[mini-claude] STATE_DIR=/data is not writable (...); falling back to
/app/.miniclaude. Spend caps will reset on restart.
```

Check the deploy logs for that line. If you see it, either drop the volume and
lean on the OpenAI budget cap, or remove the `USER agent` line from the
Dockerfile (you lose the non-root protection — only worth it if you care about
the daily cap more).

**4. Serverless** (Service → Settings → Deploy)

Enable it. Takes effect on the next deploy.

**5. Generate a domain** (Service → Settings → Networking → Generate Domain)

Put the URL in the README and on your portfolio site.

---

## Check it worked

```bash
curl https://<your-app>.up.railway.app/healthz
# {"ok":true}

curl "https://<your-app>.up.railway.app/api/meta?session_id=test"
# {"model":"gpt-6-luna","demo":true,"messages_left":10,...}
```

`"demo":true` is the one to confirm. If it says `false`, `DEMO_MODE` didn't take
and your service is wide open — fix it before sharing the link.

Then open the URL, send a message, and watch the Railway logs for the
`STATE_DIR` warning.

---

## If something breaks

| Symptom | Cause |
|---|---|
| Build fails on `pip install` | Railway is on a cached layer — redeploy without cache |
| 502 on first load | Cold start taking longer than the healthcheck timeout; raise `healthcheckTimeout` in `railway.toml` |
| Streaming arrives all at once | A proxy buffering SSE. The app already sends `X-Accel-Buffering: no` |
| `"demo":false` | `DEMO_MODE` not set on the service |
| Caps reset constantly | The `STATE_DIR` warning above — no writable volume |
