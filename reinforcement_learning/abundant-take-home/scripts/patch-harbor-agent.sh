#!/usr/bin/env bash
# Apply benchmark agent request and trajectory patches independently of the viewer.
set -euo pipefail

HARBOR_BIN="$(command -v harbor || true)"
HARBOR_PY=""
if [ -n "$HARBOR_BIN" ]; then
  HARBOR_PY="$(dirname "$(readlink -f "$HARBOR_BIN")")/python"
fi
if [ ! -x "${HARBOR_PY:-}" ]; then
  HARBOR_PY="${HOME}/.local/share/uv/tools/harbor/bin/python"
fi
HARBOR_PKG=""
if [ -x "${HARBOR_PY:-}" ]; then
  HARBOR_PKG="$("$HARBOR_PY" -c 'import harbor, pathlib; print(pathlib.Path(harbor.__file__).parent)')"
fi
if [ ! -d "$HARBOR_PKG" ]; then
  echo "[patch-harbor-agent] harbor package not found; skip"
  exit 0
fi

AGENT_PY="$HARBOR_PKG/agents/installed/mini_swe_agent.py"

"$HARBOR_PY" - "$AGENT_PY" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
# Apply independently of the trajectory marker so existing installs get this fix.
if "HARBOR_ANTHROPIC_EFFORT_PATCH" not in text:
    old_effort = '''            else:
                config_flags += (
                    f"-c model.model_kwargs.extra_body.reasoning_effort={eff} "
                )'''
    new_effort = '''            elif self.model_name.startswith("anthropic/"):
                # HARBOR_ANTHROPIC_EFFORT_PATCH: native Anthropic request fields.
                config_flags += (
                    f"-c model.model_kwargs.output_config.effort={eff} "
                    "-c model.model_kwargs.thinking.type=adaptive "
                    "-c model.model_kwargs.max_tokens=64000 "
                )
''' + old_effort
    if text.count(old_effort) != 1:
        raise SystemExit("mini_swe_agent.py effort forwarding block not found")
    text = text.replace(old_effort, new_effort, 1)
    path.write_text(text)

marker = "HARBOR_EFFORT_PATCH"
if marker in text:
    raise SystemExit(0)

old_sig = """def convert_mini_swe_agent_to_atif(
    mini_swe_agent_trajectory: dict[str, Any],
    session_id: str,
) -> Trajectory:"""
new_sig = """def convert_mini_swe_agent_to_atif(
    mini_swe_agent_trajectory: dict[str, Any],
    session_id: str,
    reasoning_effort: str | None = None,
) -> Trajectory:"""
if old_sig not in text:
    raise SystemExit("mini_swe_agent.py signature not found; skip trajectory patch")
text = text.replace(old_sig, new_sig, 1)

old_step = """            steps.append(
                Step(
                    step_id=step_id,
                    timestamp=timestamp,
                    source="agent",
                    model_name=model_name,
                    message=content,
                    reasoning_content=reasoning,
                    tool_calls=tool_calls,
                    metrics=metrics,
                )
            )"""
new_step = """            steps.append(
                Step(
                    step_id=step_id,
                    timestamp=timestamp,
                    source="agent",
                    model_name=model_name,
                    reasoning_effort=reasoning_effort,  # HARBOR_EFFORT_PATCH
                    message=content,
                    reasoning_content=reasoning,
                    tool_calls=tool_calls,
                    metrics=metrics,
                )
            )"""
if old_step not in text:
    raise SystemExit("mini_swe_agent.py step constructor not found; skip trajectory patch")
text = text.replace(old_step, new_step, 1)

old_save = """def convert_and_save_trajectory(
    mini_swe_agent_trajectory_path: Path,
    atif_trajectory_path: Path,
    session_id: str,
) -> None:"""
new_save = """def convert_and_save_trajectory(
    mini_swe_agent_trajectory_path: Path,
    atif_trajectory_path: Path,
    session_id: str,
    reasoning_effort: str | None = None,
) -> None:"""
text = text.replace(old_save, new_save, 1)

old_call = """        atif_trajectory = convert_mini_swe_agent_to_atif(
            mini_swe_agent_trajectory,
            session_id,
        )"""
new_call = """        atif_trajectory = convert_mini_swe_agent_to_atif(
            mini_swe_agent_trajectory,
            session_id,
            reasoning_effort=reasoning_effort,
        )"""
text = text.replace(old_call, new_call, 1)

old_ctx = """            convert_and_save_trajectory(
                mini_swe_agent_trajectory_path=mini_trajectory_path,
                atif_trajectory_path=atif_trajectory_path,
                session_id=session_id,
            )"""
new_ctx = """            convert_and_save_trajectory(
                mini_swe_agent_trajectory_path=mini_trajectory_path,
                atif_trajectory_path=atif_trajectory_path,
                session_id=session_id,
                reasoning_effort=self._reasoning_effort,
            )"""
text = text.replace(old_ctx, new_ctx, 1)
path.write_text(text)
PY

echo "[patch-harbor-agent] agent patches installed"
