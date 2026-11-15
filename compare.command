#!/bin/bash
# Compares clustering on LLM claims (A) vs Kent's stored raw-text embeddings (B), same comments.
# Reuses the claims from your last ./run_pipeline.command run; installs sentence-transformers (free, local) once.
cd "$(dirname "$0")"
.venv/bin/python -m pip install -q sentence-transformers || exit 1
WORKSPACE="$(pwd)/workspace" .venv/bin/python narrative/compare.py "$@" 2>&1 | grep -v -i warning | tee compare_output.txt
