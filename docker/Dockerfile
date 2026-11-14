# OpenBSD/amd64 cross toolchain image (clang/lld + verified OpenBSD sysroot).
#   docker build -f docker/Dockerfile --build-arg OPENBSD_FLAVOR=8.0 -t openbsd-cross:8.0 .
#   docker build -f docker/Dockerfile --build-arg OPENBSD_FLAVOR=current -t openbsd-cross:current .
FROM debian:trixie-slim

ARG LLVM_VERSION=21
ARG OPENBSD_FLAVOR=8.0
# xbase holds the X11/Mesa headers and libraries that SDL2, GL and other GUI packages link against.
ARG OPENBSD_SETS="base comp xbase"
ARG OPENBSD_MIRROR=https://cdn.openbsd.org/pub/OpenBSD

# apt.llvm.org occasionally drops connections: let apt retry instead of failing the whole image build.
RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries \
 && apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && curl -fsSL --retry 5 https://apt.llvm.org/llvm-snapshot.gpg.key | gpg --dearmor -o /usr/share/keyrings/llvm.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/llvm.gpg] http://apt.llvm.org/trixie/ llvm-toolchain-trixie-${LLVM_VERSION} main" \
      > /etc/apt/sources.list.d/llvm.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      clang-${LLVM_VERSION} lld-${LLVM_VERSION} llvm-${LLVM_VERSION} \
      python3 python3-cryptography \
      make cmake meson ninja-build pkgconf file xz-utils patch git \
 && rm -rf /var/lib/apt/lists/*

ENV OPENBSD_LLVM_BIN=/usr/lib/llvm-${LLVM_VERSION}/bin \
    OPENBSD_CROSS=/opt/openbsd-cross \
    OPENBSD_FLAVOR=${OPENBSD_FLAVOR} \
    PATH=/opt/openbsd-cross/bin:$PATH

COPY src /tmp/openbsd-cross-src
RUN /tmp/openbsd-cross-src/wrappers/install.sh /opt/openbsd-cross ${OPENBSD_FLAVOR} \
 && OPENBSD_MIRROR=${OPENBSD_MIRROR} CACHE_DIR=/tmp/sets \
      openbsd-fetch-sysroot ${OPENBSD_FLAVOR} /opt/openbsd-cross/sysroot/${OPENBSD_FLAVOR} ${OPENBSD_SETS} \
 && rm -rf /tmp/sets /tmp/openbsd-cross-src \
 && x86_64-unknown-openbsd${OPENBSD_FLAVOR}-cc --version

WORKDIR /work
