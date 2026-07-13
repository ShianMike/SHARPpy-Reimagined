# Sounding Parameter Guide Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `docs/sounding_parameter_guide.md` so every implementation claim and formula matches the active SHARPpy Reimagined calculation/display code and all mathematics renders in GitHub Markdown.

**Architecture:** Treat `sharpmod/sharptab`, the installed SHARPpy 1.4.0a5 parcel/profile routines, `sharpmod/colors.py`, and the active board widgets as the sources of truth. Separate exact code formulas from explanatory meteorology, identify values delegated to upstream rather than pretending they are locally reimplemented, and use GitHub-supported inline math plus fenced `math` blocks. Add a lightweight documentation regression test for delimiter/fence integrity, then render the complete file through GitHub's Markdown API for an authoritative math-rendering smoke test.

**Tech Stack:** Markdown, GitHub MathJax rendering, Python 3.14, pytest, SHARPpy/NumPy source inspection.

---

### Task 1: Build the code-to-guide audit

**Files:**
- Modify: `docs/sounding_parameter_guide.md`
- Create: `docs/superpowers/plans/2026-07-12-sounding-parameter-guide-audit.md`

- [x] **Step 1: Inventory the guide headings and displayed parameters**

Compare the document's parameter list with `sharpmod/viz/index_board.py`, `sharpmod/viz/param_board.py`, and `sharpmod/sharptab/profile.py`. Record that the active GUI exposes six parcel keys, custom SFC-500 m kinematics, NCIN, HPI, and separate MMP probability/MCS logit quantities.

- [x] **Step 2: Verify formulas against calculation functions**

Check standard parcel/profile values against installed `sharppy.sharptab.params` and custom values against `sharpmod/sharptab/derived.py`, `params.py`, `winds.py`, `ecape.py`, and `viz/streamwiseness.py`. Correct the mid-level RH layer, DCAPE source/virtual-temperature behavior, 6CAPE parcel type, DCP units, MCS probability sign, ECAPE `psi`, LRGHAIL terms, and missing-value rules.

- [x] **Step 3: Verify colors against draw-time dispatch**

Use `sharpmod/colors.py` helper functions rather than the stale introspection tables for STP, SCP, SHIP, and LRGHAIL. Document neutral-zero behavior, positive-CAPE guards, negative-SCP cyan, and the exact white/yellow/red/pink cut points.

### Task 2: Rewrite and validate Markdown mathematics

**Files:**
- Modify: `docs/sounding_parameter_guide.md`
- Create: `sharpmod/tests/test_sounding_parameter_guide.py`

- [x] **Step 1: Rewrite the guide with source-backed language**

Replace the current file with a code-aligned guide containing a scope/source note, exact parcel definitions, formulas and implementation details for displayed thermodynamic/kinematic/composite parameters, color rules, missing-data behavior, and a source map. Mark project-defined or delegated formulas explicitly.

- [x] **Step 2: Use GitHub-supported math syntax**

Keep short expressions in `$...$`; place every display equation in a fenced `math` block. Avoid malformed nested fractions, unescaped Markdown-sensitive math, and unmatched braces/delimiters.

- [x] **Step 3: Add documentation integrity tests**

Test that the guide contains no legacy `$$` blocks, has paired `math` fences, has balanced inline-dollar delimiters outside code spans/fences, and contains the corrected critical formulas/layer statements.

- [x] **Step 4: Run focused tests**

Run: `python -m pytest sharpmod/tests/test_sounding_parameter_guide.py -q`

Expected: all documentation integrity tests pass.

- [x] **Step 5: Render through GitHub Markdown and inspect output**

Send the complete guide to GitHub's `/markdown` API in GFM mode, verify a successful response, confirm the expected number of rendered math blocks, and scan the HTML for unrendered formula delimiters or error markers.

- [x] **Step 6: Run final repository checks**

Run: `git diff --check -- docs/sounding_parameter_guide.md sharpmod/tests/test_sounding_parameter_guide.py` and re-run the guide test.

Expected: zero whitespace errors and all tests pass.
