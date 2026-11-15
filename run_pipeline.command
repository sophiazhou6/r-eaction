#!/bin/bash
# Double-click or run in Terminal. Runs Sophia's pipeline on the latest event in Supabase.
#   ./run_pipeline.command             # OpenAI gpt-4.1-mini for claims + stance (~$0.50 per 2k comments)
#   ./run_pipeline.command --cheap     # gpt-4.1-nano (~4x cheaper)
#   ./run_pipeline.command --free      # no LLM: stored embeddings, no stance (free, ~30 s)
#   ./run_pipeline.command --local     # free local model via Ollama (slow: tens of minutes)
#   add --cleanup to also save outputs to Supabase narrative_runs and delete the raw comments
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "creating .venv (first run only)..."
  python3 -m venv .venv || exit 1
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q supabase python-dotenv openai numpy pandas scikit-learn umap-learn langchain-core || exit 1
LLM="--llm"; ARGS=()
for a in "$@"; do
  case "$a" in
    --free)  LLM="" ;;
    --cheap) export CLAIM_MODEL=gpt-4.1-nano ;;
    --local) export CLAIM_BASE_URL=http://localhost:11434/v1 CLAIM_API_KEY=ollama CLAIM_MODEL=${CLAIM_MODEL:-qwen2.5:7b} EMBED_BACKEND=supabase ;;
    *) ARGS+=("$a") ;;
  esac
done
WORKSPACE="$(pwd)/workspace" .venv/bin/python narrative/run_real.py $LLM "${ARGS[@]}" 2>&1 | grep -v -i warning | tee workspace_run_output.txt
echo; echo "Done. Output saved to workspace_run_output.txt; files in workspace/data/"
