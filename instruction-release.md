# Release instructions for AI agents

These instructions apply to every AI agent working in this repository. Read
them before making changes and follow them before completing a task.
Direct user instructions, including requests for read-only work, take precedence.

## Required for every change

1. Review the actual final diff. Record only work that was implemented; do not
   describe plans, unfinished work or unverified improvements as completed.
2. Update [CHANGELOG.md](CHANGELOG.md) in the current **Unreleased** section
   before the final response. This includes features, fixes, removals, refactors,
   tests and documentation changes. Use the existing Added, Changed, Fixed,
   Removed or Security headings as appropriate; combine related entries.
3. For every user-facing change, update the matching version in
   `frontend/release-notes.json` in the same change set. Keep the summary
   consistent with the changelog and the final implementation.
4. Review both files again after the implementation changes. Remove stale
   claims, merge duplicate highlights and preserve previous release history.
5. In the final response, say that the changelog and release highlights were
   updated, or explain why the highlights needed no change.

Read-only reviews and research must remain read-only. For documentation, tests
or internal maintenance with no user-visible effect, log the work in the
changelog and verify the existing highlights remain accurate; do not add
developer housekeeping to the in-app What's new screen.

## Writing the What's new summary

- Use the existing JSON shape: a version key such as `3.2.0`, a `summary` string
  and a `highlights` array of objects with `title` and `description` strings.
  Version keys have no leading `v`.
- Write one short introductory sentence and about three to six grouped
  highlights. Explain what the user can do or what now works better.
- Use plain language. Keep file paths, internal ticket numbers, implementation
  details and raw commit lists in the technical changelog.
- Describe important fixes and removals as well as new features. Preserve
  relevant availability limits and data-quality caveats.
- Never invent speed measurements, claim scientific correctness without
  validation, or present an unreleased feature as already installed.
- Keep notes for different versions separate. The app selects the installed
  version's notes; pending release notes must not replace those of that version.
- Keep `frontend/release-notes.json` as the shared source for the popup,
  Settings → Updates and the updater feed. Do not maintain separate copies of
  the same summary in components or a manually edited `latest.json`.

## Validation and release status

- Check that the JSON parses, the relevant version entry exists and every
  summary, title and description is nonempty.
- When changing note data or update behavior, run the relevant existing checks:
  the frontend release-highlights/update tests and
  `sharpmod/tests/test_updater_packaging.py`. Follow
  [CONTRIBUTING.md](CONTRIBUTING.md) and the
  [release workflow](.github/workflows/release.yml) for actual release gates.
- For UI changes, verify the real app and provide current screenshots for review,
  including 1920 × 1080 captures when screenshots are requested.
- Do not change a released changelog section, mark an Unreleased section as
  released, bump application versions, create release tags, dispatch a release
  workflow or publish assets without explicit user authorization.
- **Release authority:** the user has explicitly authorized v3.2.0 publication.
  Complete the required validation and verify the published release before
  reporting success. Authorization for this version does not authorize future
  releases; follow the user's latest instructions for each one.
