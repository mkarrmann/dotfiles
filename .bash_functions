function gworktree() {

  # Check if a branch name is provided

  if [ -z "\$1" ]; then

    echo "Usage: create_worktree <branch-name>"

    return 1

  fi

 

  # Store the branch name

  local branch_name=$1

 

  # Find the root of the git repository

  local repo_root=$(git rev-parse --show-toplevel)

  if [ $? -ne 0 ]; then

    echo "Error: Must be run inside a git repository."

    return 1

  fi

 

  # Get the name of the repository from the root path

  local repo_name=$(basename "$repo_root")

 

  # Define the worktree path

  local worktree_path="${repo_root%/*}/worktrees/${repo_name}+${branch_name}"

 

  # Create the worktree

  git worktree add "$worktree_path" "$branch_name"

  if [ $? -eq 0 ]; then

    echo "Worktree for branch '$branch_name' created at '$worktree_path'"

  else

    echo "Failed to create worktree for branch '$branch_name'"

    return 1

  fi

}
