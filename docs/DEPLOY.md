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

A mounted volume arrives **owned by root**, and the app runs as a non-root user,
so it can't write there. That bit us on the first deploy: volume attached, no
error anywhere, and files quietly landing in `/app/.miniclaude` instead.

`entrypoint.py` handles it — starts as root, chowns `STATE_DIR`, drops to
`agent`, then execs uvicorn. The app never runs as root.

Confirm it took, in the deploy logs:

```
[entrypoint] /data prepared for agent
[entrypoint] dropped to agent (10001)
```

If you instead see `WARNING could not prepare /data` or the app's own
`STATE_DIR ... is not writable`, the volume isn't mounted at `/data` — check
the mount path in Settings → Volumes.

The giveaway at runtime: ask the agent to run `pwd` and look at the path. It
should say `/data/workspaces/<id>`, not `/app/.miniclaude/workspaces/<id>`.

**4. Resource limits** (Service → Settings → Scale)

Railway bills **measured usage** — $10/GB/month RAM and $20/vCPU/month, per
second — not the limit you configure. The limit is a ceiling, not a
reservation, so raising it costs nothing and lowering it protects you.

Measured on this app:

| | |
|---|---|
| idle | 18 MB |
| after a full agent turn | 69 MB |
| each `python3` the agent spawns | ~14 MB, briefly |

**Set 512 MB / 1 vCPU.** That is roughly 7x headroom on memory, and it caps the
damage when a visitor asks the agent to run something expensive. Going higher
buys nothing; going much lower risks an OOM mid-turn.

Expected bill with serverless on: well under $1/month. Even always-on at this
footprint is ~$1-2/month, because you pay for the ~0.07 GB actually resident,
not for the 0.5 GB ceiling.

**Also set a hard usage limit** (Project → Settings → Usage). Railway takes
workloads offline when the billing cycle hits it. $5 is sensible — it is the
only Railway-side control that stops spend dead.

The app bounds its own subprocesses too, independent of all this
(`limits.py` → `ULIMIT_PREFIX`): 25 CPU-seconds and 512 MB of address space per
command, applied by the shell before the agent's command runs. The container
limit protects the host; this protects the container, so one
`python3 -c "x = [0] * 10**9"` can't take the service down.

**5. Serverless** (Service → Settings → Deploy)

Enable it. Takes effect on the next deploy.

**6. Generate a domain** (Service → Settings → Networking → Generate Domain)

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
