# Architecture

```
src/sysroot/fetch-sysroot.sh   SHA256.sig (signify) -> sha256 des sets -> extraction filtrée
                               -> liens libfoo.so -> libfoo.so.MAJ.MIN -> .sysroot-info
src/wrappers/openbsd-cross     wrapper multi-appel : x86_64-unknown-openbsd<flavor>-<tool>
src/wrappers/install.sh        installe wrappers, fichiers CMake/Meson/config.site, pkgtools
src/pkgtools/signify.py        Ed25519 : generate, verify-embedded, verify-gzip, sign-gzip
src/pkgtools/pkg_fetch.py      paquets officiels -> sysroot/usr/local + sysroot/var/db/pkg/<pkg>/+CONTENTS
src/pkgtools/pkg_create.py     staging -> +CONTENTS/+DESC -> ustar -> gzip -> signature
src/pkgtools/elfinfo.py        lecture ELF64 minimale (type, types de program headers, INTERP, NEEDED, SONAME)
src/pkgtools/pkgcommon.py      mtree, packing-list, écriture ustar
src/nuitka/nuitka_cross.py     programme Python -> build hôte Nuitka -> recompilation du C pour OpenBSD
src/nuitka/openbsd_target_plugin.py  plugin Nuitka : sys.executable/sys.prefix de la cible
src/nuitka/standalone.py       mode standalone : rapport Nuitka -> modules d'extension et bibliothèques du sysroot
```

## Installation (préfixe)

```
<prefix>/bin/openbsd-cross, x86_64-unknown-openbsd{8.0,current}-{cc,c++,ld,ar,...,pkg-config,sysroot}
<prefix>/bin/openbsd-{fetch-sysroot,pkg-fetch,pkg-create,signify,nuitka}
<prefix>/share/openbsd-cross/{openbsd-toolchain.cmake,openbsd-<flavor>.ini,config.site}
<prefix>/libexec/openbsd-cross/{pkgtools,nuitka,sysroot,keys}
<prefix>/sysroot/<flavor>/          sysroot + cache/ voisin (sets, paquets)
```

## Wrapper

- `--target=x86_64-unknown-openbsd<ver> --sysroot=<sysroot>` ; la version vient de `.sysroot-info`.
- Édition de liens : `--ld-path=<ld.lld>`, et `-pie` pour un exécutable (pas pour `-shared`, `-r`, `-static`, `-nopie`).
- Variables : `OPENBSD_SYSROOT`, `OPENBSD_CLANG`, `OPENBSD_CLANGXX`, `OPENBSD_LLD`, `OPENBSD_LLVM_BIN`.
- `pkg-config` : `PKG_CONFIG_SYSROOT_DIR` + `PKG_CONFIG_LIBDIR` limités au sysroot.

## pkg_create

- Annotations : ELF exécutable → `@bin` ; `lib/libX.so.M.m` → `@lib` (refus d'un `lib/lib*.so` mal nommé) ;
  tout autre objet partagé (modules d'extension Python, plugins, bibliothèques privées d'un
  programme standalone) → `@so`, mode 0755, comme dans le paquet python officiel : un `@lib`
  ferait enregistrer son répertoire par `ldconfig`. Les `libX.so.M.m` en `@so` comptent quand même
  comme fournies par le paquet (pas de `@wantlib`). `lib*.a` → `@static-lib` ;
  `man/` → `@man` ; `info/*.info` → `@info` ; liens symboliques → `@symlink`.
- Répertoires absents de `etc/mtree/4.4BSD.dist` → entrée `dir/`.
- `@wantlib` : `DT_NEEDED` hors bibliothèques fournies par le paquet ; trouvés dans `usr/lib`,
  `usr/X11R6/lib`, ou dans un paquet enregistré par `pkg_fetch`, ce qui ajoute alors un `@depend` automatique.
- Signature : clé nommée `*-pkg.sec`, car `pkg_add` ne fait confiance qu'à `/etc/signify/*-pkg.pub`.

## pkg_fetch

- Noms acceptés : radical (`jq`), nom complet (`jq-1.8.2`) ou radical%branche comme `pkg_add`
  (`python%3.14`, nécessaire quand plusieurs branches coexistent : python-2.7 et python-3.14).

## Nuitka (openbsd-nuitka)

Mode *accelerated* : le binaire contient le code compilé du programme et de ses modules locaux,
et charge `libpython3.X.so` et la bibliothèque standard du paquet officiel `python`.

1. Contrôles : un seul `include/python3.X` dans le sysroot, `PY_VERSION` lu dans `patchlevel.h`,
   pas de build free-threaded, paquet `python-3.X*` enregistré dans `var/db/pkg`, `libpython3.X.so.*`
   présent ; l'interpréteur hôte (`OPENBSD_NUITKA_PYTHON`) doit avoir le même major.minor.
2. Build hôte normal : `python -m nuitka --mode=accelerated --follow-imports
   --file-reference-choice=runtime --lto=no --static-libpython=no --show-scons
   --user-plugin=openbsd_target_plugin.py`. `CC`/`CFLAGS`… sont retirés de l'environnement :
   ce build utilise le compilateur hôte. Il produit tous les fichiers générés (C des modules,
   blob de constantes, `build_definitions.h`, `static_src/`, `@link_input.txt`).
3. Le plugin remplace, pendant la seule génération des constantes, `sys.executable` et
   `sys.prefix`/`exec_prefix`/`base_*` par `/usr/local/bin/python3.X` et `/usr/local` : un
   programme accelerated les restaure au démarrage. `--file-reference-choice=runtime` évite que
   `__file__` contienne les chemins de build.
4. Recompilation : les sources = objets de `@link_input.txt` ; drapeaux repris de la première
   commande de compilation affichée par `--show-scons` (`-std`, `-D`, `-I` de Nuitka), les `-I`
   Python de l'hôte remplacés par ceux du sysroot. `_NUITKA_ADAPTED_PYTHON_HEADERS` et le cache
   d'en-têtes adaptés sont retirés : Nuitka n'adapte les en-têtes que sous Linux et Windows, un
   build natif OpenBSD utilise les en-têtes d'origine.
5. Édition de liens : `-lpython3.X -lm` (comme le lien natif de Nuitka), plus `-z wxneeded` / `-z nobtcfi`
   recopiés des program headers de `/usr/local/bin/python3.X` du sysroot (même interpréteur,
   mêmes modules d'extension chargés). Puis `strip`.
6. `--print-depend` donne `lang/python/3,-main:python->=3.X:python-3.X.Yp0`, la forme des paquets
   `py3-*` officiels ; `pkg_create --depend` remplace alors le `python-*` générique déduit de
   la `@wantlib`, que python-2.7 satisferait.

Mode *standalone* (`--mode standalone`, `-o` = répertoire, exécutable nommé d'après le script ou
`--name`) : mêmes étapes 1 à 5 avec `--mode=standalone`, puis assemblage (`standalone.py`) :

- Build hôte : le bytecode de la bibliothèque standard de l'hôte est embarqué dans le binaire
  (blob de constantes). Les `_sysconfigdata_*` de l'hôte (Linux, chemins du build) sont exclus par
  `--nofollow-import-to` ; celui du sysroot (`_sysconfigdata__openbsdN_`) est copié dans un
  répertoire ajouté au `PYTHONPATH` du build et inclus par `--include-module`, pour que
  `sysconfig.get_config_vars()` fonctionne sur OpenBSD. En standalone, Nuitka calcule `sys.prefix`
  (répertoire du programme) à l'exécution ; `sys.executable` = `<répertoire>/python3.X`.
- Lien : en plus, `-Wl,-rpath,$ORIGIN -Wl,--disable-new-dtags`. Un DT_RPATH et non un
  DT_RUNPATH : `ld.so` d'OpenBSD (`library_subr.c`) cherche les dépendances d'un objet sans
  RUNPATH dans son RPATH puis dans le RPATH du programme principal, ce qui couvre les modules
  d'extension chargés par `dlopen()` (ils n'ont aucun rpath). `$ORIGIN` du programme vient de
  `AUX_openbsd_execpath`, chemin réel calculé par le noyau, donc le lien `bin/<nom>` →
  `libexec/<nom>/<nom>` fonctionne. Nuitka retrouve son répertoire par `getexecpath()` (OpenBSD ≥ 202610).
- Modules d'extension : ceux du rapport `--report` du build hôte, cherchés dans `lib-dynload`
  (stdlib) puis `site-packages` du sysroot (paquets `py3-*`), copiés en `<chemin/du/module>.so`
  comme dans le `*.dist` de l'hôte (c'est le nom que cherche le chargeur de Nuitka). Un module de la
  stdlib absent du paquet OpenBSD est ignoré avec un avertissement ; un module tiers absent est une
  erreur. S'y ajoutent les modules de `lib-dynload` dont le programme se sert (usages du rapport) :
  beaucoup sont intégrés à l'interpréteur sur Linux (`math`, `zlib`, `_struct`…) mais sont des
  `.so` séparés dans le paquet OpenBSD ; les modules de test (`_test*`, `xx*`) sont exclus.
- Bibliothèques : fermeture des `DT_NEEDED` de l'exécutable et des modules ; celles de
  `usr/local/lib` sont copiées à côté du programme, celles de `usr/lib` et `usr/X11R6/lib` (sets de
  base) restent à l'OS.
- Fichiers de données : fichiers non ELF du `*.dist` de l'hôte, copiés tels quels ; les ELF de l'hôte
  sont remplacés (programme, modules) ou abandonnés (bibliothèques Linux), avec un avertissement
  pour tout ELF non reconnu.

Version de Nuitka vérifiée : `NUITKA_VERSION` dans `nuitka_cross.py` (avertissement sinon) ; le
plugin échoue explicitement si `CodeGeneration.getConstantsDefinitionCode` n'existe plus.

## CI

- `image.yml` : images `ghcr.io/<owner>/openbsd-cross:{8.0,current}`, reconstruites chaque nuit ;
  elles contiennent aussi le paquet python dans le sysroot, un CPython géré par uv
  (`OPENBSD_NUITKA_PYTHON`) et Nuitka (`OPENBSD_NUITKA_PATH`).
- `packages.yml` : build des exemples dans l'image, puis installation et exécution dans une VM OpenBSD 8.0,
  puis publication en release sur un tag `v*`.
