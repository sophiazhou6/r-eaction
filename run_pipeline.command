#!/bin/bash
# Double-click or run in Terminal. Runs Sophia's pipeline on the latest event in Supabase.
#   ./run_pipeline.command             # run + save outputs locally
#   ./run_pipeline.command --cleanup   # also save outputs to Supabase narrative_runs, then delete the raw comments
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "creating .venv (first run only)..."
  python3 -m venv .venv || exit 1
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q supabase python-dotenv openai numpy pandas scikit-learn umap-learn langchain-core || exit 1
WORKSPACE="$(pwd)/workspace" .venv/bin/python narrative/run_real.py --llm "$@" 2>&1 | grep -v -i warning | tee workspace_run_output.txt
echo; echo "Done. Output saved to workspace_run_output.txt; files in workspace/data/"
