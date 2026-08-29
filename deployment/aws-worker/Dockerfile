FROM mwader/static-ffmpeg:7.1 AS ffmpeg

FROM public.ecr.aws/lambda/python:3.12

COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe
COPY requirements-worker-container.txt /tmp/requirements-worker-container.txt

RUN python -m pip install --no-cache-dir \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      "torch>=2.2,<3" "torchvision>=0.17,<1" && \
    python -m pip install --no-cache-dir -r /tmp/requirements-worker-container.txt && \
    python -m pip install --no-cache-dir "yolov5==7.0.14" && \
    python -m pip install --no-cache-dir --no-deps "megadetector==5.0.4" && \
    python -m pip install --no-cache-dir "opencv-python-headless>=4.8,<5" && \
    python -m pip check && \
    ffmpeg -version >/dev/null && \
    ffprobe -version >/dev/null

COPY backend ${LAMBDA_TASK_ROOT}/backend
COPY worker_adapter.py ${LAMBDA_TASK_ROOT}/worker_adapter.py
COPY models /opt/pba-model

ENV ML_MODEL_DIR=/opt/pba-model \
    ML_DEVICE=cpu \
    TORCH_HOME=/tmp/torch \
    YOLO_CONFIG_DIR=/tmp/yolo

CMD ["worker_adapter.handler"]
