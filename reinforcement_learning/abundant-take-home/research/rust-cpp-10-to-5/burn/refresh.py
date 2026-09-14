"""Read public Burn metadata and preserve a bounded freshness-search record."""
import concurrent.futures
import datetime
import json
import pathlib
import subprocess
import urllib.parse

DEST = pathlib.Path(__file__).parent / "sources"
REQUESTS = {
    "head": "repos/tracel-ai/burn/commits/main",
    "issue-4716": "repos/tracel-ai/burn/issues/4716",
    "comments-4716": "repos/tracel-ai/burn/issues/4716/comments?per_page=100",
    "open-prs": "repos/tracel-ai/burn/pulls?state=open&per_page=100",
}
for name, query in {
    "prs-4716": "repo:tracel-ai/burn is:pr 4716",
    "prs-remap": "repo:tracel-ai/burn is:pr remap",
    "prs-contiguous": "repo:tracel-ai/burn is:pr contiguous",
    "prs-prefix": "repo:tracel-ai/burn is:pr prefix",
}.items():
    REQUESTS[name] = "search/issues?" + urllib.parse.urlencode({"q": query, "per_page": 100})

def fetch(item):
    name, endpoint = item
    run = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True)
    record = {"url": "https://api.github.com/" + endpoint,
              "checked_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    record["data" if run.returncode == 0 else "error"] = json.loads(run.stdout) if run.returncode == 0 else run.stderr
    (DEST / (name + ".json")).write_text(json.dumps(record, indent=2) + "\n")
    data = record.get("data", {})
    if name == "head": print(name, data.get("sha"), flush=True)
    elif isinstance(data, dict) and "items" in data:
        print(name, data["total_count"], [(r["number"], r["title"], r["state"]) for r in data["items"]], flush=True)
    else: print(name, "saved" if run.returncode == 0 else "ERROR", flush=True)

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(fetch, REQUESTS.items()))
