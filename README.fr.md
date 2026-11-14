# cross-pkg-openbsd

Compilation croisée de logiciels C/C++ pour **OpenBSD/amd64** depuis Linux avec **clang/lld
upstream**, pour la dernière version stable (8.0) et pour **-current**, avec production de
**paquets `.tgz` signés, installables directement par `pkg_add`**. Conçu pour Docker / GitHub Actions.

Aucun compilateur n'est construit : clang et lld savent déjà faire de la compilation croisée.
La toolchain se compose d'un sysroot extrait des **sets officiels vérifiés par signify**, de
wrappers légers, de fichiers d'intégration CMake/Meson/autotools et d'un générateur de paquets
en Python pur.

English: [README.md](README.md) · Étude de faisabilité : [docs/FAISABILITE.md](docs/FAISABILITE.md) ·
Architecture : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## État

| Vérifié sous Linux | Pas encore vérifié |
|---|---|
| Récupération du sysroot + vérification signify (8.0 et -current) | Exécution sur OpenBSD **8.0** (fait par le job CI `test-stable`) |
| C, C++ (libc++), bibliothèques partagées : PIE, IBT, notes OpenBSD identiques aux binaires natifs | Build de l'image Docker, workflows GitHub (écrits, pas exécutés) |
| Dépendances issues des paquets officiels (jq, sdl2 + X11 via `xbase`) | Acceptation des paquets générés par `pkg_add` |
| Build CMake d'un programme SDL2, empaqueté avec `@wantlib`/`@depend` automatiques | |
| Signatures de paquets compatibles octet pour octet avec `signify -z` (comparées à de vrais paquets) | |
| **sdl2demo fonctionne sur une vraie machine OpenBSD -current** (2026-11-14, snapshot du jour) | |

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

## Limites connues

- Le clang d'OpenBSD a des patchs locaux (ex. **retguard**) absents de l'upstream : les binaires
  fonctionnent mais sans ce durcissement.
- **-current casse l'ABI** régulièrement (`libutil` 22 → 23 diffère déjà de la 8.0) : reconstruire
  souvent, ne jamais mélanger les saveurs.
- Liaison statique non testée et déconseillée.
- Les builds qui exécutent des binaires cibles pendant la compilation nécessitent un build natif.
