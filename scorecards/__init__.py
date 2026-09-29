"""
Scorecard registry — the one place that says which scorecard version is
current and what each version scores.

Both the evaluation and the Excel generator Lambdas import this package (it is
bundled into their deployment zips), so the criteria the model is asked about
and the template the result is written into always come from the same version.
Every evaluation records the version it was scored on; results written before
versioning existed carry none and are treated as LEGACY_VERSION.
"""
import json
import os
from functools import lru_cache

_HERE = os.path.dirname(os.path.abspath(__file__))
LEGACY_VERSION = '0'


@lru_cache(maxsize=1)
def registry() -> dict:
    with open(os.path.join(_HERE, 'registry.json'), encoding='utf-8') as f:
        return json.load(f)


def current_version() -> str:
    # SCORECARD_VERSION lets an environment pin a version (e.g. to roll back
    # without a redeploy); otherwise the registry decides.
    v = os.environ.get('SCORECARD_VERSION') or registry()['current']
    if v not in registry()['versions']:
        raise ValueError(f'Unknown scorecard version {v!r}')
    return v


def resolve_version(stored) -> str:
    """The version an existing result was scored on (legacy when unrecorded)."""
    v = str(stored).strip() if stored not in (None, '') else LEGACY_VERSION
    return v if v in registry()['versions'] else LEGACY_VERSION


@lru_cache(maxsize=None)
def load(version: str) -> dict:
    with open(os.path.join(_HERE, f'v{version}', 'rubric.json'), encoding='utf-8') as f:
        return json.load(f)


def config(call_type: str, version: str) -> dict:
    types = load(version)['call_types']
    return types.get(call_type) or types['BCM_PHYSICAL_PD']


def label(version: str) -> str:
    return registry()['versions'].get(version, {}).get('label', f'v{version}')
