#!/usr/bin/env python3
from collections import defaultdict
from pathlib import Path
import json
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)

HEADING = "### \U0001f4dd Complete TODO List"
HEADING_RE = re.compile(r"^#{1,6}(?:\s|$)")
BULLET_RE = re.compile(r"^- \[ \] \*\*(?P<path>.+?):(?P<line>\d+)\*\* - (?P<text>.*)$")
TODO_RE = re.compile(r"TODO")
WS_RE = re.compile(r"\s+")
TEMP_PATHS = {".github/scripts/sync_todo.py"}


def canon(text):
    return WS_RE.sub(" ", text).strip()


def extract_todo(line):
    match = TODO_RE.search(line)
    if not match:
        return None
    rest = line[match.end():]
    rest = re.sub(r"^[\s:]+", "", rest)
    return rest.rstrip()


def changed_python_files():
    subprocess.run(
        ["git", "fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main"],
        check=True,
        capture_output=True,
        text=True,
    )
    diff = subprocess.check_output(
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        text=True,
    )
    paths = []
    for raw in diff.splitlines():
        path = raw.strip()
        if path.endswith(".py") and path not in TEMP_PATHS:
            paths.append(path)
    return sorted(set(paths))


def main():
    readme_path = ROOT / "README.md"
    original_text = readme_path.read_text(encoding="utf-8")
    original_ends_with_newline = original_text.endswith("\n")
    lines = original_text.splitlines()

    heading_indices = [i for i, line in enumerate(lines) if line == HEADING]
    if len(heading_indices) != 1:
        close = [(i + 1, line) for i, line in enumerate(lines) if "Complete TODO List" in line]
        raise SystemExit(
            f"Expected exactly one unchanged heading {HEADING!r}; "
            f"matches={heading_indices!r}; close={close!r}"
        )
    heading_index = heading_indices[0]

    section_end = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if HEADING_RE.match(lines[index]):
            section_end = index
            break

    old_entries = []
    invalid_lines = []
    for index in range(heading_index + 1, section_end):
        raw = lines[index]
        if not raw.strip():
            continue
        match = BULLET_RE.match(raw)
        if not match:
            invalid_lines.append({"line": index + 1, "text": raw})
            continue
        groups = match.groupdict()
        old_entries.append(
            {
                "path": groups["path"],
                "line": int(groups["line"]),
                "text": groups["text"],
                "raw": raw,
            }
        )

    if invalid_lines:
        print("Invalid TODO bullets:")
        print(json.dumps(invalid_lines, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    scanned = defaultdict(list)
    scanned_files = 0
    scanned_todo_lines = 0
    for path in sorted(ROOT.rglob("*.py")):
        rel_path = path.relative_to(ROOT).as_posix()
        if rel_path in TEMP_PATHS or ".git" in path.relative_to(ROOT).parts:
            continue
        if not path.is_file():
            continue
        scanned_files += 1
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(source.splitlines(), start=1):
            if "TODO" not in line:
                continue
            todo_text = extract_todo(line)
            if todo_text is None:
                continue
            scanned_todo_lines += 1
            scanned[rel_path].append(
                {"path": rel_path, "line": line_number, "text": todo_text}
            )

    changed_paths = changed_python_files()
    changed_set = set(changed_paths)

    final_entries = [dict(entry) for entry in old_entries if entry["path"] not in changed_set]
    log = {
        "old_entry_count": len(old_entries),
        "scanned_python_files": scanned_files,
        "scanned_todo_lines": scanned_todo_lines,
        "changed_python_files": changed_paths,
        "matched": [],
        "added": [],
        "removed": [],
        "changed_path_details": {},
    }

    for path in changed_paths:
        old_for_path = [dict(entry) for entry in old_entries if entry["path"] == path]
        current_for_path = scanned.get(path, [])
        available_old = list(old_for_path)
        final_for_path = []

        for current in current_for_path:
            match_index = None
            current_canon = canon(current["text"])
            for candidate_index, old_candidate in enumerate(available_old):
                if canon(old_candidate["text"]) == current_canon:
                    match_index = candidate_index
                    break

            if match_index is None:
                item = {
                    "path": path,
                    "line": current["line"],
                    "text": current["text"],
                }
                final_for_path.append(item)
                log["added"].append(dict(item))
            else:
                old_item = available_old.pop(match_index)
                item = {
                    "path": path,
                    "line": current["line"],
                    "text": old_item["text"],
                }
                final_for_path.append(item)
                log["matched"].append(
                    {
                        "path": path,
                        "old_line": old_item["line"],
                        "new_line": current["line"],
                        "text": old_item["text"],
                    }
                )

        for leftover in available_old:
            log["removed"].append(
                {
                    "path": path,
                    "old_line": leftover["line"],
                    "text": leftover["text"],
                }
            )

        final_entries.extend(final_for_path)
        log["changed_path_details"][path] = {
            "old_count": len(old_for_path),
            "scanned_count": len(current_for_path),
            "final_count": len(final_for_path),
            "old": old_for_path,
            "scanned": current_for_path,
            "final": final_for_path,
        }

    final_entries.sort(key=lambda entry: (entry["path"], entry["line"]))
    bullets = [
        f"- [ ] **{entry['path']}:{entry['line']}** - {entry['text']}"
        for entry in final_entries
    ]

    rebuilt_lines = (
        lines[: heading_index + 1]
        + [""]
        + bullets
        + [""]
        + lines[section_end:]
    )
    new_text = "\n".join(rebuilt_lines)
    if original_ends_with_newline:
        new_text += "\n"

    readme_changed = new_text != original_text
    if readme_changed:
        with readme_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(new_text)

    log["new_entry_count"] = len(final_entries)
    log["net_entry_delta"] = len(final_entries) - len(old_entries)
    log["readme_changed"] = readme_changed
    log["sorted_correctly"] = [
        (entry["path"], entry["line"]) for entry in final_entries
    ] == sorted((entry["path"], entry["line"]) for entry in final_entries)
    log["changed_block_entries"] = {
        path: [
            {"line": entry["line"], "text": entry["text"]}
            for entry in final_entries
            if entry["path"] == path
        ]
        for path in changed_paths
    }

    print(json.dumps(log, ensure_ascii=False, indent=2))
    print("README_CHANGED:", readme_changed)
    print("NEW_ENTRY_COUNT:", len(final_entries))


if __name__ == "__main__":
    main()
