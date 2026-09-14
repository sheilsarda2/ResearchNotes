from pathlib import Path
p=Path('/workspace/repo/sqlite_utils/resumable.py')
s=p.read_text()
assert 'with db.atomic():' in s
p.write_text(s.replace('with db.atomic():','with __import__("contextlib").nullcontext():'))
