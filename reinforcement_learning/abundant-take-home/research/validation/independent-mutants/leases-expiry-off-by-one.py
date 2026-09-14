from pathlib import Path
p=Path('/workspace/repo/huey/storage.py')
s=p.read_text()
assert 'token=? and expires_at>?' in s
p.write_text(s.replace('token=? and expires_at>?','token=? and expires_at>=?'))
