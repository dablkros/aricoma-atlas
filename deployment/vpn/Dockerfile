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
COPY deployment/vpn/patches/strongswan-6.1.0-fortinet-eap-payload.patch /tmp/01-eap-payload.patch
COPY deployment/vpn/patches/strongswan-6.1.0-fortinet-eap-authenticator.patch /tmp/02-eap-authenticator.patch
COPY deployment/vpn/patches/strongswan-6.1.0-fortinet-eap-gtc-otp.patch /tmp/03-eap-gtc-otp.patch
COPY deployment/vpn/patches/strongswan-6.1.0-fortinet-eap-auth.patch /tmp/04-eap-auth.patch
COPY deployment/vpn/patches/strongswan-6.1.0-fortinet-ike-auth-fct.patch /tmp/05-ike-auth-fct.patch
COPY deployment/vpn/patches/strongswan-6.1.0-fortinet-vendor-id.patch /tmp/06-vendor-id.patch
COPY deployment/vpn/patches/strongswan-6.1.0-fortinet-network-id.patch /tmp/07-network-id.patch
RUN patch -p1 < /tmp/01-eap-payload.patch \
    && patch -p1 < /tmp/02-eap-authenticator.patch \
    && patch -p1 < /tmp/03-eap-gtc-otp.patch \
    && patch -p1 < /tmp/04-eap-auth.patch \
    && patch -p1 < /tmp/05-ike-auth-fct.patch \
    && patch -p1 < /tmp/06-vendor-id.patch \
    && patch -p1 < /tmp/07-network-id.patch \
    && ./configure \
       --prefix=/usr/local \
       --sysconfdir=/usr/local/etc \
       --with-systemdsystemunitdir=/usr/local/lib/systemd/system \
       --enable-systemd \
       --enable-swanctl \
       --enable-vici \
       --enable-md4 \
       --enable-eap-identity \
       --enable-eap-mschapv2 \
       --enable-eap-gtc \
       --enable-openssl \
       --enable-kernel-netlink \
       --enable-socket-default \
       --enable-attr \
       --enable-resolve \
       --disable-ikev1 \
    && make -j"$(nproc)" \
    && make DESTDIR=/install install \
    && test -x /install/usr/local/sbin/swanctl \
    && test -x /install/usr/local/sbin/charon-systemd \
    && for plugin in md4 eap-identity eap-mschapv2 eap-gtc vici; do \
         test -f "/install/usr/local/lib/ipsec/plugins/libstrongswan-${plugin}.so"; \
       done \
    && nm -D /install/usr/local/lib/ipsec/plugins/libstrongswan-md4.so | grep -q 'md4_plugin_create' \
    && nm -D /install/usr/local/lib/ipsec/plugins/libstrongswan-eap-identity.so | grep -q 'eap_identity_plugin_create' \
    && nm -D /install/usr/local/lib/ipsec/plugins/libstrongswan-eap-mschapv2.so | grep -q 'eap_mschapv2_plugin_create' \
    && nm -D /install/usr/local/lib/ipsec/plugins/libstrongswan-eap-gtc.so | grep -q 'eap_gtc_plugin_create'

FROM docker.io/library/ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates iproute2 libssl3 libsystemd0 openfortivpn openvpn ppp python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

COPY deployment/vpn/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt
COPY --from=strongswan-build /install/ /
RUN ldconfig
COPY deployment/vpn/strongswan.conf /usr/local/etc/strongswan.conf
COPY deployment/vpn/strongswan-build-manifest.txt /usr/local/share/atlas/strongswan-build-manifest.txt
COPY atlas /app/atlas

WORKDIR /app
EXPOSE 8090
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=12 \
    CMD ["python", "-c", "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=2); assert json.load(r)['status']=='ok'"]
CMD ["python", "-m", "uvicorn", "atlas.vpn.worker_app:app", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]
