# Mint-Gate production image (Hugging Face Spaces, Docker SDK, CPU).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    HF_HUB_OFFLINE=0 \
    APP_ENV=production \
    PORT=7860 \
    TRUSTED_PROXIES=1 \
    STORAGE_DIR=/tmp/mintgate \
    REFERENCE_DIR=/app/reference \
    REFERENCE_ART_DIR=/app/reference/art \
    EXPLORER_URL=https://sepolia.etherscan.io \
    RPC_URL=https://ethereum-sepolia-rpc.publicnode.com

WORKDIR /app

# CPU-only PyTorch keeps the image ~1.5 GB smaller than the CUDA build.
COPY requirements-deploy.txt .
RUN pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements-deploy.txt

# Bake CLIP into the image so start-up needs no download.
RUN python -c "from transformers import CLIPModel, CLIPProcessor; \
CLIPModel.from_pretrained('openai/clip-vit-base-patch32'); CLIPProcessor.from_pretrained('openai/clip-vit-base-patch32')"

COPY backend backend
COPY frontend frontend
COPY reference.zip .
RUN python -c "import zipfile; zipfile.ZipFile('reference.zip').extractall('/app/reference')" && rm reference.zip

# Spaces run containers as uid 1000.
RUN useradd -m -u 1000 user && mkdir -p /tmp/mintgate && chown -R user /app /tmp/mintgate
USER user
ENV HF_HUB_OFFLINE=1
WORKDIR /app/backend
EXPOSE 7860
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--timeout", "180", "--bind", "0.0.0.0:7860", "wsgi:app"]
