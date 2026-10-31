FROM docker.io/library/ubuntu:24.04 AS strongswan-build

ARG STRONGSWAN_VERSION=6.1.0
ARG STRONGSWAN_SHA256=fe6c97481298767213cfc2e9a1da29fdd8018d481ff4cb9cf0283099654f20d4

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       build-essential ca-certificates curl bzip2 patch pkg-config \
       libssl-dev libsystemd-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN curl --fail --show-error --location --output strongswan.tar.bz2 \
      "https://download.strongswan.org/strongswan-${STRONGSWAN_VERSION}.tar.bz2" \
    && echo "${STRONGSWAN_SHA256}  strongswan.tar.bz2" | sha256sum --check - \
    && tar -xjf strongswan.tar.bz2 --strip-components=1
COPY deployment/vpn/patches/strongswan-6.1.0-fortinet-network-id.patch /tmp/network-id.patch
RUN patch -p1 < /tmp/network-id.patch \
    && ./configure \
       --prefix=/usr/local \
       --sysconfdir=/usr/local/etc \
       --enable-systemd \
       --enable-swanctl \
       --enable-eap-identity \
       --enable-eap-mschapv2 \
       --enable-openssl \
       --disable-ikev1 \
    && make -j"$(nproc)" \
    && make DESTDIR=/install install

FROM docker.io/library/ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates iproute2 libssl3 libsystemd0 openfortivpn ppp python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

COPY deployment/vpn/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt
COPY --from=strongswan-build /install/ /
RUN ldconfig
COPY deployment/vpn/strongswan.conf /usr/local/etc/strongswan.conf
COPY atlas /app/atlas

WORKDIR /app
EXPOSE 8090
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=12 \
    CMD ["python", "-c", "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=2); assert json.load(r)['status']=='ok'"]
CMD ["python", "-m", "uvicorn", "atlas.vpn.worker_app:app", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]
