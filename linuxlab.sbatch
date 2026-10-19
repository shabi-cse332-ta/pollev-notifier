#!/bin/bash
#SBATCH --partition=linuxlab
#SBATCH --account=engr-class-any
#SBATCH --job-name=pollev
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=02:00:00
#SBATCH --output=slurm-%j.log

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?Submit this job from the repository directory}"
if [[ -f .env ]]; then
    source .env
fi
exec .venv/bin/python -u pollev.py --interval 30
