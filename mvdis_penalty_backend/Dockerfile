FROM python:3.13-slim AS ocr-assets

COPY ocr-assets-requirements.txt /tmp/ocr-assets-requirements.txt
COPY tools/extract_ocr_assets.py /tmp/extract_ocr_assets.py
RUN python -m pip download \
        --no-deps \
        --only-binary=:all: \
        --require-hashes \
        --requirement /tmp/ocr-assets-requirements.txt \
        --dest /tmp/wheel \
    && python /tmp/extract_ocr_assets.py /tmp/wheel/ddddocr-1.6.1-py3-none-any.whl /ocr_assets

FROM python:3.13-slim

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}" \
      org.opencontainers.image.title="Taiwan MVDIS Penalty" \
      org.opencontainers.image.description="Automatic MVDIS checks and Home Assistant notifications" \
      org.opencontainers.image.source="https://github.com/sunnylinisme/ha-mvdis-penalty" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-compile --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY app /app
COPY --from=ocr-assets /ocr_assets /app/ocr_assets
COPY THIRD_PARTY_NOTICES.md /app/THIRD_PARTY_NOTICES.md

EXPOSE 8099

CMD ["python", "/app/server.py"]
