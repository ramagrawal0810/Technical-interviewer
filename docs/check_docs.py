"""Check the maintained documentation: relative Markdown links resolve, repository paths named in backticks exist, code fences are balanced,
and every function named as `path/file.py: name` exists in that file. Read-only; no network.

Run from the repository root:   backend\\venv\\Scripts\\python.exe docs\\check_docs.py
"""

import glob
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOTS = ("backend/", "frontend/", "evaluation/", "docs/", "data/")
DOCS = sorted(glob.glob(os.path.join(ROOT, "docs", "*.md")) + [os.path.join(ROOT, "README.md"), os.path.join(ROOT, "docs", "archive", "README.md"),
                                                                os.path.join(ROOT, "learning", "PROJECT_DEEP_DIVE_AND_INTERVIEW_PREP.md")])


def exists(rel, base_dir=None):
    rel = rel.split("#")[0].rstrip("/")
    for start in ([base_dir] if base_dir else []) + [ROOT]:
        if os.path.exists(os.path.normpath(os.path.join(start, rel))):
            return True
    return False


def main():
    problems, checked = [], 0
    for path in DOCS:
        if not os.path.exists(path):
            problems.append(f"{os.path.relpath(path, ROOT)}: document is missing")
            continue
        text = open(path, encoding="utf-8").read()
        rel_doc, base = os.path.relpath(path, ROOT), os.path.dirname(path)
        if text.count("```") % 2:
            problems.append(f"{rel_doc}: unbalanced code fences")
        prose = re.sub(r"```.*?```", "", text, flags=re.S)
        for target in re.findall(r"\]\(([^)]+)\)", prose):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            checked += 1
            if not exists(target, base):
                problems.append(f"{rel_doc}: broken link ({target})")
        for span in re.findall(r"`([^`\n]+)`", prose):
            m = re.match(r"^((?:backend|frontend|evaluation|docs|data|services|api|db)/[\w./\\*{}\-]+?)(?::\s*([A-Za-z_][\w]*))?(?:[,)]|$)", span.strip())
            if not m:
                continue
            ref, func = m.group(1), m.group(2)
            if ref.startswith(("services/", "api/", "db/")):      # shorthand used in the docs for backend/app/<package>/...
                ref = "backend/app/" + ref
            if any(c in ref for c in "*{}"):
                continue                               # wildcard or placeholder paths are descriptive
            checked += 1
            if not exists(ref):
                problems.append(f"{rel_doc}: path does not exist (`{span}`)")
            elif func and ref.endswith(".py"):
                source = open(os.path.join(ROOT, ref), encoding="utf-8").read()
                if not re.search(rf"(def|class)\s+{re.escape(func)}\b|^{re.escape(func)}\s*[=:]", source, re.M):
                    problems.append(f"{rel_doc}: `{func}` not found in {ref}")
    print(f"documents checked: {len([p for p in DOCS if os.path.exists(p)])}, references checked: {checked}")
    for p in problems:
        print("PROBLEM:", p)
    print("RESULT:", "PASS" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
