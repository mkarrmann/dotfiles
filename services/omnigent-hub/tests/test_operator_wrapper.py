from __future__ import annotations

import os
import subprocess
from pathlib import Path

WRAPPER = Path(__file__).parents[3] / "bin/omnigent-hub"


def executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_operator_wrapper_mints_process_only_credential(tmp_path: Path) -> None:
    dotfiles = tmp_path / "dotfiles"
    binary = dotfiles / "services/omnigent-hub/.venv/bin/omnigent-hub"
    executable(binary, "#!/bin/sh\nprintf '%s' \"$OMNIGENT_HA_DELEGATED_CAT\"\n")
    topology = dotfiles / "omnigent_config/topology.env"
    topology.parent.mkdir(parents=True)
    topology.write_text("OMNIGENT_OWNER_FBID=12345\n", encoding="utf-8")
    tools = tmp_path / "bin"
    clicat = tools / "clicat"
    executable(
        clicat,
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *'--signer_id 12345'*) printf 'process-secret\\n' ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
    )
    env = os.environ.copy()
    env.pop("OMNIGENT_HA_DELEGATED_CAT", None)
    env.update({"DOTFILES_DIR": str(dotfiles), "PATH": f"{tools}:{env['PATH']}"})

    result = subprocess.run(
        [str(WRAPPER), "status"], env=env, text=True, capture_output=True, check=False
    )

    assert result.returncode == 0
    assert result.stdout == "process-secret"
    assert "process-secret" not in result.stderr
