# Repo Restructure — Implementation Plan (Phase 1.5)

**Goal:** Turn the repo root from a 25-folder tutorial listing into a production layout, without losing the learning archive.

**Architecture:** Pure `git mv` — no file *contents* change except path references. All 25 stages move as siblings into `stages/`, so stage-to-stage relative references stay valid. The React frontend is **copied** (not moved) to `/frontend`, keeping `stages/stage25_react_ui/` self-contained per CLAUDE.md.

**Prerequisite:** Phase 1 Task 8 must be complete and green. This move breaks two hardcoded paths that Phase 1's tests depend on; doing it mid-port means debugging path errors and port errors at once.

## Target layout

```
langgraph/
├── app/                  # production package (Phase 1 output)
├── frontend/             # React app, copied from stage25_react_ui/
├── stages/               # all 25, archived
│   ├── stage1_chatbot/ … stage25_react_ui/
├── tests/
├── docs/
│   ├── PROGRESS.md
│   ├── flow.md
│   ├── specs/            # from .claude/spec/
│   ├── plans/            # from .claude/plans/
│   └── superpowers/
├── docker-compose.yml
├── requirements.txt
├── README.md
└── CLAUDE.md
```

## Global Constraints

- **Stage folder *contents* are not edited.** They move; their internal text is untouched. Stage-to-stage references remain correct because they move together.
- `git mv` for every move so history follows. Never delete-and-recreate.
- After every task, `pytest tests/` must still pass. That is the regression gate for the whole phase.

---

## Task 1: Move the stages

- [ ] **Step 1: Confirm green baseline**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass. Do not proceed otherwise.

- [ ] **Step 2: Copy the frontend out before moving**

The frontend is copied, not moved, so the archived stage stays runnable.

```bash
mkdir -p frontend
cp -r stage25_react_ui/src stage25_react_ui/public stage25_react_ui/index.html \
      stage25_react_ui/package.json stage25_react_ui/package-lock.json \
      stage25_react_ui/vite.config.ts stage25_react_ui/tsconfig*.json \
      stage25_react_ui/.env.example stage25_react_ui/.gitignore frontend/
```

Do NOT copy `stage25_react_ui/backend/` — that role belongs to `app/`.

- [ ] **Step 3: Move all stages**

```bash
mkdir -p stages
for d in stage*_*/; do git mv "$d" "stages/${d}"; done
```

- [ ] **Step 4: Fix the two code paths that break**

`tests/test_agents.py` and `tests/test_schema_parity.py` both hardcode `stage25_react_ui/backend/main.py` relative to CWD. Anchor them to the repo root instead — this fixes the move *and* the pre-existing fragility of depending on the working directory:

```python
REPO_ROOT = Path(__file__).resolve().parent.parent
ORIGINAL_PATH = REPO_ROOT / "stages" / "stage25_react_ui" / "backend" / "main.py"
```

- [ ] **Step 5: Verify and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: same pass count as Step 1.

```bash
git add -A && git commit -m "Move the 25 learning stages into stages/ and promote frontend/"
```

---

## Task 2: Consolidate docs

- [ ] **Step 1: Move the loose root markdown and .claude docs**

```bash
git mv PROGRESS.md docs/PROGRESS.md
git mv flow.md docs/flow.md          # currently untracked - `git add` first
git mv .claude/spec docs/specs
git mv .claude/plans docs/plans
```

`file.md` is a raw terminal transcript from the project's first session, not documentation — move it to `docs/archive/session-transcript.md` rather than leaving it at root.

`CLAUDE.md` and `README.md` stay at root — both are convention-mandated there.

- [ ] **Step 2: Verify and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`

```bash
git add -A && git commit -m "Consolidate documentation under docs/"
```

---

## Task 3: Update path references in root docs

**Scope:** `README.md` (64 refs), `docs/PROGRESS.md` (58), `CLAUDE.md` (12), `docs/flow.md` (5). Stage READMEs are archive — leave them.

- [ ] **Step 1: Rewrite stage paths**

Every `stageN_name/` path reference in those four files becomes `stages/stageN_name/`. Prose references to a stage *by name* ("Stage 13 added the supervisor") stay as-is — only paths change.

- [ ] **Step 2: Fix the OS-specific setup commands**

`README.md` and `CLAUDE.md` document Windows-only activation (`.venv\Scripts\activate`). A production repo must run on Linux and macOS — that is where it deploys. Replace with both:

````markdown
```bash
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```
````

Apply the same to any `.venv/Scripts/python.exe` invocation in root docs.

- [ ] **Step 3: Verify no stale paths remain**

```bash
grep -rn "^\|[^s]stage[0-9]" README.md CLAUDE.md docs/*.md | grep -v "stages/" | grep -vi "stage [0-9]"
```
Expected: no path-shaped matches.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "Update documentation paths for the stages/ layout"
```

---

## Task 4: Rewrite the README for the new shape

The README currently opens as a learning-project roadmap. It should open as a
product README, with the learning archive as a section — not the other way round.

- [ ] **Step 1: Restructure**

Order: what the app is → architecture diagram → quickstart (docker compose, backend, frontend) → API surface → the 25-stage learning archive (kept, summarized, linked to `stages/`) → deployment links.

Preserve the existing live-deployment URLs and the stage table; do not invent capabilities the app does not have. The "what's not built" list from `docs/flow.md` (no auth, no streaming, in-process rate limiting) belongs in the README too — accuracy over polish.

- [ ] **Step 2: Verify and commit**

Confirm every link resolves: `grep -o "](\./[^)]*)" README.md` then check each path exists.

```bash
git add README.md && git commit -m "Rewrite README around the production layout"
```

---

## Definition of Done

- [ ] `pytest tests/` passes with the same count as before the move.
- [ ] Repo root contains only: `app/ frontend/ stages/ tests/ docs/` + `README.md CLAUDE.md requirements.txt docker-compose.yml .env.example .gitignore`.
- [ ] `git log --follow stages/stage1_chatbot/main.py` shows history across the move.
- [ ] No root doc references a `stageN_*` path outside `stages/`.
- [ ] Setup instructions work on Linux/macOS as well as Windows.
- [ ] `stages/stage25_react_ui/` is still self-contained (its own frontend copy intact).
