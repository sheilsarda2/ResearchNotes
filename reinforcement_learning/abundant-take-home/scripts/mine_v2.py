#!/usr/bin/env python3
"""Mine merged feature-scale PRs (or commits) from a repo for candidates_v2 sourcing.

Uses `gh api graphql` so one call returns 100 PRs with size/files metadata.
Output: candidates_v2/mining/<owner>__<repo>.json with filtered, scored PRs.
"""
import argparse, json, subprocess, sys, re, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'candidates_v2' / 'mining'

THEME = re.compile(r'(storage|wal\b|checkpoint|snapshot|compact|index|codec|compress|chunk|recover|durab|atomic|transact|replicat|migrat|serializ|deserializ|format|schema|ipc|parquet|mcap|rrd|blueprint|dataframe|plugin|backend|onnx|import|kernel|autodiff|quantiz|shard|consolidat|manifest|version|persist|journal|fsync|crash|resum|lease|liveliness|query|subscri|publish|router|protocol|encod|decod)', re.I)
TEST = re.compile(r'(^|/)(tests?|testing|__tests__|spec)(/|$)|_test\.|test_|\.test\.|Test\.cpp$|_tests?\.rs$|/snapshots?/', re.I)
SKIP_LABEL = re.compile(r'(dependenc|chore|ci\b|docs?\b|documentation|release|bump|typo|revert|flaky|refactor only)', re.I)
SKIP_TITLE = re.compile(r'^(chore|ci|docs?|build|release|bump|revert|deps?|test|tests)(\(|:|\s)|typo|changelog|clippy|formatting|lint fixes?|\bmove\b|\bbump\b|\bupgrade\b|moderni[sz]e|\brename\b|migrate from|switch to|\badopt\b|remove .* support|merge .* release|deprecate|centrali[sz]e|avoid port collision|msrv|edition', re.I)

QUERY = '''
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest {
      number title url mergedAt additions deletions changedFiles
      labels(first: 10) { nodes { name } }
      closingIssuesReferences(first: 5) { nodes { number title url } }
      files(first: 100) { nodes { path additions deletions } }
    } }
  }
}'''

def gh_graphql(q, after=None):
    args = ['gh', 'api', 'graphql', '-f', f'query={QUERY}', '-f', f'q={q}']
    if after:
        args += ['-f', f'after={after}']
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:500])
    return json.loads(out.stdout)['data']['search']

def mine_prs(repo, since, min_add, min_files):
    q = f'repo:{repo} is:pr is:merged merged:>={since}'
    after, rows, total = None, [], None
    while True:
        page = gh_graphql(q, after)
        total = page['issueCount']
        for pr in page['nodes']:
            if not pr: continue
            files = [f['path'] for f in pr['files']['nodes']]
            labels = [l['name'] for l in pr['labels']['nodes']]
            if pr['additions'] < min_add or pr['changedFiles'] < min_files or pr['changedFiles'] > 90: continue
            if any(SKIP_LABEL.search(l) for l in labels) or SKIP_TITLE.search(pr['title']): continue
            test_files = [f for f in files if TEST.search(f)]
            if not test_files: continue
            src_files = [f for f in files if not TEST.search(f)]
            theme_hits = sorted({m.group(0).lower() for f in src_files + [pr['title']] for m in [THEME.search(f)] if m})
            rows.append(dict(
                number=pr['number'], title=pr['title'], url=pr['url'], merged_at=pr['mergedAt'][:10],
                additions=pr['additions'], deletions=pr['deletions'], changed_files=pr['changedFiles'],
                labels=labels, issues=[dict(n=i['number'], title=i['title'], url=i['url']) for i in pr['closingIssuesReferences']['nodes']],
                src_files=src_files[:40], test_files=test_files[:20], theme_hits=theme_hits,
                horizon_score=pr['additions'] + 20 * pr['changedFiles'] + 40 * len(test_files) + 60 * len(theme_hits),
            ))
        if not page['pageInfo']['hasNextPage']: break
        after = page['pageInfo']['endCursor']
    rows.sort(key=lambda r: -r['horizon_score'])
    return total, rows

def mine_commits(repo, since, min_add, min_files, limit=400):
    """Fallback for repos that land imported commits (RocksDB)."""
    rows = []
    page = 1
    while len(rows) < limit:
        out = subprocess.run(['gh', 'api', f'repos/{repo}/commits?since={since}T00:00:00Z&per_page=100&page={page}'], capture_output=True, text=True)
        commits = json.loads(out.stdout) if out.returncode == 0 else []
        if not commits: break
        for c in commits:
            sha = c['sha']
            d = json.loads(subprocess.run(['gh', 'api', f'repos/{repo}/commits/{sha}'], capture_output=True, text=True).stdout)
            st = d.get('stats', {}); files = [f['filename'] for f in d.get('files', [])]
            if st.get('additions', 0) < min_add or len(files) < min_files: continue
            test_files = [f for f in files if TEST.search(f)]
            if not test_files: continue
            msg = d['commit']['message']; title = msg.splitlines()[0]
            if SKIP_TITLE.search(title): continue
            src_files = [f for f in files if not TEST.search(f)]
            theme_hits = sorted({m.group(0).lower() for f in src_files + [title] for m in [THEME.search(f)] if m})
            pr = re.search(r'Pull Request resolved: (\S+)', msg)
            rows.append(dict(number=sha[:10], title=title, url=pr.group(1) if pr else d['html_url'], merged_at=d['commit']['committer']['date'][:10],
                             additions=st.get('additions'), deletions=st.get('deletions'), changed_files=len(files), labels=[], issues=[],
                             src_files=src_files[:40], test_files=test_files[:20], theme_hits=theme_hits,
                             horizon_score=st.get('additions', 0) + 20 * len(files) + 40 * len(test_files) + 60 * len(theme_hits)))
        page += 1
    rows.sort(key=lambda r: -r['horizon_score'])
    return len(rows), rows

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('repos', nargs='+')
    ap.add_argument('--since', default='2026-01-01')
    ap.add_argument('--min-additions', type=int, default=200)
    ap.add_argument('--min-files', type=int, default=5)
    ap.add_argument('--commits', action='store_true', help='mine commits instead of PRs')
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for repo in a.repos:
        try:
            total, rows = (mine_commits if a.commits else mine_prs)(repo, a.since, a.min_additions, a.min_files)
        except Exception as e:
            print(f'{repo}: ERROR {e}', file=sys.stderr); continue
        path = OUT / (repo.replace('/', '__') + '.json')
        path.write_text(json.dumps(dict(repo=repo, mined_at=datetime.datetime.utcnow().isoformat() + 'Z', since=a.since,
                                        filters=dict(min_additions=a.min_additions, min_files=a.min_files, needs_test_files=True),
                                        total_merged_in_window=total, candidates=len(rows), prs=rows), indent=1))
        print(f'{repo}: {total} merged in window -> {len(rows)} feature-scale candidates -> {path.relative_to(ROOT)}')
