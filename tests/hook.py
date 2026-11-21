"""Claude Code PostToolUse hook: after an Edit/Write of a .py file in this repo, run the test suite
(quick Hypothesis profile) and refresh tests/out/decisions.csv.

Reads the hook payload on stdin. Exit 0 with a one-line summary when everything passes; exit 2 with
the failure summary on stderr when something fails (Claude Code shows that to Claude).
Run it by hand with:  echo '{"tool_input":{"file_path":"bot.py"}}' | python tests/hook.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    path = (payload.get("tool_input") or {}).get("file_path") or (payload.get("tool_response") or {}).get("filePath") or ""
    full = os.path.abspath(os.path.join(REPO, path))
    inside = os.path.normcase(full).startswith(os.path.normcase(REPO + os.sep))
    rel = os.path.relpath(full, REPO) if inside else ""
    skip_dirs = ("mechfight-kit", ".claude")  # the kit, and worktrees of other sessions under .claude/worktrees
    if not path.endswith(".py") or not inside or rel.split(os.sep)[0] in skip_dirs:
        return 0

    env = {**os.environ, "HYPOTHESIS_PROFILE": "hook", "PYTHONDONTWRITEBYTECODE": "1"}
    # -rfE lists every failure/error by name, including strict xfails that started passing (XPASS(strict)).
    tests = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "--tb=line", "-rfE", "-p", "no:cacheprovider"],
                           cwd=REPO, env=env, capture_output=True, text=True, timeout=170)
    replay = subprocess.run([sys.executable, os.path.join(HERE, "replay.py")],
                            cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
    lines = [l for l in tests.stdout.splitlines() if l.strip()]
    summary = lines[-1] if lines else "pytest produced no output"
    if tests.returncode == 0 and replay.returncode == 0:
        print(json.dumps({"systemMessage": f"tests: {summary} | {replay.stdout.strip()}"}))
        return 0
    # The short summary (-rfE) has one "FAILED test - reason" / "ERROR ..." line per problem.
    failed = [l for l in lines if l.startswith(("FAILED", "ERROR"))]
    report = "\n".join(failed[-40:] + [summary])
    if replay.returncode != 0:
        report += "\nreplay.py failed:\n" + replay.stderr[-1500:]
    print(f"Test suite after editing {os.path.relpath(full, REPO)}:\n{report}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
