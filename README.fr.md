# cross-pkg-openbsd

Compilation croisée de logiciels C/C++ (et de programmes Python, via **Nuitka**) pour
**OpenBSD/amd64** depuis Linux avec **clang/lld upstream**, pour la dernière version stable (8.0) et pour **-current**, avec production de
**paquets `.tgz` signés, installables directement par `pkg_add`**. Conçu pour Docker / GitHub Actions.

Aucun compilateur n'est construit : clang et lld savent déjà faire de la compilation croisée.
La toolchain se compose d'un sysroot extrait des **sets officiels vérifiés par signify**, de
wrappers légers, de fichiers d'intégration CMake/Meson/autotools et d'un générateur de paquets
en Python pur.

English: [README.md](README.md) · Étude de faisabilité : [docs/FAISABILITE.md](docs/FAISABILITE.md) ·
Architecture : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## État

| Vérifié | Pas encore vérifié |
|---|---|
| Récupération du sysroot + vérification signify (8.0 et -current) | Installation par `pkg_add` sur une vraie machine -current |
| | Exécution sur OpenBSD d'un programme Nuitka onefile (job CI `test-stable` étendu, pas encore exécuté) |
| C, C++ (libc++), bibliothèques partagées : PIE, IBT, notes OpenBSD identiques aux binaires natifs | |
| Dépendances issues des paquets officiels (jq, sdl2 + X11 via `xbase`) | |
| Build CMake d'un programme SDL2, empaqueté avec `@wantlib`/`@depend` automatiques | |
| Signatures de paquets compatibles octet pour octet avec `signify -z` (comparées à de vrais paquets) | |
| **sdl2demo fonctionne sur une vraie machine OpenBSD -current** (2026-11-14, snapshot du jour) | |
| **CI dans une VM OpenBSD 8.0** : `pkg_add` des deux paquets d'exemple (dépendances récupérées par pkg_add), exécution des deux, `pkg_delete` (2026-11-15) | |
| Images Docker `openbsd-cross:8.0` / `:current` construites et publiées par la CI | |
| Programme Nuitka (mode accelerated) compilé pour 8.0 et -current : marques ELF identiques au `python3.14` officiel, empaqueté avec le même `@depend` que les paquets `py3-*` officiels | |
| Programme Nuitka standalone compilé pour 8.0 et -current : libpython, modules d'extension et bibliothèques de paquets copiés depuis le sysroot, `DT_RPATH $ORIGIN`, aucun `@depend`, seulement des `@wantlib` du système de base | |
| **Programmes Nuitka exécutés dans une VM OpenBSD 8.0** (CI `test-stable`, 2026-11-16) : accelerated avec le paquet python officiel, standalone sans lui | |
| Programme Nuitka onefile compilé pour 8.0 et -current : le répertoire standalone empaqueté dans le bootstrap de Nuitka, qui n'a besoin que de la `libc` | |

## Démarrage rapide

```sh
src/wrappers/install.sh /opt/openbsd-cross 8.0 current
export PATH=/opt/openbsd-cross/bin:$PATH
openbsd-fetch-sysroot 8.0 /opt/openbsd-cross/sysroot/8.0 base comp xbase
openbsd-fetch-sysroot current /opt/openbsd-cross/sysroot/current base comp xbase

x86_64-unknown-openbsd8.0-cc -O2 -o hello hello.c

openbsd-pkg-fetch --sysroot "$(x86_64-unknown-openbsd8.0-sysroot)" sdl2
openbsd-signify generate -p mykey-pkg.pub -s mykey-pkg.sec
tests/build-example.sh tests/sdl2demo 8.0 out mykey-pkg.sec
```

Sur OpenBSD : copier `mykey-pkg.pub` dans `/etc/signify/`, puis `pkg_add ./sdl2demo-0.1.tgz`
(ou `pkg_add -D unsigned` pour un paquet non signé).

## Programmes Python (Nuitka)

`openbsd-nuitka` compile un programme Python avec [Nuitka](https://nuitka.net) pour OpenBSD,
selon trois modes :

- **accelerated** (par défaut) : un seul exécutable, lié à la `libpython` du paquet officiel
  `python` et utilisant sa bibliothèque standard ; le paquet OpenBSD dépend de `python`.
- **standalone** (`--mode standalone`) : un répertoire contenant l'exécutable, les modules
  d'extension et les bibliothèques de paquets nécessaires (`libpython`, `libintl`,
  `libsqlite3`...), tous copiés depuis les paquets officiels du sysroot. Il fonctionne sans le
  paquet `python` ; les bibliothèques du système de base (`libc`, `libssl`...) viennent de l'OS,
  donc le build ne tourne que sur la version correspondante.
- **onefile** (`--mode onefile`) : le répertoire standalone empaqueté dans un seul exécutable,
  qui le décompresse dans `$TMPDIR` (ou `/tmp`) à chaque lancement, exécute le programme depuis
  là et le supprime à la sortie. `--name` nomme le programme intérieur (par défaut : le nom du
  script).

```sh
openbsd-pkg-fetch --sysroot "$(x86_64-unknown-openbsd8.0-sysroot)" python%3.14
openbsd-nuitka --flavor 8.0 -o stage/usr/local/bin/hello hello.py   # [-- <options nuitka>]
openbsd-nuitka --flavor 8.0 --print-depend   # argument --depend pour openbsd-pkg-create
openbsd-nuitka --flavor 8.0 --mode standalone -o stage/usr/local/libexec/hello hello.py
ln -s ../libexec/hello/hello stage/usr/local/bin/hello
openbsd-nuitka --flavor 8.0 --mode onefile -o stage/usr/local/bin/hello hello.py
tests/build-example.sh tests/nuitkademo 8.0 out mykey-pkg.sec        # exemples complets
tests/build-example.sh tests/nuitkastandalone 8.0 out mykey-pkg.sec
tests/build-example.sh tests/nuitkaonefile 8.0 out mykey-pkg.sec
```

- Le Python qui exécute Nuitka doit avoir **le même major.minor que la cible** (3.14 pour 8.0
  et -current aujourd'hui) : définir `OPENBSD_NUITKA_PYTHON` (par ex. un interpréteur installé
  par `uv python install 3.14`) et `OPENBSD_NUITKA_PATH` si Nuitka est installé par
  `pip install --target`. Version vérifiée : Nuitka 4.3.5 ; il faut aussi un compilateur C
  hôte (`gcc`), et `patchelf` en standalone et en onefile (exigé par la construction hôte de
  Nuitka sous Linux).
- Nuitka ne sait pas faire de compilation croisée : il construit d'abord pour l'hôte, puis le C
  généré est recompilé par le compilateur OpenBSD contre les en-têtes Python du sysroot. Les
  modules locaux sont compilés (`--follow-imports`). En accelerated, la bibliothèque standard
  vient du paquet python de la cible. En standalone, elle est embarquée sous forme de bytecode
  de la bibliothèque standard du Python **de l'hôte** (préférer un CPython non modifié, comme celui
  d'uv, à celui d'une distribution qui le patche), sauf `_sysconfigdata`, pris dans le sysroot.
- Les modules d'extension C tiers ne sont pas compilés : en accelerated, dépendre du paquet
  `py3-*` officiel (`openbsd-pkg-create --depend ...`) ; en standalone, ils sont copiés depuis
  le sysroot, donc récupérer ce paquet d'abord (`openbsd-pkg-fetch ... py3-foo`).
- Paquets standalone : les objets partagés hors de `lib/` sont empaquetés en `@so` (comme les
  modules d'extension du paquet python officiel), pour que `pkg_add` n'enregistre pas leur
  répertoire auprès de `ldconfig`.
- Onefile : le programme garde la marque `wxneeded` du `python3.X` officiel mais s'exécute
  depuis `/tmp`, qui n'est pas monté `wxallowed` par défaut. OpenBSD le lance quand même ; seules
  les projections mémoire W|X y échouent (par ex. les closures libffi des callbacks `ctypes`).
  Les paquets onefile ne déclarent que la `@wantlib c` du bootstrap : `pkg_create` ne voit pas
  les bibliothèques de base du programme empaqueté, donc rien n'empêche de les installer sur
  une autre version.

L'image Docker contient aussi le paquet python dans son sysroot, un CPython de même version
géré par uv et Nuitka (arguments de build `OPENBSD_PYTHON`, 3.14 par défaut, et
`NUITKA_VERSION`, 4.3.5 par défaut).

## Limites connues

- Le clang d'OpenBSD a des patchs locaux (ex. **retguard**) absents de l'upstream : les binaires
  fonctionnent mais sans ce durcissement.
- **-current casse l'ABI** régulièrement (`libutil` 22 → 23 diffère déjà de la 8.0) : reconstruire
  souvent, ne jamais mélanger les saveurs.
- Liaison statique non testée et déconseillée.
- Les builds qui exécutent des binaires cibles pendant la compilation nécessitent un build natif.
- Nuitka : le C est généré par la version corrective de l'hôte (par ex. 3.14.7) et compilé contre
  les en-têtes de la cible (3.14.9 sur -current aujourd'hui). CPython garde son ABI au sein d'une
  version mineure, mais préférer la même version corrective et reconstruire après une mise à jour
  de python. En standalone, la bibliothèque standard embarquée est aussi celle de la version
  corrective de l'hôte.
- Nuitka onefile : seule l'extraction par défaut (un nouveau répertoire temporaire à chaque
  lancement) a été vérifiée ; les builds dont le programme principal est hors du payload sont
  refusés.
