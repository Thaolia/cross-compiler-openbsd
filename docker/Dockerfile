# OpenBSD/amd64 cross toolchain image (clang/lld + verified OpenBSD sysroot).
#   docker build -f docker/Dockerfile --build-arg OPENBSD_FLAVOR=8.0 -t openbsd-cross:8.0 .
#   docker build -f docker/Dockerfile --build-arg OPENBSD_FLAVOR=current -t openbsd-cross:current .
FROM debian:trixie-slim

# uv provides the host CPython that runs Nuitka: it must have the target python package's
# major.minor, which Debian does not ship. uv checks the sha256 of the Python it downloads.
COPY --from=ghcr.io/astral-sh/uv:0.12.11@sha256:79c6f4776b851471cc73b7d21d0cc834bb94383c292e83640d27eff512864df7 /uv /usr/local/bin/uv

ARG LLVM_VERSION=21
ARG OPENBSD_FLAVOR=8.0
# xbase holds the X11/Mesa headers and libraries that SDL2, GL and other GUI packages link against.
ARG OPENBSD_SETS="base comp xbase"
ARG OPENBSD_MIRROR=https://cdn.openbsd.org/pub/OpenBSD
# Python branch of the flavor's python package, and the Nuitka version openbsd-nuitka supports.
ARG OPENBSD_PYTHON=3.14
ARG NUITKA_VERSION=4.3.5

# apt.llvm.org occasionally drops connections: let apt retry instead of failing the whole image build.
RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries \
 && apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && curl -fsSL --retry 5 --retry-all-errors -o /tmp/llvm.key https://apt.llvm.org/llvm-snapshot.gpg.key \
 && gpg --dearmor -o /usr/share/keyrings/llvm.gpg /tmp/llvm.key && rm /tmp/llvm.key \
 && echo "deb [signed-by=/usr/share/keyrings/llvm.gpg] https://apt.llvm.org/trixie/ llvm-toolchain-trixie-${LLVM_VERSION} main" \
      > /etc/apt/sources.list.d/llvm.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      clang-${LLVM_VERSION} lld-${LLVM_VERSION} llvm-${LLVM_VERSION} \
      python3 python3-cryptography \
      make cmake meson ninja-build pkgconf file xz-utils patch git \
      gcc libc6-dev patchelf \
 && rm -rf /var/lib/apt/lists/*

ENV OPENBSD_LLVM_BIN=/usr/lib/llvm-${LLVM_VERSION}/bin \
    OPENBSD_CROSS=/opt/openbsd-cross \
    OPENBSD_FLAVOR=${OPENBSD_FLAVOR} \
    OPENBSD_NUITKA_PYTHON=/opt/openbsd-cross/host-python/python3 \
    OPENBSD_NUITKA_PATH=/opt/openbsd-cross/host-python/nuitka \
    PATH=/opt/openbsd-cross/bin:$PATH

COPY src /tmp/openbsd-cross-src
RUN /tmp/openbsd-cross-src/wrappers/install.sh /opt/openbsd-cross ${OPENBSD_FLAVOR} \
 && OPENBSD_MIRROR=${OPENBSD_MIRROR} CACHE_DIR=/tmp/sets \
      openbsd-fetch-sysroot ${OPENBSD_FLAVOR} /opt/openbsd-cross/sysroot/${OPENBSD_FLAVOR} ${OPENBSD_SETS} \
 && rm -rf /tmp/sets /tmp/openbsd-cross-src \
 && x86_64-unknown-openbsd${OPENBSD_FLAVOR}-cc --version

# Nuitka support: the target python package in the sysroot, and a matching host Python + Nuitka.
RUN openbsd-pkg-fetch --sysroot /opt/openbsd-cross/sysroot/${OPENBSD_FLAVOR} python%${OPENBSD_PYTHON} \
 && rm -rf /opt/openbsd-cross/sysroot/cache \
 && export UV_PYTHON_INSTALL_DIR=/opt/openbsd-cross/host-python/cpython UV_NO_CACHE=1 \
 && uv python install ${OPENBSD_PYTHON} \
 && ln -s "$(uv python find --managed-python ${OPENBSD_PYTHON})" "$OPENBSD_NUITKA_PYTHON" \
 && uv pip install --python "$OPENBSD_NUITKA_PYTHON" --target "$OPENBSD_NUITKA_PATH" \
      nuitka==${NUITKA_VERSION} \
 && openbsd-nuitka --flavor ${OPENBSD_FLAVOR} --print-depend

WORKDIR /work
