# Producer box provisioning (Plex / Ubuntu)

The **producer** is the machine that runs the LLM-enriched crawl with the operator's
own credential and pushes the result back to the repo. The keyless GitHub Actions
run publishes whatever the producer pushes. This is the BYO-credential half — see
the README for the public/private split.

Target here: the Plex box (`ssh plex`, Ubuntu 24.04, x86_64).

## 1. Tooling (one-time)
```bash
sudo apt-get update && sudo apt-get install -y git
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv -> ~/.local/bin
# nodejs + gemini CLI are only needed for the optional legacy OAuth "cli" engine;
# the default "api" engine (below) needs neither.
```

## 2. Repo access via SSH deploy key (no interactive login)
```bash
ssh-keygen -t ed25519 -f ~/.ssh/technl_deploy -N "" -C "technl-producer@plex"
# Register the PUBLIC key on the repo as a deploy key WITH write access
# (done from a machine that has gh/admin: `gh repo deploy-key add` or the API).
git clone git@github.com:danielterwilliger/techNL-crawler.git ~/techNL-crawler
# Pin this key for the repo's remote:
cd ~/techNL-crawler
git config core.sshCommand "ssh -i ~/.ssh/technl_deploy -o IdentitiesOnly=yes"
```

## 3. LLM credential (the one operator secret)
Get a free Gemini API key at <https://aistudio.google.com/apikey>, then:
```bash
umask 077
cat > ~/techNL-crawler/.env.producer <<'EOF'
GEMINI_API_KEY=<YOUR_KEY>
TECHNL_LLM_ENGINE=api
EOF
```
`.env.producer` is gitignored. The `api` engine in `src/llm.py` reads `GEMINI_API_KEY`
directly (a REST call — no CLI); its quota solver rotates models to survive free-tier
limits. (The legacy `cli` engine — `TECHNL_LLM_ENGINE=cli`, OAuth via the gemini CLI —
is still supported but not the production path.)

## 4. Python + browser deps
```bash
cd ~/techNL-crawler
~/.local/bin/uv sync
~/.local/bin/uv run playwright install --with-deps chromium
```

## 5. systemd units (daily producer + local dashboard)
```bash
sudo cp deploy/technl-producer.service deploy/technl-producer.timer \
        deploy/technl-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now technl-producer.timer      # daily crawl
sudo systemctl enable --now technl-dashboard.service   # view dashboard locally
sudo ufw allow in on tailscale0 to any port 8088       # dashboard over Tailscale only
```
The public dashboard is live on GitHub Pages (see the README). The Tailscale-only
local view remains handy for previewing before a push: `http://<tailscale-ip>:8088`.

## Ops
```bash
systemctl list-timers technl-producer.timer      # next run
sudo systemctl start technl-producer.service     # run now (on-demand)
journalctl -u technl-producer.service -n 100 --no-pager   # logs
```
