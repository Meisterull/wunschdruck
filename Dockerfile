FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8

# GTK/WebKit/GL: Laufzeit-Abhaengigkeiten des Snapmaker-Orca-Binaries (Ubuntu-24.04-Build);
# xvfb, weil die Orca-CLI auch headless ein Display-Backend erwartet.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv \
    libopengl0 libglu1-mesa libegl1 libgl1 \
    libgtk-3-0t64 libwebkit2gtk-4.1-0 \
    libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 gstreamer1.0-gl \
    xvfb xauth fontconfig locales ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Entpackter Snapmaker-Orca-AppImage wird nach /opt/orca gelegt (siehe docker-compose.yml)
ENV ORCA_APPRUN=/opt/orca/AppRun

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY werkzeuge/ werkzeuge/

EXPOSE 8446
CMD ["/venv/bin/uvicorn", "app.haupt:app", "--host", "0.0.0.0", "--port", "8446"]
