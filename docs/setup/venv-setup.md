# Python environment

This repository only needs a virtual environment when running the optional
ArXiv retrieval or literature tooling. The skill gate and deployment scripts
use the system `python3` plus PyYAML.

## Minimal setup

```bash
cd /path/to/scholar-agent
python3 -m venv venv
source venv/bin/activate
python -m pip install pyyaml -r requirements.txt
```

Run the dependency-light retrieval smoke test:

```bash
python3 scripts/tests/test_retrieval.py
```

The default test does not download models. To exercise model-backed retrieval,
install the optional model dependencies and run:

```bash
python3 scripts/tests/test_retrieval.py --actual
```

The test skips model-backed cases when `sentence-transformers` is unavailable.

## Verification

Run the repository checks after changing skills or scripts:

```bash
python3 scripts/tests/test-skill-consistency.py
bash scripts/tests/test-skill-gate.sh
bash scripts/deploy/lint-skills.sh
```

`requirements.txt` contains the core dependencies (arxiv, pyyaml, requests,
numpy). The retrieval / reranking / embedding stack (torch, transformers,
sentence-transformers, rank-bm25, scipy, accelerate) lives separately in
`requirements-retrieval.txt` — install it only when using the retrieval paths.
Model downloads are optional and can be large; do not install them for ordinary
skill or deployment changes.
