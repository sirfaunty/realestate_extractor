#!/usr/bin/env bash
# Connect to the staging VM with GitHub key forwarding (so `git pull`
# works there). Usage from Git Bash:  ./vm.sh
# Optional: pass a command to run and exit, e.g.  ./vm.sh "docker compose ps"
VM_HOST="${CAPACTIVE_VM:-root@91.98.30.65}"
eval "$(ssh-agent -s)" >/dev/null
ssh-add ~/.ssh/id_ed25519 2>/dev/null || ssh-add ~/.ssh/id_rsa
if [ $# -gt 0 ]; then
  ssh -A "$VM_HOST" "cd ~/capactive && $*"
else
  echo "Prompt will read root@ubuntu… — you are ON THE VM. 'exit' returns here."
  ssh -A "$VM_HOST"
fi
