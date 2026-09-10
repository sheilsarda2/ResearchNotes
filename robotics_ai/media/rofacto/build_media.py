#!/usr/bin/env python3
"""Build labeled, normal-speed previews of the review's official source videos.

Requires Python, Pillow, ffmpeg and ffprobe. Run from any directory:
  python3 robotics_ai/media/rofacto/build_media.py --cache /tmp/rofacto-media
Rebuild selected assets while retaining the rest of the source manifest:
  python3 robotics_ai/media/rofacto/build_media.py --cache /tmp/rofacto-media \
    --only human-retargeting hand-mesh-trajectory
Only presentation changes are made: synchronized horizontal composition for the
two gap comparisons, uniform resizing, labels outside the imagery, and encoding.
The GIFs sample at 8 fps; MP4s preserve every decoded source frame at its source
frame rate. Per-item provenance identifies examples from cited prior work.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import argparse
import hashlib
import json
import subprocess
import urllib.request
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
BASE = 'https://bjkim95.github.io/rofacto/static/videos/'
PROJECT = 'https://bjkim95.github.io/rofacto/'
AUTHOR = 'Byungjun Kim, Taeksoo Kim, Hyunsoo Cha, Hanbyul Joo'
PAPER = 'https://arxiv.org/abs/2607.22535v1'
ITEMS = [
    dict(id='action-edit', files=['counterfactual/pick_tube.mp4'], labels=['Original action', 'Edited action'], rows=['Robot\nrendering', 'Generated\nprediction'], section='action-controllability', poster_time=3.5),
    dict(id='nominal-motion', files=['gaps/a_action_outline.mp4', 'gaps/a_nominal_outline.mp4', 'gaps/a_logged_outline.mp4'], labels=['Raw targets', 'Nominal motion', 'Logged realized motion'], section='nominal-trajectory-conditioning', poster_time=2),
    dict(id='contact-gap', files=['gaps/b_nominal_outline.mp4', 'gaps/b_realized_outline.mp4'], labels=['Nominal motion', 'Logged realized motion'], section='nominal-trajectory-conditioning', poster_time=3),
    dict(id='depth-contact', files=['depth/depth_c02_full.mp4'], labels=['Without depth', 'With paired depth', 'Recorded reference'], section='impact-of-depth-conditioning', poster_time=1.8),
    dict(id='droid-comparison', files=['droid/rank041.mp4'], labels=['AdaLN prediction', 'RoFacto prediction', 'Recorded reference'], section='results', poster_time=3),
    dict(id='robocasa-comparison', files=['robocasa/rank051.mp4'], labels=['AdaLN prediction', 'RoFacto prediction', 'Simulator reference'], section='results', poster_time=2),
    dict(id='unseen-hand', files=['embodiment/hrdex_banana.mp4'], labels=['Mesh input', 'RoFacto prediction', 'Recorded reference'], section='zero-shot-embodiment-generalization', poster_time=3),
    dict(id='dual-arm', files=['embodiment/dexmimicgen_dual.mp4'], labels=['Mesh input', 'RoFacto prediction', 'Simulator reference'], section='zero-shot-embodiment-generalization', poster_time=3),
    dict(id='human-retargeting', files=['human2robot/detergent.mp4'], labels=['Recorded human demonstration', 'Generated robot video'], section='application-human-demonstration--robot-video', poster_time=2.5),
    dict(id='hand-mesh-trajectory', files=['dwm/dataset/real_comp.mp4'],
         source_urls=['https://snuvclab.github.io/dwm/static/videos/dataset/real_comp.mp4'],
         labels=['DWM cited prior work · training data triplet'],
         source_panel_labels=['Interaction Video', 'Static Scene Video', 'Hand Video'],
         source_labels_baked_in=True, source_work='Dexterous World Models (DWM)',
         relationship_to_review='Cited prior work; training-data example, not a RoFacto result or generated prediction.',
         author='Byungjun Kim, Taeksoo Kim, Junyoung Lee, Hanbyul Joo',
         paper='https://arxiv.org/abs/2512.17907',
         project='https://snuvclab.github.io/dwm/',
         project_url='https://snuvclab.github.io/dwm/#-key-insight-1-hybrid-interaction-static-paired-video-dataset',
         poster_time=3),
]

def run(args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)

def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'quiet', '-count_frames', '-select_streams', 'v:0', '-show_entries', 'format=duration:stream=width,height,r_frame_rate,nb_read_frames', '-of', 'json', str(path)]))

def font(size):
    for p in ['/System/Library/Fonts/Supplemental/Arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)

def make(item, cache):
    paths = []
    inputs = []
    source_urls = item.get('source_urls', [BASE + name for name in item['files']])
    assert len(source_urls) == len(item['files']), 'Each cached file needs a source URL'
    for name, url in zip(item['files'], source_urls):
        p = cache / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            urllib.request.urlretrieve(url, p)
        paths.append(p)
        inputs += ['-i', str(p)]
    info = [probe(p) for p in paths]
    streams = [i['streams'][0] for i in info]
    durations = [float(i['format']['duration']) for i in info]
    rates = [s['r_frame_rate'] for s in streams]
    frame_counts = [int(s['nb_read_frames']) for s in streams]
    assert len(set(rates)) == 1, 'Do not combine clips with different frame rates'
    assert len(set(frame_counts)) == 1, 'Do not combine clips with different frame counts'
    assert max(durations) - min(durations) < 0.001, 'Do not combine unequal time spans'
    frame_count = frame_counts[0]
    assert frame_count > 0, 'Source video contains no decoded frames'
    duration = float(frame_count / Fraction(rates[0]))
    native_w = sum(s['width'] for s in streams)
    native_h = streams[0]['height']
    assert all(s['height'] == native_h for s in streams)
    side = 112 if item.get('rows') else 0
    body_w = min(native_w, 1280 - side)
    body_w -= body_w % 2
    body_h = round(native_h * body_w / native_w / 2) * 2
    header_h = 44
    width, height = body_w + side, body_h + header_h
    overlay = Image.new('RGBA', (width, height))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, width, header_h), fill='#e8eeeb')
    if side:
        draw.rectangle((0, header_h, side, height), fill='#f3f4ef')
    for n, label in enumerate(item['labels']):
        x = side + (n + .5) * body_w / len(item['labels'])
        draw.text((x, header_h / 2), label, font=font(21), fill='#18392f', anchor='mm')
    for n, label in enumerate(item.get('rows', [])):
        draw.multiline_text((side / 2, header_h + (n + .5) * body_h / 2), label, font=font(19), fill='#18392f', anchor='mm', align='center', spacing=7)
    overlay_file = cache / (item['id'] + '-labels.png')
    overlay.save(overlay_file)
    if len(paths) == 1:
        graph = '[0:v]setpts=PTS-STARTPTS[joined];'
    else:
        graph = ''.join(f'[{i}:v]setpts=PTS-STARTPTS[v{i}];' for i in range(len(paths)))
        graph += ''.join(f'[v{i}]' for i in range(len(paths))) + f'hstack=inputs={len(paths)}[joined];'
    graph += f'[joined]scale={body_w}:{body_h}:flags=lanczos,pad={width}:{height}:{side}:{header_h}:color=white[body];[body][{len(paths)}:v]overlay=0:0[out]'
    video = ROOT / (item['id'] + '.mp4')
    # Some releases report a duration ending at the final frame's timestamp.
    # A duration cutoff drops that frame; limit by decoded frame count instead.
    run(['ffmpeg', '-y', '-v', 'error', *inputs, '-i', str(overlay_file), '-filter_complex', graph, '-map', '[out]', '-an', '-frames:v', str(frame_count), '-r', rates[0], '-c:v', 'libx264', '-preset', 'medium', '-crf', '19', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(video)])
    output_stream = probe(video)['streams'][0]
    assert int(output_stream['nb_read_frames']) == frame_count, 'Output lost or added frames'
    assert Fraction(output_stream['r_frame_rate']) == Fraction(rates[0]), 'Output frame rate changed'
    gif = ROOT / (item['id'] + '.gif')
    preview_width = min(width, 880 if side else 960)
    gif_filter = f'fps=8,scale={preview_width}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle'
    run(['ffmpeg', '-y', '-v', 'error', '-i', str(video), '-filter_complex', gif_filter, '-loop', '0', str(gif)])
    poster = ROOT / (item['id'] + '.jpg')
    run(['ffmpeg', '-y', '-v', 'error', '-ss', str(item['poster_time']), '-i', str(video), '-frames:v', '1', '-q:v', '2', str(poster)])
    result = dict(item)
    result['source_urls'] = source_urls
    result.setdefault('author', AUTHOR)
    result.setdefault('paper', PAPER)
    result.setdefault('project', PROJECT)
    result.setdefault('project_url', result['project'] + ('#' + item['section'] if item.get('section') else ''))
    result['source_duration_seconds'] = duration
    result['source_reported_duration_seconds'] = durations
    result['source_frame_rate'] = rates[0]
    result['source_frame_count'] = frame_count
    result['source_sha256'] = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    result['presentation'] = 'Every decoded source frame preserved at the original frame rate; resized, labels added outside imagery; gap clips synchronized from frame zero and placed side by side. No image regions removed or retouched. GIF preview sampled at 8 fps.'
    if item.get('source_labels_baked_in'):
        result['presentation'] += ' Original panel headings remain visible; the added header identifies the source as cited DWM prior work.'
    result['outputs'] = {p.suffix[1:]: dict(path=p.name, bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in [video, gif, poster]}
    print(item['id'], f'{duration:.3f}s', f'{sum(p.stat().st_size for p in [video,gif,poster])/1e6:.2f} MB', flush=True)
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=Path('/tmp/rofacto-media'))
    parser.add_argument('--only', nargs='+', choices=[item['id'] for item in ITEMS], metavar='ID', help='Rebuild only these assets; retain other manifest entries and metadata')
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / 'sources.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = dict(author=AUTHOR, paper=PAPER, project=PROJECT, retrieved='2026-09-10', media=[], method_figure=dict(source_url=PROJECT+'static/image/overview.png', output='method-overview.png', transformation='Unmodified authors\' image'))
    selected = [item for item in ITEMS if not args.only or item['id'] in args.only]
    with ThreadPoolExecutor(max_workers=2) as pool:
        rebuilt = {item['id']: item for item in pool.map(lambda item: make(item, args.cache), selected)}
    items = []
    for previous in manifest['media']:
        # Preserve unknown per-item metadata as well as untouched entries.
        items.append({**previous, **rebuilt.pop(previous['id'], {})})
    items.extend(rebuilt.values())
    manifest['media'] = items
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')

if __name__ == '__main__':
    main()
