FROM python:3.12-slim
# Lets the Pi's deploy remove this app's old images and no one else's
LABEL org.opencontainers.image.title="ancient-greek-reader"
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY greek.py .
COPY greek/ greek/
# Shipped with the data export (greek export-data)
COPY DATA-LICENSE.md .
# Record the commit the code is from (greek --version, /version) from .git's
# HEAD and refs: .dockerignore lets in just them, a few KB
COPY .git/ /tmp/checkout/.git/
RUN python -m greek.version build /tmp/checkout && rm -rf /tmp/checkout
RUN mkdir -p /data/texts
ENV GREEK_DATA_DIR=/data
ENTRYPOINT ["python", "greek.py"]
CMD ["--help"]
