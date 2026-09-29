# Scorecard versions

Every evaluation records the scorecard version it was scored on
(`scorecard_version` in DynamoDB and in `evaluation-result.json`). The UI shows
it on every evaluation, and the Excel/PDF is always written into that version's
template, so a result never changes meaning when the client updates a sheet.

| Version | Label  | Client scorecards | Templates |
|---------|--------|-------------------|-----------|
| 0       | Legacy | V2.0 Simple       | `Audio Assessment and Scoring/` |
| 1       | v1     | V2.1 Simple       | `templates_version_1.0/` |

Evaluations stored before versioning have no `scorecard_version` and are Legacy.

## Files

- `registry.json` — the versions, which one is `current`, and where each
  version's Excel templates live (repo path and S3 key).
- `v<N>/rules.json` — hand-written scoring rules taken from that version's
  checklist documents: per-criterion guidance, "stated absence counts as
  covered", required-phrase checks, recording durations, red-flag codes.
- `v<N>/rubric.json` — **generated.** The criteria, points, MUST flags and
  sections are read from the Excel templates and merged with `rules.json`.
  Never edit it by hand.
- `build_rubric.py` — builds and validates `rubric.json` (points add up, every
  rule points at a real criterion). `--check` fails if any version is stale.
- `__init__.py` — the loader both Lambdas import (bundled into their zips).

## Releasing a new scorecard version

1. Put the client's new Excel templates (and checklist documents) in the repo.
2. Add the version to `registry.json`: label, client version, template
   `source` paths and new `s3_key`s (e.g. `scorecards/v2/...`).
3. Copy the previous `v<N>/rules.json` to `v<N+1>/rules.json` and update it
   from the new checklist documents.
4. `python scorecards/build_rubric.py <N+1>` — read its output: item counts,
   section points and MUST lists must match the new documents.
5. Set `"current"` in `registry.json` to the new version.
6. `python tests/test_safety_nets.py` (checks MUST flags against each
   template's own formula), then score a few real recordings with
   `python tests/score_local.py <evaluation_id> ...` and open the Excels.
7. Upload the templates to their S3 keys, rebuild and deploy the evaluation
   and excel-generator Lambdas (`create_evaluation_package.py`,
   `create_excel_package.py`). New uploads are scored on the new version; the
   UI needs no change.

To roll back without a redeploy, set `SCORECARD_VERSION` on the evaluation
Lambda to the previous version.
