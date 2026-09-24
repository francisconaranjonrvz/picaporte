"""Los workflows no interpolan inputs de workflow_dispatch dentro de los scripts."""

from pathlib import Path

import pytest

from django.conf import settings

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(settings.BASE_DIR) / ".github" / "workflows"


@pytest.mark.parametrize("name", ["enrich.yml", "discover.yml", "personalize.yml"])
def test_los_inputs_no_se_interpolan_en_run(name):
    """`${{ inputs.x }}` en `run:` permite inyectar órdenes; deben ir por `env:`."""
    workflow = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            assert "inputs." not in step.get("run", ""), step.get("name")
