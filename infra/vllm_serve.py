"""
Deploy Nemotron 3 Nano 4B on Modal with vLLM (OpenAI-compatible API + tool calling).

One-time setup:
    modal secret create huggingface HF_TOKEN=hf_xxxx
    modal deploy infra/vllm_serve.py

After deploy, copy the printed URL into .env as VITAL_LLM_BASE_URL (append /v1).
"""

import subprocess

import modal

MODEL_NAME = "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"
SERVED_MODEL_NAME = "nemotron3-nano-4B-BF16"
VLLM_PORT = 8000
MINUTES = 60

# 8192 matches Vitál PRD context budget; keeps VRAM lower than full 262k.
MAX_MODEL_LEN = 8192

PARSER_URL = (
    "https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16/"
    "resolve/main/nano_v3_reasoning_parser.py"
)

vllm_image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .apt_install("wget")
    .uv_pip_install("vllm>=0.15.1")
    .run_commands(f"wget -O /root/nano_v3_reasoning_parser.py {PARSER_URL}")
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)

hf_cache_vol = modal.Volume.from_name("vital-hf-cache", create_if_missing=True)
vllm_cache_vol = modal.Volume.from_name("vital-vllm-cache", create_if_missing=True)

app = modal.App("vital-nemotron")


@app.function(
    image=vllm_image,
    gpu="A10G",
    secrets=[modal.Secret.from_name("huggingface")],
    scaledown_window=15 * MINUTES,
    timeout=20 * MINUTES,
    volumes={
        "/root/.cache/huggingface": hf_cache_vol,
        "/root/.cache/vllm": vllm_cache_vol,
    },
)
@modal.concurrent(max_inputs=32)
@modal.web_server(port=VLLM_PORT, startup_timeout=20 * MINUTES)
def serve() -> None:
    """Start the vLLM OpenAI-compatible server for Nemotron tool calling."""
    cmd = [
        "vllm",
        "serve",
        MODEL_NAME,
        "--served-model-name",
        SERVED_MODEL_NAME,
        "--host",
        "0.0.0.0",
        "--port",
        str(VLLM_PORT),
        "--trust-remote-code",
        "--mamba_ssm_cache_dtype",
        "float32",
        "--max-model-len",
        str(MAX_MODEL_LEN),
        "--tensor-parallel-size",
        "1",
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "qwen3_coder",
        "--reasoning-parser-plugin",
        "/root/nano_v3_reasoning_parser.py",
        "--reasoning-parser",
        "nano_v3",
    ]
    print("Starting vLLM:", " ".join(cmd))
    subprocess.Popen(cmd)


@app.local_entrypoint()
def main() -> None:
    """Print the deployed web URL for Vitál configuration."""
    url = serve.get_web_url()
    print("Vitál LLM endpoint (add /v1 for OpenAI client):")
    print(f"  {url}/v1")
    print("Set in .env:")
    print(f"  VITAL_LLM_BASE_URL={url}/v1")
    print(f"  VITAL_MODEL_ID={SERVED_MODEL_NAME}")
