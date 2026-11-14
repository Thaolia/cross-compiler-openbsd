# Architecture

```
src/sysroot/fetch-sysroot.sh   SHA256.sig (signify) -> sha256 des sets -> extraction filtrée
                               -> liens libfoo.so -> libfoo.so.MAJ.MIN -> .sysroot-info
src/wrappers/openbsd-cross     wrapper multi-appel : x86_64-unknown-openbsd<flavor>-<tool>
src/wrappers/install.sh        installe wrappers, fichiers CMake/Meson/config.site, pkgtools
src/pkgtools/signify.py        Ed25519 : generate, verify-embedded, verify-gzip, sign-gzip
src/pkgtools/pkg_fetch.py      paquets officiels -> sysroot/usr/local + sysroot/var/db/pkg/<pkg>/+CONTENTS
src/pkgtools/pkg_create.py     staging -> +CONTENTS/+DESC -> ustar -> gzip -> signature
src/pkgtools/elfinfo.py        lecture ELF64 minimale (type, INTERP, NEEDED, SONAME)
src/pkgtools/pkgcommon.py      mtree, packing-list, écriture ustar
```

## Installation (préfixe)

```
<prefix>/bin/openbsd-cross, x86_64-unknown-openbsd{8.0,current}-{cc,c++,ld,ar,...,pkg-config,sysroot}
<prefix>/bin/openbsd-{fetch-sysroot,pkg-fetch,pkg-create,signify}
<prefix>/share/openbsd-cross/{openbsd-toolchain.cmake,openbsd-<flavor>.ini,config.site}
<prefix>/libexec/openbsd-cross/{pkgtools,sysroot,keys}
<prefix>/sysroot/<flavor>/          sysroot + cache/ voisin (sets, paquets)
```

## Wrapper

- `--target=x86_64-unknown-openbsd<ver> --sysroot=<sysroot>` ; la version vient de `.sysroot-info`.
- Édition de liens : `--ld-path=<ld.lld>`, et `-pie` pour un exécutable (pas pour `-shared`, `-r`, `-static`, `-nopie`).
- Variables : `OPENBSD_SYSROOT`, `OPENBSD_CLANG`, `OPENBSD_CLANGXX`, `OPENBSD_LLD`, `OPENBSD_LLVM_BIN`.
- `pkg-config` : `PKG_CONFIG_SYSROOT_DIR` + `PKG_CONFIG_LIBDIR` limités au sysroot.

## pkg_create

- Annotations : ELF exécutable → `@bin` ; `libX.so.M.m` → `@lib` (refus sinon) ; `lib*.a` → `@static-lib` ;
  `man/` → `@man` ; `info/*.info` → `@info` ; liens symboliques → `@symlink`.
- Répertoires absents de `etc/mtree/4.4BSD.dist` → entrée `dir/`.
- `@wantlib` : `DT_NEEDED` hors bibliothèques fournies par le paquet ; trouvés dans `usr/lib`,
  `usr/X11R6/lib`, ou dans un paquet enregistré par `pkg_fetch`, ce qui ajoute alors un `@depend` automatique.
- Signature : clé nommée `*-pkg.sec`, car `pkg_add` ne fait confiance qu'à `/etc/signify/*-pkg.pub`.

## CI

- `image.yml` : images `ghcr.io/<owner>/openbsd-cross:{8.0,current}`, reconstruites chaque nuit.
- `packages.yml` : build des exemples dans l'image, puis installation et exécution dans une VM OpenBSD 8.0,
  puis publication en release sur un tag `v*`.
