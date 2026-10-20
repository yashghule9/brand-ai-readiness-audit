# Deploying to AWS Lightsail

One small Linux server running the same Docker setup as local, with Caddy in
front for HTTPS. No domain needed: the server's IP plus `.sslip.io` is a
working hostname, and Caddy gets a real certificate for it.

Expect a few dollars a month on a 2 GB plan. Check current Lightsail pricing,
and that your AWS credits cover Lightsail.

## 0. Before you start

- **Push your code to GitHub.** The server clones from there, so local commits
  that aren't pushed won't be deployed.
- **Set a budget alert.** AWS console → Billing → Budgets → Create budget →
  "Monthly cost budget", e.g. $15, with an email alert. Do this first.

## 1. Create the server

In the [Lightsail console](https://lightsail.aws.amazon.com/):

1. **Create instance.**
2. Region: the one nearest you (for India, **Mumbai, ap-south-1**).
3. Platform **Linux/Unix**, blueprint **OS Only → Ubuntu 24.04 LTS**.
4. Plan: **2 GB RAM**. 1 GB is too small for the image build and the browser.
5. Name it, e.g. `brand-audit`, and create.

Then, in the instance's **Networking** tab:

6. **Create static IP** and attach it to the instance. Without this the IP
   can change on a reboot, and the HTTPS hostname is tied to it. A static IP
   attached to a running instance costs nothing.
7. Under **IPv4 Firewall**, add a rule for **HTTPS (443)**. SSH (22) and
   HTTP (80) are open by default; keep 80, which is needed to issue the
   certificate.

Note the static IP, e.g. `13.233.10.20`. Your site will be
`https://13.233.10.20.sslip.io`.

## 2. Prepare the server

Open the instance and click **Connect using SSH** (a browser terminal). Run:

```bash
# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu

# 2 GB of swap, so the image build can't run out of memory
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**Close the terminal and reconnect**, so the docker group takes effect.

## 3. Get the code

```bash
git clone -b final_submission https://github.com/yashghule9/brand-ai-readiness-audit.git
cd brand-ai-readiness-audit
```

If the repository is private, git asks for a username and password. Use your
GitHub username and a **personal access token** (GitHub → Settings → Developer
settings → Personal access tokens) as the password. Your account password
won't work.

## 4. Configure

Create `.env` in the project folder (it is git-ignored), replacing the IP
with yours:

```bash
cat > .env <<'EOF'
SITE_ADDRESS=13.233.10.20.sslip.io
EOF
```

Later, the assistant API key goes in this same file, on its own line.

## 5. Start it

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The first build takes several minutes: it downloads a ~3.5 GB browser image.
When it's done, open `https://13.233.10.20.sslip.io`. The certificate is
issued on the first visit, which can take a few seconds.

## Day to day

| Task | Command (from the project folder) |
|---|---|
| Watch logs | `docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f` |
| Deploy new code | `git pull`, then the start command from step 5 |
| Stop | `docker compose -f docker-compose.yml -f docker-compose.prod.yml down` |

Audit history and certificates are kept in Docker volumes, so they survive
restarts and redeploys.

To save typing, add an alias once:

```bash
echo "alias prod='docker compose -f docker-compose.yml -f docker-compose.prod.yml'" >> ~/.bashrc && source ~/.bashrc
# then: prod up -d --build   /   prod logs -f   /   prod down
```

## Settings

All set in `docker-compose.prod.yml`, under `app.environment`:

| Variable | Production value | Meaning |
|---|---|---|
| `BRAIAUDIT_MAX_RENDER_PAGES` | `0` | Headless renders per audit. `0` is fast and light; `3` gives fuller results and needs the 2 GB plan |
| `BRAIAUDIT_AUDITS_PER_HOUR` | `5` | Audits one visitor can start per hour |
| `BRAIAUDIT_MAX_QUEUE` | `10` | Audits waiting or running before new ones are refused |

## Optional hardening

The crawler already refuses the cloud metadata address (`169.254.169.254`).
As a second layer, require session tokens for the metadata service, so that
even a request that somehow got through couldn't read it. From
[AWS CloudShell](https://console.aws.amazon.com/cloudshell/), in your
instance's region:

```bash
aws lightsail update-instance-metadata-options \
  --instance-name brand-audit --http-tokens required --http-put-response-hop-limit 1
```

## If something goes wrong

- **Site doesn't load at all:** check that the firewall has 443 open and that
  `SITE_ADDRESS` in `.env` matches the static IP exactly.
- **Certificate errors:** port 80 must be open, since it's used to issue the
  certificate. Then `prod logs caddy`.
- **Build fails with `EOF` fetching an image:** a transient registry error.
  Run the start command again.
- **Build killed / out of memory:** check the swap file from step 2 exists
  (`swapon --show`).
