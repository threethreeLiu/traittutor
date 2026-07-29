# TraitTutor Flask App Source Project

Source path: `/Users/lrm/Documents/code/TraitTutor_flask_app`

## Role In TraitTutor

TraitTutor_flask_app is the source for the existing TraitTutor experiment implementation. For this MVP, it contributes Big Five assessment logic and personalized courseware generation concepts. It does not contribute the posttest or full experiment flow.

## Observed App Shape

- Formal app version: `simple_version/`
- Flask entry point: `simple_version/app.py`
- Route handlers: `simple_version/routes.py`
- App constants and question definitions: `simple_version/constants.py`
- Scoring logic: `simple_version/scoring.py`
- Courseware generation/cache/jobs: `simple_version/courseware.py`
- Research pipeline: `src/research/pipeline.py`
- Runtime CSVs: `simple_version/*.csv`
- Materials: root `md/` and `simple_version/data/`

## MVP Reuse

- BFI-10/TIPI question definitions from `simple_version/constants.py`.
- O/C/E/A/N score calculation and reverse scoring from `simple_version/scoring.py`.
- Bounded interpretation: personality is a personalization cue, not diagnosis or learning-style classification.
- Courseware generation ideas from `simple_version/courseware.py` and `src/research/pipeline.py`.

## Explicitly Excluded From MVP

- Knowledge pretest.
- Experimental grouping and bucket assignment.
- PPS perceived personalization questionnaire.
- RIMMS motivation questionnaire.
- PSRLS/SRL questionnaire.
- Posttest and completion flow.
- Paper-analysis/statistical workflows.

## Migration Notes

- Do not retain Flask pages as a runtime dependency.
- Do not use CSV files as the primary product store.
- Convert the relevant logic into TraitTutor package services, FastAPI routers, and capability/generation APIs.
- Generated outputs should integrate with Notebook, Question Bank, and Chat rather than the old experiment completion pages.
