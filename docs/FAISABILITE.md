# Étude de faisabilité — toolchain croisée Linux → OpenBSD/amd64

Date : 2026-11-14. Cibles : OpenBSD **8.0** (stable) et **-current** (snapshots du 2026-11-14, sets `80`).

## Verdict

**Faisable, et démontré** pour la partie Linux : partir de zéro est plus simple que de
modifier crosstool-ng.

| Critère | crosstool-ng | Solution retenue |
|---|---|---|
| Cible OpenBSD | non supportée (glibc/musl/newlib/uClibc) | oui |
| Compilateur | construit GCC + binutils | clang/lld upstream, déjà cross |
| libc | compilée depuis les sources | sets officiels `base`/`comp`/`xbase` = ABI exacte |
| Volume de code | port d'une libc dans ct-ng | ~1 000 lignes (sh + Python) |

## Ce qui a été vérifié (preuves)

1. **Sets officiels** : `SHA256.sig` vérifié avec `openbsd-80-base.pub`, puis sha256 de chaque set.
   Les clés de `src/keys/` viennent du miroir GitHub `openbsd/src` et sont identiques à celles du set `base80` signé.
2. **Binaires** : comparés à `/usr/bin/tee` natif 8.0, avec la même liste de segments
   (`PHDR INTERP LOAD×4 DYNAMIC GNU_RELRO GNU_EH_FRAME OPENBSD_RANDOM GNU_STACK GNU_PROPERTY NOTE×2`),
   `FLAGS_1 PIE`, `.note.openbsd.ident`, propriété `x86 feature: IBT`, `NEEDED libc.so.104.0`.
3. **Défaut corrigé** : clang upstream génère du code PIE (avec `-fcf-protection=branch` et le stack protector),
   mais **ne lie pas en PIE** pour OpenBSD (on obtient `ET_EXEC`). Le wrapper ajoute `-pie`, comme le `ld` d'OpenBSD.
4. **Format de paquet** : analysé sur `jq-1.8.2.tgz` (8.0). Il s'agit d'un ustar gzip dont `+CONTENTS` est la première entrée,
   avec une signature signify embarquée dans le commentaire gzip (`SHA512/256`, blocs de 64 Kio).
   L'implémentation Python vérifie les vrais paquets et rejette ceux qui sont altérés, et ses en-têtes tar sont
   identiques à ceux d'OpenBSD (`root/bin`, gid 7, mtime 0, `0100755` ; métadonnées en `root/wheel`, `0444`).
5. **Dépendances** : `sdl2-2.32.10p1` (+ libsamplerate, libiconv) et `jq` (+ oniguruma) sont installés dans le sysroot.
   Le paquet produit contient `@depend devel/sdl2:sdl2-*:sdl2-2.32.10p1` et `@wantlib SDL2.0.15` automatiquement.
6. **-current** : même chaîne de bout en bout. Divergence d'ABI déjà visible par rapport à la 8.0 (`libutil.so.23.0` contre `22.0`).
7. **Exécution réelle sur -current** (2026-11-14) : `sdl2demo-0.1.tgz`, compilé contre le snapshot du jour,
   a été testé par l'utilisateur sur sa machine OpenBSD -current, et le binaire fonctionne.

8. **CI de bout en bout** (2026-11-15) : images Docker 8.0 et -current construites et publiées. Les exemples ont été
   compilés et empaquetés dans ces images. Dans une VM OpenBSD 8.0, `pkg_add` les a installés (en allant chercher
   jq et sdl2 tout seul), les deux binaires ont tourné et `pkg_delete` les a retirés.

## Ce qui reste à prouver

- `pkg_add` sur -current (le binaire y tourne ; l'installation par paquet n'a été confirmée qu'en 8.0).

## Risques

| Risque | Gravité | Parade |
|---|---|---|
| retguard absent (patch local d'OpenBSD) | moyenne | documenté ; pas de contournement upstream |
| ABI mouvante de -current | moyenne | sysroot reconstruit chaque nuit (workflow `image.yml`) |
| Builds qui exécutent des binaires cibles | moyenne | `config.site`, sinon build natif en VM |
| Sous-ensemble de `pkg_create` (pas de `@exec`, `@sample`, `@newuser`, setuid) | faible | à étendre au besoin |
| Paquets officiels -current mis à jour en continu | faible | `pkg_fetch` suit l'index du jour |
