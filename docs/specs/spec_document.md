# Personal Research Assistant — Project Specification

## 1. Project Purpose

Build a Personal Research Assistant as a progressive learning project using **Python, LangChain, and LangGraph**.

The project starts as a simple chatbot and gradually grows into a capable research assistant and eventually a multi-agent system.

The primary goal is **learning agentic AI concepts step by step**, not building a production system immediately.

---

## 2. Core Principles

1. **One stage at a time.**
2. **One major concept at a time.**
3. Keep implementations beginner-friendly.
4. Each stage must be independently runnable.
5. Previous stages must remain unchanged unless explicitly approved.
6. Avoid unnecessary abstractions and over-engineering.
7. Do not implement future functionality early.
8. Every stage must have a short README explaining what was learned.
9. Every stage should be tested before moving forward.
10. Claude Code must inspect the existing project before making changes.
11. Claude Code must explain its proposed changes before implementing them.
12. Claude Code must wait for approval before implementation when requested.
13. The final goal is multi-agent, but multi-agent functionality should only be introduced when its concepts have been learned.

---

# 3. Technology

### Required

* Python
* LangChain
* LangGraph
* LLM provider supported by the project

### Additional libraries

Only add a dependency when a stage actually requires it.

Examples:

* Web search library
* `requests`
* `beautifulsoup4`
* `pypdf`
* Embedding/vector-store libraries

Do not add libraries simply for future use.

---

# 4. Project Structure

Each stage should be separate.

Example:

```text
personal-research-assistant/

├── stages/stage1_chatbot/
├── stages/stage2_tool_agent/
├── stages/stage3_rag/
├── stages/stage4_web_fetch/
├── stages/stage5_pdf_fetch/
├── stages/stage6_planner/
├── stages/stage7_human_loop/
│
├── CLAUDE.md
├── PROJECT_SPEC.md
├── PROGRESS.md
├── README.md
└── requirements.txt
```

The exact folder names may evolve, but previous completed stages should remain available for comparison.

---

# 5. Stage Roadmap

## Stage 1 — Stateful Chatbot

### Goal

Learn basic LangGraph state.

### Features

* Chat with an LLM
* Store conversation messages
* Basic LangGraph graph

### Concept

```text
StateGraph
State
Node
START
END
```

### Status

Completed.

---

# Stage 2 — Tool Calling

### Goal

Learn how an LLM can decide to call a tool.

### Tool

```text
DuckDuckGoSearchRun
```

### Concept

```text
User
 ↓
LLM
 ↓
Tool decision
 ↓
Tool
 ↓
LLM
 ↓
Answer
```

### Status

Completed.

---

# Stage 3 — Knowledge Base Search

### Goal

Learn basic RAG.

### Tool

```text
search_knowledge_base
```

### Basic flow

```text
Documents
 ↓
Chunks
 ↓
Embeddings
 ↓
Vector store
 ↓
Similarity search
 ↓
Tool
```

### Concept

Basic retrieval-augmented generation.

### Status

Completed.

---

# Stage 4 — Webpage Fetch

### Goal

Learn how an agent can retrieve the contents of a webpage.

### Tool

```text
fetch_webpage
```

### Flow

```text
URL
 ↓
HTTP request
 ↓
HTML
 ↓
Text extraction
 ↓
Agent
```

### Status

Completed.

---

# Stage 5 — PDF Fetch

### Goal

Learn how to retrieve text from PDF documents.

### Tool

```text
fetch_pdf
```

### Flow

```text
PDF URL
 ↓
Download
 ↓
PDF parser
 ↓
Text
 ↓
Agent
```

### Concept

Binary document retrieval and text extraction.

### Status

Completed.

---

# Stage 6 — Planning and Looping

### Goal

Learn how LangGraph can break a task into subtasks and process them one by one.

### Flow

```text
START
 ↓
Plan
 ↓
Research subtask
 ↓
More subtasks?
 ├── Yes → Research subtask
 └── No  → Synthesize
 ↓
END
```

### Concepts

* Graph state
* Conditional edges
* Loops
* Task tracking
* Basic planning

### Status

Completed.

---

# Stage 7 — Human-in-the-Loop

### Goal

Learn how a LangGraph workflow can pause and wait for human input.

### Flow

```text
User question
 ↓
Planner
 ↓
Show plan
 ↓
Human approval
 ├── Approve → Continue
 └── Reject  → Stop
```

### Concept

LangGraph interrupt/resume.

### Status

Next.

---

# 6. Future Stages

These stages should NOT be implemented until the earlier concepts are understood.

## Stage 8 — Better Research Workflow

Improve the existing research flow using the tools already created.

Possible capabilities:

* Search web
* Fetch webpage
* Fetch PDF
* Search knowledge base

No new complex architecture unless necessary.

---

## Stage 9 — Memory

Introduce a simple distinction between:

### Short-term state

Information needed during the current task.

### Long-term memory

Information that should remain available for future conversations.

Keep the first implementation simple.

---

## Stage 10 — Better Research Agent

Combine the previously learned capabilities into a more useful research workflow.

The agent should be able to decide which existing capability it needs.

Example:

```text
Question
 ↓
Agent
 ├── web search
 ├── fetch webpage
 ├── fetch PDF
 └── knowledge search
```

Do not add multi-agent functionality yet.

---

## Stage 11 — Specialist Agents

Introduce the first multiple-agent architecture.

Possible agents:

```text
Research Agent
Knowledge Agent
Analysis Agent
```

Each agent should have a clear responsibility.

Keep the number of agents small.

---

## Stage 12 — Supervisor

Introduce a supervisor that decides which specialist agent should handle the next part of a task.

```text
             Supervisor
             /   |   \
            /    |    \
     Research  Knowledge  Analysis
       Agent     Agent      Agent
```

---

## Stage 13 — Critic

Add a simple reviewer/critic.

```text
Research
   ↓
Critic
   ↓
Pass / Retry
```

The critic should check whether the result is adequate before the final response.

---

## Stage 14 — Final Multi-Agent Research Assistant

Combine the concepts learned throughout the project:

```text
                         User
                          ↓
                     Supervisor
                          ↓
             ┌────────────┼────────────┐
             ↓            ↓            ↓
        Research       Knowledge     Analysis
          Agent          Agent         Agent
             \            |            /
              \           |           /
                       Critic
                          ↓
                     Final Answer
```

The system should use the tools and concepts learned in previous stages.

---

# 7. Tool Catalog

These are possible tools, not requirements to implement immediately.

## Current Tools

```text
DuckDuckGoSearchRun
search_knowledge_base
fetch_webpage
fetch_pdf
```

## Possible Future Tools

### Research

```text
web_search
fetch_webpage
fetch_pdf
```

### Knowledge

```text
search_knowledge_base
```

### Data

```text
calculator
python_executor
```

### Database

```text
get_database_schema
execute_sql
```

### Output

```text
create_report
save_report
```

Only add a tool when there is a clear learning or application reason.

---

# 8. Agent Architecture Rules

Do not call something an agent simply because it uses an LLM.

A meaningful agent should have some ability to:

* decide what action to take
* use available capabilities
* observe results
* continue or stop based on results

Early stages may intentionally be simpler.

The project should gradually move from:

```text
LLM
 ↓
Tool
```

to:

```text
LLM
 ↓
Tool
 ↓
Observation
 ↓
Decision
 ↓
Tool
```

and eventually:

```text
Supervisor
 ↓
Specialist Agent
 ↓
Tools
 ↓
Results
 ↓
Critic
 ↓
Final Answer
```

---

# 9. Claude Code Rules

Claude Code must follow these rules when working on this repository.

### Before coding

1. Inspect the repository.
2. Read `PROJECT_SPEC.md`.
3. Read `CLAUDE.md`.
4. Check the current completed stage.
5. Explain the proposed implementation.

### During coding

1. Implement only the requested stage.
2. Do not modify previous stages unnecessarily.
3. Do not implement future features.
4. Keep code simple.
5. Avoid unnecessary abstractions.
6. Use existing dependencies when possible.

### After coding

1. Run the relevant test.
2. Confirm the stage works.
3. Explain what changed.
4. Explain the new concept.
5. Update `PROGRESS.md`.
6. Update the stage README.

---

# 10. Definition of Done

A stage is complete only when:

* [ ] The stage runs successfully.
* [ ] The intended concept is implemented.
* [ ] The implementation is understandable.
* [ ] A basic test has passed.
* [ ] Previous stages still work.
* [ ] The stage README exists.
* [ ] `PROGRESS.md` is updated.
* [ ] No unnecessary future functionality was added.

---

# 11. Current Project Status

```text
Stage 1  Stateful Chatbot          ✅
Stage 2  Tool Calling              ✅
Stage 3  RAG                       ✅
Stage 4  Webpage Fetch             ✅
Stage 5  PDF Fetch                 ✅
Stage 6  Planning + Looping        ✅
Stage 7  Human-in-the-Loop         → Next
Stage 8  Research Workflow         ⏳
Stage 9  Memory                    ⏳
Stage 10 Research Agent            ⏳
Stage 11 Specialist Agents        ⏳
Stage 12 Supervisor                ⏳
Stage 13 Critic                    ⏳
Stage 14 Multi-Agent System        ⏳
```

---

# 12. Most Important Rule

**Do not optimize for reaching the final multi-agent architecture quickly.**

Optimize for understanding each concept.

The project should grow like this:

```text
Understand
   ↓
Build
   ↓
Test
   ↓
Explain
   ↓
Move to next concept
```

The final multi-agent research assistant is the destination.

The stages are the learning path.
