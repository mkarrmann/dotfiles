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
export -f gworktree

function fix-code() {
  export VSCODE_IPC_HOOK_CLI=$(ls -lt /run/user/$UID/vscode-ipc-*.sock 2> /dev/null | awk '{print $NF}' | head -n 1)
}
export -f fix-code


# Clean up the sockets that VS Code leaves all over the place like rabbit droppings. We
# use socat to test wether anyone is listening on the other side without reading or
# writing any bytes. If it’s a dead end, we assume the socket is stale and remove it.
function vsclean() {
  for i in /run/user/$UID/vscode-*.sock ; do
      if ! socat -u OPEN:/dev/null "UNIX-CONNECT:${i}" ; then
          rm --force --verbose "${i}"
      fi
  done
}
export -f vsclean
