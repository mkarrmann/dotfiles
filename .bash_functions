#!/bin/bash

function gworktree() {
  # Check if a branch name is provided
  if [ -z $1 ]; then
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



# KUBERNETES

# change namespace

kcn() {

  if [ -z $1 ]

  then

    echo "you must provide a namespace to set"

  else

    kubectl config set-context --current --namespace=$1

    echo "export set TILLER_NAMESPACE=$1"

    export set TILLER_NAMESPACE=$1

  fi

}

export -f kcn

 

# change cluster

kcc() {

  if [ -z $1 ]

  then

    kubectl config get-contexts

  else

    kubectl config use-context $1

  fi

}

export -f kcc

 

 

# read and b64 decode secret

kgs () {

  kubectl get secret $1 -o json | jq '.data | map_values(@base64d)'

}

export -f kgs

 

# b64 encode with a width of 0 (necessary for keytabs)

kb64() {

  echo -n "$1" | base64 -w 0

}

export -f kb64

function fix-code() {
  export VSCODE_IPC_HOOK_CLI=$(ls -lt /run/user/$UID/vscode-ipc-*.sock 2> /dev/null | awk '{print $NF}' | head -n 1)

}
export -f fix-code

function kgrep () {
  if [ $# -eq 0 ]; then
    echo "Usage: kgrep <pattern> [<object>]"
    return 1
  elif [ $# -eq 1 ]; then
    kubectl get pods | grep "$1"
  else
    kubectl get "$1" | grep "$2"
  fi
}
export -f kgrep

function kgs () {
  kubectl get secret $1 -o json | jq '.data | map_values(@base64d)'
}
export -f kgs

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
