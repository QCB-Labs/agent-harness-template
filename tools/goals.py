"""Small goal/project files and the operator's byte-exact legacy move."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from tools.stop_marker import atomic_write
from tools import names

CALLS = {
    "set_ambition": 'set_ambition(text="<text>")',
    "set_goal": 'set_goal(name="<name>", how="<how>")',
    "drop_goal": 'drop_goal(name="<name>", reason="<reason>")',
    "open_project": 'open_project(title="<title>", target="<target>", next_step="<next step>", serves="none", steps=[])',
    "advance_project": 'advance_project(title="<title>", next_step="<next step>", done_step="", add_steps=[])',
    "close_project": 'close_project(title="<title>", outcome="completed", reason="")',
    "serve_project": 'serve_project(title="<title>", serves="<goal name or none>")',
    "read_goal": 'read_goal(name="<name>")',
    "read_project": 'read_project(title="<title>", closed_in="")',
}
FULL_CALLS = 'read_goal(name="<name>") and read_project(title="<title>").'
UPDATE_REFUSAL = ('update_goals REFUSED: nothing was changed. Goals and projects are separate files now. '
    'Use set_ambition(text="<text>"), set_goal(name="<name>", how="<how>"), open_project(title="<title>", target="<target>", next_step="<next step>"), '
    'advance_project(title="<title>", next_step="<next step>") or close_project(title="<title>", outcome="completed"). read_goals() shows them all.')
AMBITION_OLD_LIST_REFUSAL = ('set_ambition REFUSED: nothing was changed because goals.md still has a ## Now heading '
    'and the old list has not been moved out; call update_goals(new_text="<the whole file>").')
CALLS = {key: names.render_tools(value) for key, value in CALLS.items()}
FULL_CALLS = names.render_tools(FULL_CALLS)
UPDATE_REFUSAL = names.render_tools(UPDATE_REFUSAL)
AMBITION_OLD_LIST_REFUSAL = names.render_tools(AMBITION_OLD_LIST_REFUSAL.replace("goals.md", names.rel("goals")))
NOTICES = {
    1: "Shortening step 1 applied: open projects show title, serves and next step only.",
    2: "Shortening step 2 applied: goals show name and how size; open projects show title, serves and next step only.",
    3: "Shortening step 3 applied: only goal and project summary lines that fit are shown.",
    4: "Shortening step 3 applied: the standing ambition is shown alone with counts.",
}


class GoalError(ValueError):
    pass


def refusal(tool: str, fact: str) -> str:
    return f"{names.tool(tool)} REFUSED: nothing was changed. {fact} Call {CALLS[tool]}."


def slug(name: str) -> str:
    plain = ''.join(c for c in unicodedata.normalize('NFKD', name) if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', '-', plain.lower()).strip('-')[:60].rstrip('-')


def line(value: str, label: str, *, empty: bool = False) -> None:
    if not isinstance(value, str) or '\n' in value or '\r' in value or (not empty and not value.strip()):
        raise GoalError(f"{label} must be {'an optional' if empty else 'a non-empty'} single line.")


def folders(root: Path) -> tuple[Path, Path]:
    naming = root / 'naming.json'
    data = json.loads(naming.read_bytes()) if naming.is_file() else {}
    dirs = data.get('dirs', {})
    paths = {key: names.rel(key) for key in ('goal_files', 'projects')} | data.get('paths', {})
    return tuple(root / paths.get(key, default).format(**dirs) for key, default in (
        ('goal_files', names.rel('goal_files')), ('projects', names.rel('projects'))))


def files(folder: Path) -> list[Path]:
    return sorted(folder.glob('*.md'))


def text(path: Path) -> str:
    return path.read_bytes().decode('utf-8')


def title_of(body: str) -> str:
    match = re.match(r'# ([^\r\n]+)\r?\n\r?\n', body)
    if not match:
        raise GoalError("The file has no name or title heading.")
    return match.group(1)


def named_path(folder: Path, name: str, *, closed: bool = False) -> Path:
    line(name, 'Name or title')
    key = slug(name)
    if not key:
        raise GoalError("The name or title has no letters or digits for a file name.")
    path = folder / (key + '.md')
    candidates = [path, folder / 'closed' / path.name] if closed else [path]
    for candidate in candidates:
        if candidate.is_file():
            existing = title_of(text(candidate))
            if existing != name:
                raise GoalError(f"The file name {key}.md belongs to {json.dumps(existing, ensure_ascii=False)}.")
            return candidate
    return path


def goal_how(body: str) -> str:
    title_of(body)
    return re.split(r'\r?\n\r?\n', body, maxsplit=1)[1]


def project_parts(body: str) -> dict[str, str]:
    title = title_of(body)
    serves = re.search(r'(?m)^- \*\*Serves:\*\* ([^\r\n]*)', body)
    matches = list(re.finditer(r'(?m)^## Next step\r?\n\r?\n([^\r\n]*)', body))
    next_step = matches[-1] if matches else None
    if serves is None or next_step is None:
        raise GoalError("The project file has no Serves or Next step line.")
    return {'title': title, 'serves': serves.group(1), 'next_step': next_step.group(1)}


def goal_names(root: Path) -> list[str]:
    return [title_of(text(path)) for path in files(folders(root)[0])]


def validate_serves(root: Path, serves: str) -> None:
    names = goal_names(root)
    if serves != 'none' and serves not in names:
        raise GoalError('Serves is not an existing goal. Existing goals: ' + json.dumps(names, ensure_ascii=False) + '.')


def serving_projects(root: Path, name: str) -> list[str]:
    titles = []
    for path in files(folders(root)[1]):
        body = text(path)
        serves = re.search(r'(?m)^- \*\*Serves:\*\* ([^\r\n]*)', body)
        if serves and serves.group(1) == name:
            titles.append(title_of(body))
    return titles


def serve_project(root: Path, title: str, serves: str) -> Path:
    path = named_path(folders(root)[1], title)
    if not path.is_file():
        raise GoalError("No open project has that title.")
    body = text(path)
    if not re.search(r'(?m)^- \*\*Status:\*\* open\r?$', body):
        raise GoalError("The project is not open.")
    validate_serves(root, serves)
    match = re.search(r'(?m)^- \*\*Serves:\*\* ([^\r\n]*)', body)
    if match is None:
        raise GoalError("The project file has no Serves line.")
    atomic_write(path, (body[:match.start(1)] + serves + body[match.end(1):]).encode('utf-8'))
    return path


def set_goal(root: Path, name: str, how: str) -> Path:
    path = named_path(folders(root)[0], name)
    if not isinstance(how, str):
        raise GoalError("How must be a string.")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, f'# {name}\n\n'.encode() + how.encode('utf-8'))
    return path


def open_project(root: Path, title: str, target: str, next_step: str, serves: str, steps: list[str]) -> Path:
    path = named_path(folders(root)[1], title)
    if path.exists():
        raise GoalError("A project with that title already exists.")
    validate_serves(root, serves)
    if not isinstance(target, str):
        raise GoalError("Target must be a string.")
    line(next_step, 'Next step')
    if not isinstance(steps, list):
        raise GoalError("Steps must be a list of single lines.")
    for step in steps:
        line(step, 'Each step')
    body = f'# {title}\n\n- **Status:** open\n- **Serves:** {serves}\n- **Target:** {target}\n\n## Steps\n\n'
    body += ''.join(f'- [ ] {step}\n' for step in steps)
    body += f'\n## Next step\n\n{next_step}'
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, body.encode('utf-8'))
    return path


def advance_project(root: Path, title: str, next_step: str, done_step: str, add_steps: list[str]) -> Path:
    path = named_path(folders(root)[1], title)
    if not path.is_file():
        raise GoalError("No open project has that title.")
    line(next_step, 'Next step')
    line(done_step, 'Done step', empty=True)
    if not isinstance(add_steps, list):
        raise GoalError("Add steps must be a list of single lines.")
    for step in add_steps:
        line(step, 'Each added step')
    body = text(path)
    if not re.search(r'(?m)^- \*\*Status:\*\* open\r?$', body):
        raise GoalError("The project is not open.")
    headings = list(re.finditer(r'(?m)^## Steps\r?$', body))
    start = headings[-1].start() if headings else len(body)
    section = re.compile(r'(?m)^## Steps\r?\n\r?\n(.*?)\r?\n## Next step\r?\n\r?\n([^\r\n]*)', re.DOTALL).search(body, start)
    if section is None:
        raise GoalError("The project file has no Steps or Next step section.")
    step_text = section.group(1)
    eol = '\r\n' if section.group().startswith('## Steps\r\n') else '\n'
    if done_step:
        match = re.search(r'(?m)^- \[ \] ' + re.escape(done_step) + r'(?=\r?$)', step_text)
        if match:
            start = match.start() + 3
            step_text = step_text[:start] + 'x' + step_text[start + 1:]
        else:
            if step_text and not step_text.endswith('\n'):
                step_text += eol
            step_text += f'- [x] {done_step}{eol}'
    for step in add_steps:
        if step_text and not step_text.endswith('\n'):
            step_text += eol
        step_text += f'- [ ] {step}{eol}'
    body = body[:section.start(2)] + next_step + body[section.end(2):]
    body = body[:section.start(1)] + step_text + body[section.end(1):]
    atomic_write(path, body.encode('utf-8'))
    return path


def close_project(root: Path, title: str, outcome: str, reason: str, wake: int, date: str) -> tuple[Path, Path]:
    if outcome not in {'completed', 'abandoned'}:
        raise GoalError("Outcome must be completed or abandoned.")
    if outcome == 'abandoned' and (not isinstance(reason, str) or not reason.strip()):
        raise GoalError("Abandoned needs a reason.")
    if not isinstance(reason, str):
        raise GoalError("Reason must be a string.")
    folder = folders(root)[1]
    old = named_path(folder, title)
    if not old.is_file():
        raise GoalError("No open project has that title.")
    new = folder / 'closed' / f'{old.stem}-{names.wake_id(wake)}.md'
    suffix = 2
    while new.exists():
        new = folder / 'closed' / f'{old.stem}-{names.wake_id(wake)}-{suffix}.md'
        suffix += 1
    body = text(old)
    status = re.search(r'(?m)^- \*\*Status:\*\* open(?=\r?$)', body)
    if status is None:
        raise GoalError("The project is not open.")
    replacement = f'- **Status:** {outcome} in {names.wake_id(wake)}, {date}' + (f': {reason}' if outcome == 'abandoned' else '')
    atomic_write(old, (body[:status.start()] + replacement + body[status.end():]).encode('utf-8'))
    new.parent.mkdir(parents=True, exist_ok=True)
    old.replace(new)
    return old, new


def read_project(root: Path, title: str, closed_in: str = "") -> str:
    line(title, 'Name or title')
    key = slug(title)
    if not key:
        raise GoalError("The name or title has no letters or digits for a file name.")
    line(closed_in, 'Closed in', empty=True)
    if closed_in and not re.fullmatch(rf'{re.escape(names.load()["wake_id"]["prefix"])}[0-9]+', closed_in):
        raise GoalError("Closed in must be empty or a wake number such as SR-0152.")
    folder = folders(root)[1]
    opened = folder / (key + '.md')
    if not closed_in and opened.is_file() and title_of(text(opened)) == title:
        return text(opened)
    closings = []
    for path in files(folder / 'closed'):
        body = text(path)
        if title_of(body) != title:
            continue
        status = re.search(rf'(?m)^- \*\*Status:\*\* (?:completed|abandoned) in {re.escape(names.load()["wake_id"]["prefix"])}([0-9]+),', body)
        if status is None:
            raise GoalError("The closed project file has no wake number.")
        wake = int(status.group(1))
        naming = re.fullmatch(re.escape(key) + rf'-{re.escape(names.load()["wake_id"]["prefix"])}[0-9]+(?:-([0-9]+))?', path.stem)
        suffix = int(naming.group(1) or 1) if naming else 0
        closings.append((wake, suffix, path.name, body))
    closings.sort(key=lambda entry: entry[:3], reverse=True)
    if closed_in:
        matching = [entry for entry in closings if entry[0] == int(closed_in[len(names.load()["wake_id"]["prefix"]):])]
        if not matching:
            raise GoalError("No closed project with that title was closed in that wake.")
        return matching[0][3]
    if not closings:
        raise GoalError("No open or closed project has that title.")
    answer = closings[0][3]
    if len(closings) > 1:
        answer += '\n\nEarlier closings: ' + ', '.join(names.wake_id(entry[0]) for entry in closings[1:]) + '.'
    return answer


def read_goals(root: Path, legacy: str, limit: int = 8000) -> str:
    goals, projects = folders(root)
    if not (root / names.rel('goals')).is_file() and not goals.exists() and not projects.exists():
        return legacy
    now = re.search(r'(?m)^## Now\r?$', legacy)
    ambition = legacy[:now.start()] if now else legacy
    goal_bodies = [text(path) for path in files(goals)]
    project_bodies = [text(path) for path in files(projects)]
    closed_count = len(files(projects / 'closed'))
    closed = f'Closed projects: {closed_count}.'
    if now:
        # The old list is still in goals.md: it has not been moved out. Show the
        # file whole, as before, then whatever goal and project files exist.
        # Nothing is shortened in this state, so his goals are never hidden.
        if not goal_bodies and not project_bodies and not closed_count:
            return legacy
        return '\n\n'.join([legacy, *goal_bodies, *project_bodies, closed])
    counts_line = f'Goals: {len(goal_bodies)}; open projects: {len(project_bodies)}; closed projects: {closed_count}.'
    ambition_only = '\n\n'.join((ambition, counts_line, NOTICES[4], FULL_CALLS))
    if len(ambition_only.encode('utf-8')) > limit:
        return ambition_only
    summaries = [f'Project: {p["title"]}; serves: {p["serves"]}; next step: {p["next_step"]}.'
                 for p in map(project_parts, project_bodies)]
    goal_summaries = [f'Goal: {title_of(body)}; how: {len(goal_how(body).encode("utf-8"))} UTF-8 bytes.' for body in goal_bodies]

    def render(gs: list[str], ps: list[str], step: int = 0, extra: str = '') -> str:
        parts = [ambition, *gs, *ps, closed]
        if extra:
            parts.append(extra)
        if step:
            parts += [NOTICES[step], FULL_CALLS]
        return '\n\n'.join(parts)

    for step, gs, ps in ((0, goal_bodies, project_bodies), (1, goal_bodies, summaries), (2, goal_summaries, summaries)):
        answer = render(gs, ps, step)
        if len(answer.encode('utf-8')) <= limit:
            return answer
    chosen_goals, chosen_projects = [], []
    for is_goal, item in [(True, g) for g in goal_summaries] + [(False, p) for p in summaries]:
        gs = chosen_goals + [item] if is_goal else chosen_goals
        ps = chosen_projects if is_goal else chosen_projects + [item]
        missing = f'Goals not shown: {len(goal_bodies) - len(gs)}; open projects not shown: {len(project_bodies) - len(ps)}.'
        if len(render(gs, ps, 3, missing).encode('utf-8')) <= limit:
            chosen_goals, chosen_projects = gs, ps
        else:
            continue
    missing = f'Goals not shown: {len(goal_bodies) - len(chosen_goals)}; open projects not shown: {len(project_bodies) - len(chosen_projects)}.'
    answer = render(chosen_goals, chosen_projects, 3, missing)
    return answer if len(answer.encode('utf-8')) <= limit else ambition_only


def move_old_list(root: Path, date: str) -> int:
    path = root / names.rel('goals')
    if not path.is_file():
        return 0
    original = path.read_bytes()
    match = re.search(rb'(?m)^## Now\r?$', original)
    if match is None:
        return 0
    moved = original[match.start():]
    heading = f'\n## Moved by the operator on {date}: the goals list as it stood before goals and projects became separate files\n\n'.encode('utf-8')
    archive = root / names.rel('goals_archive')
    with archive.open('ab') as handle:
        handle.write(heading + moved)
    atomic_write(path, original[:match.start()])
    return len(moved)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Move the old goals list to its archive.')
    parser.add_argument('command', choices=['move-old-list'])
    parser.add_argument('--repo-root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    moved = move_old_list(args.repo_root, datetime.now(timezone.utc).strftime('%Y-%m-%d'))
    print(f'Moved {moved} bytes.' if moved else 'Moved 0 bytes: no ## Now heading; nothing was changed.')


if __name__ == '__main__':
    main()
