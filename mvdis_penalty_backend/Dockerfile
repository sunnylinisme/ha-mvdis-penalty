FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS ocr-assets

COPY ocr-assets-requirements.txt /tmp/ocr-assets-requirements.txt
COPY tools/extract_ocr_assets.py /tmp/extract_ocr_assets.py
RUN python -m pip download \
        --no-deps \
        --only-binary=:all: \
        --require-hashes \
        --requirement /tmp/ocr-assets-requirements.txt \
        --dest /tmp/wheel \
    && python /tmp/extract_ocr_assets.py /tmp/wheel/ddddocr-1.6.1-py3-none-any.whl /ocr_assets

FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS python-deps

COPY requirements.txt /tmp/requirements.txt
RUN pip install \
        --no-cache-dir \
        --no-compile \
        --prefix /python-deps \
        --requirement /tmp/requirements.txt \
    && find /python-deps -exec touch -h -d @0 {} +

FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

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

COPY --from=python-deps /python-deps /usr/local
COPY --from=ocr-assets /ocr_assets /app/ocr_assets
COPY app /app
COPY THIRD_PARTY_NOTICES.md /app/THIRD_PARTY_NOTICES.md

EXPOSE 8099

CMD ["python", "/app/server.py"]
