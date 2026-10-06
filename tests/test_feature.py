from pathlib import Path

from workspace.manager import (
    create_workspace,
    remove_workspace,
    workspace_has_changes,
)


def test_workspace_is_created_outside_main_repository() -> None:
    workspace = create_workspace("workspace-isolation-test")

    try:
        assert workspace.path.exists()
        assert workspace.path.is_dir()

        repo_root = Path.cwd().resolve()

        assert workspace.path != repo_root
        assert repo_root not in workspace.path.parents

        assert workspace.branch_name.startswith(
            "agent/workspace-isolation-test-"
        )

        assert workspace_has_changes(workspace) is False

    finally:
        remove_workspace(workspace, force=True)