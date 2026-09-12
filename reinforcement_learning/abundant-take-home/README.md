# Abundant Take-Home Environment Setup
This README explains how to set up your development environment to properly complete our take-home assignment. If you use an editor like VS Code that supports Dev Containers, we've provided one for you to quickly get you up and running. If not, no worries! It's pretty simple to set things up locally as well.

## Background
For this assignment, you have been provided a unique key that only works against Abundant's API Gateway. This allows you to run Harbor tasks against Anthropic models without paying out of pocket. Note that the sample task's `task.toml` sets some of this information, so be sure that your new tasks also include this information.

Additionally, you can use this key with Claude Code as well. Note that this requires some additional configuration.

## Setting up your developer environment

### Prerequisites

- **Docker Desktop** (or compatible), running. Recommended: Settings → Resources → **8 GB+ memory**.

### Dev Container (recommended)

To use Dev Containers, you will need **VS Code** (or compatible editor) with the **Dev Containers** extension (`ms-vscode-remote.remote-containers`) installed. **Note for Apple Silicon:** use multi-arch base images in your task Dockerfiles (`ubuntu`, `debian`, `python`, …). amd64-only images won't run inside the container.

1. Unzip this folder and open it in VS Code.
2. Click **"Reopen in Container"** when prompted (or Cmd/Ctrl-Shift-P → *Dev Containers: Reopen in Container*). The first build takes a few minutes.
3. Your personal access token is already configured in the `.env` file — no setup needed. (If `.env` is ever missing, recreate it from `.env.example` using the token printed in `Abundant Take Home.pdf`.)
4. Open a terminal and verify everything:
   ```bash
   bash scripts/doctor.sh
   ```
5. Run the sample task (first run builds the task image — expect a few minutes):
   ```bash
   harbor run -p restaurant-weekly-cost-control-audit -a mini-swe-agent -m anthropic/claude-sonnet-5
   ```
6. Inspect the results of the sample task run to ensure that it completed successfully. You can use `harbor view jobs` to start a local browser to view results in a more human-readable format.

### Local env setup

If you want to get things locally (Docker-in-Docker can get funky sometimes), follow the instructions below.

1. Make sure Docker is running.

2. Install [uv](https://docs.astral.sh/uv/), then Harbor:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv tool install harbor==0.15.0
   ```
3. Export your credentials (the token is in this folder's `.env`, and printed in `Abundant Take Home.pdf`):
   ```bash
   export ANTHROPIC_API_KEY=<your thg_ token>
   ```
   `ANTHROPIC_API_KEY` is what Harbor forwards into trials
4. Verify your credentials against our gateway (expect `200`):
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://take-home-automation.vercel.app/v1/messages \
     -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" \
     -H "content-type: application/json" \
     -d '{"model":"claude-sonnet-5","max_tokens":1,"messages":[{"role":"user","content":"ping"}]}'
   ```
4. Work from this folder. Model routing to our gateway comes from each task's `task.toml`, so `harbor run …` behaves exactly as inside the dev container.

### Setting up Claude Code
In your local terminal window, ensure the following values are set:

```bash
export ANTHROPIC_BASE_URL=https://take-home-automation.vercel.app
export ANTHROPIC_AUTH_TOKEN=<your thg_ token>
```

Then start `claude`. The `/status` command should reflect the fact that you are querying Abundant's gateway.


## Authoring your own tasks

Copy the sample task's layout (`restaurant-weekly-cost-control-audit/`):

```
your-task/
├── instruction.md          # what the agent is asked to do
├── task.toml               # metadata, timeouts, resources — keep the sample's
│                           #   [environment.env] gateway block in yours!
├── environment/Dockerfile  # the world the agent works in (+ any data files)
├── tests/                  # verifier: test.sh + tests that compute the reward
└── solution/               # golden solution (solve.sh)
```

Run any task with plain `harbor` commands:

```bash
harbor run -p path/to/your-task -a oracle                                          # golden solution → expect reward 1
harbor run -p path/to/your-task -a nop                                             # no-op agent → expect reward 0
harbor run -p path/to/your-task -a mini-swe-agent -m anthropic/claude-sonnet-5   # model under test
```

All `harbor run` flags work as documented (e.g. `-k 3` for 3 attempts).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `doctor.sh` reports 401 | Token typo or stray whitespace in `.env` (recreate it from `.env.example` + the token in your PDF); on Windows-edited files, re-save with LF line endings. Still failing? Contact us for a fresh token. |
| Container build fails / OOM | Increase Docker Desktop memory to 8 GB+, then *Rebuild Container*. |
| `harbor: command not found` | `bash .devcontainer/post-create.sh`, then open a new terminal. |
| Disk full during runs | Inside the container: `docker system prune -af` |
| Changed `.devcontainer/` config | Cmd/Ctrl-Shift-P → *Dev Containers: Rebuild Container*. |
| Model runs fail with 401/auth errors | Run from a terminal inside the container (it exports your token); make sure your task's `task.toml` keeps the sample's `[environment.env]` gateway block; verify with `bash scripts/doctor.sh`. |
