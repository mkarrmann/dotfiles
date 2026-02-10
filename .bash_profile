# bash_profile is for setting environment variables and anything else that
# should happen at login.  bash_profile is sourced only in login shells (e.g.,
# the shell started when you log in via SSH).  The main things that make sense
# to put in bash_profile are environment variable exports and startup programs.

# Source bashrc to pull in configuration for interactive shell use
if [[ -f ~/.bashrc ]]; then
  source ~/.bashrc
fi
