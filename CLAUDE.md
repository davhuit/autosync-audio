# CLAUDE.md — contexte complet du projet « autosync audio VO/VF »

À lire en entier avant de répondre. Ce fichier sert à redonner à Claude tout le contexte (but, historique, état
final, décisions, façon de travailler avec l'utilisateur). Le script est `autosync_audio.py` (version
**générique** : `DEFAULT_CUTS` et `DEFAULT_STAIRS` vides), à joindre avec ce fichier **et `README.md`** (toutes les
options et les cas fréquents). Avec ces trois fichiers, Claude donne lui-même la commande d'analyse `--dry-run`
adaptée ; l'utilisateur la lance et colle la sortie (ou la joint d'emblée s'il l'a déjà).
La version validée sur le premier film avait `DEFAULT_STAIRS` rempli ; sa valeur est gardée en commentaire en
haut du script.

## 1. But
Recaler la piste audio française (VF) sur la timeline de la VO :
- VO : « Meet The Santas » (2005), 24 i/s, 1:27:54.272, fichier `VO.mp4` (WEBRip 1080p YTS).
- VF : diffusion TV « Un amour de noël 2 », 25 i/s (vitesse PAL), 1:24:35.464, fichier `VF.mp4`.
- Le montage diffère : insertions de ~1 à 3 s dans la VF (fondus de diffusion, absents de la VO, qui a des coupes
  brutes), pas de pub dans la VO. Une fois remise à la bonne vitesse (ratio 0.95904) la VF fait ~18 s de plus.
- Un autre créateur a sorti sa propre resynchro (script « Audio Track Autosync », synchro_audio.py), que
  l'utilisateur n'a pas ; sa piste audio peut servir de comparaison. Ses 7 raccords (web-dl) : +3.01, +3.04, +2.55,
  +3.21, +2.56, +0.88, +2.92 s.

## 2. Environnement de l'utilisateur
Windows, Python 3.14 (`python -m pip install numpy scipy`), ffmpeg dans `C:\Apps\ffmpeg\bin` (le script cherche aussi `ffmpeg`/`ffprobe` dans son propre dossier d'abord), mkvmerge
(MKVToolNix). Dossier : `E:\-= Torrents =-\Meet The Santas (2005) [1080p] [WEBRip] [YTS.MX]\` avec `VO.mp4`,
`VF.mp4` et le script. Dossier temporaire utilisé : `F:\tmp`. L'utilisateur lance le script en ligne de commande
et colle la sortie console dans le chat. Il est francophone.

## 3. Le script autosync_audio.py (numpy, scipy, ffmpeg/ffprobe)
1. Décodage 4000 Hz mono → enveloppe d'attaque à 100 Hz (analyse seulement ; la sortie reste pleine qualité).
2. Ratio de vitesse : test des ratios classiques (24/25, 23.976/25…), puis correction de dérive. Retenu : 0.959040.
3. Découverte des décalages : corrélation FFT par fenêtres de 30 s ; passes supplémentaires (8 s, z ≥ 8).
4. Affectation par lissage de Viterbi (états = décalages candidats + « trou »), `--bias 0.05`,
   `--switch-cost 0.8`, fermeture des excursions courtes (`--absorb 120`).
5. Affinage de chaque segment ; avec `--video`, affinage de chaque raccord par comparaison d'images (miniatures
   32×18).
6. **Vérification des segments courts** (< `--verify-len` 150 s) : le décalage du segment est comparé à ceux de ses
   voisins (images, puis audio) ; rattaché au voisin s'il colle mieux.
7. **Fermeture des trous** (`--close-gaps` 6 s) entre segments dont le raccord n'est pas confirmé par l'image :
   coupe au point le plus calme.
8. `--cut ANCIEN=NOUVEAU` (déplacer un raccord), `--stair` (répartir un raccord en plusieurs coupes dans des
   pauses), `--pauses TEMPS` (liste les silences de la source ; `--pauses-offset` = décalage avant le raccord,
   défaut 0 ; `--pauses-need` = durée du saut, marque les pauses assez longues ; sans `--ratio`, ratio supposé
   0.95904), `--video-contrast` (0.15).
9. **Corrections manuelles (version 2.0)**, appliquées après l'analyse, dans cet ordre, chacune écrite dans la
   sortie console : `--shift SEC` (décalage global, négatif = son plus tôt ; remplace un `--shift` précédent),
   `--shift-after TEMPS=SEC` (décalage des segments qui commencent à TEMPS ou après), `--extend-start SEC` /
   `--extend-end SEC` (prolongent le 1er / dernier segment jusqu'au début / à la fin de la ref, même décalage),
   `--start-at SEC` (force le début du 1er segment, à placer dans une pause de la source).
10. Rendu : morceaux ffmpeg (intermédiaires en WAV PCM 16 bits, volumineux) recollés sur la timeline de `--ref`, trous = silence ou audio de la VO avec
   `--fill-ref` ; sortie `.mka` (AAC 192k) ; **mkvmerge lancé à la fin** (`--no-mux`, `--mux-out`, `--mkvmerge`,
   `--lang fra`, `--track-name VF`). Le dossier `--tmp` est créé s'il n'existe pas.

Conventions : décalage `o` d'un segment → temps VO `t` = temps source corrigé `u` + `o` ; temps source réel =
`u × ratio` ; un raccord `oA → oB` supprime `oA − oB` secondes de source (doivent être du silence).
Formats de temps : `1:12:01.36`, `72:01.36` ou `4321.36`.

## 4. Résultat final validé (écoute complète OK)
Commande : `python autosync_audio.py --ref VO.mp4 --src VF.mp4 --video --fill-ref --out VF_sync.mka --tmp F:\tmp`
→ `final.mkv` (VO + piste « VF » en français). Avec la version générique du script, ajouter pour CE film
`--stair "1:11:39.30=0.27,1:11:58.26=0.13,1:11:58.88=0.24,1:12:00.10=reste"` (sinon la coupe de 1:12:01 est
automatique et perd des mots).

Segments (temps VO ← décalage) : 0:00.28→21:17.967 (+0.15) | →30:02.072 (−2.85) | →31:10.010 (−5.15) |
→43:30.637 (−5.90) | 43:31.887→55:17.843 (−8.45) | →1:02:56.880 (−11.66) | →1:12:xx (−14.22) | →1:14:07.475
(−15.09) | →1:27:51.220 (−18.01). Couverture 99,9 %. Sauts : 3.00, 2.30, 0.75, 2.55, 3.21, 2.56, 0.87, 2.92 s.

Trous remplis par l'audio de la VO : 0–0,28 s, 43:30.637–43:31.887 (scène de la VO absente de la VF, 1,25 s),
1:27:51–fin (3 s).

À chaque raccord (21:18, 30:02, 43:30, 55:18, 1:02:57…) la VF a un fondu, la VO une coupe brute : l'audio suit la
coupe brute de la VO, c'est voulu.

## 5. Ce qu'on a appris (historique des problèmes)
- Faux segment −5,15 s entre −5,90 et −8,45 (42:35→43:27) : artefact, supprimé par la vérification des segments
  courts (images : −5,90 → 0.943 contre −5,15 → 0.860). À l'inverse le segment −5,15 s de 30:02→31:07 est réel
  (images 0.911 contre 0.557 / 0.491) : le saut « 3,04 s » de l'original = 2,30 s (30:02) + 0,75 s (31:10).
- Raccord de 1:12:01 (−14,22 → −15,09 = 0,87 s), seul à poser problème. La phrase « Et Nick l'est aujourd'hui.
  Tu as l'esprit dérangé, ils t'ont fait un lavage de cerveau » est dite d'un seul trait (1:11:51 → ~1:11:56) :
  toute coupe unique perd 0,87 s de mots (essais à 1:11:53, 1:11:54.7 et 1:11:51.8 : mots manquants).
  `--pauses 1:11:50` a montré qu'aucune pause ne dure 0,87 s (0,30 / 0,16 / 0,27 / 0,30 s) → saut réparti en
  4 coupes dans ces pauses : `DEFAULT_STAIRS = ["1:11:39.30=0.27,1:11:58.26=0.13,1:11:58.88=0.24,1:12:00.10=reste"]`.
  Validé à l'écoute. Ces valeurs ne valent que pour ce film.
- `DEFAULT_CUTS = []` et (version générique) `DEFAULT_STAIRS = []` : coupes automatiques, rien à neutraliser
  pour un autre film. Avec l'ancienne version du script : `--stair none --cut none`.

## 5 bis. Séries (« Journal intime d'une call girl », Prime) : ce qu'on a appris en plus
VO et VF à 25 i/s : toujours `--ratio 1`. Saisons 1 et 2 terminées, saison 3 : ép. 1 à 8.
- Cas courant : un seul segment à décalage constant faible (de −0,6 à +0,5 s), ou deux segments séparés par un
  **trou à fond noir** de la VO (2 à 4 s, parfois avec musique) que la VF n'a pas : le saut de décalage
  (ex. +0,33 → +3,21 s) est voulu, l'image reste celle de la VO. Trou rempli par du silence (défaut) ou par la
  VO avec `--fill-ref` (choix à l'oreille ; `--fill-ref` remplit aussi le début et la fin).
- La corrélation est faible (~0,06 à 0,25) : normal, seule l'écoute valide.
- Début ou fin non couverts : `--extend-start` / `--extend-end` valent au moins le temps non couvert indiqué par
  l'analyse. Mot coupé au début : `--start-at`.
- Analyse inutilisable (couverture très basse, décalages multiples de ~9,13 s ou aberrants, musique en boucle) :
  `--switch-cost 5 --min-seg 45 --bias 0.01`. **Avant tout, vérifier que les deux fichiers sont les bons** (durées
  de la sortie d'analyse) : un mauvais copier-coller ou renommage a déjà donné une analyse absurde (couverture
  14 %, décalages −10, +25, +37 s) alors que tout était normal avec le bon fichier.
- `--switch-cost 5` ne fusionne pas deux segments aux décalages éloignés (ex. +0,53 et +1,33 s).
- Décalage mesuré à l'oreille : `--shift` ou `--shift-after`. Un bruit net (claquement de porte) repéré sur la VO
  puis sur la VF est plus précis que les lèvres.
- `--mux-out` donne un nom par épisode (sinon `final.mkv` est écrasé). Le rendu refait l'analyse : y remettre les
  mêmes options que pour l'analyse.

## 6. Façon de travailler avec l'utilisateur
- Répondre en français, de façon concise.
- Quand le script change, **redonner le fichier complet** (jamais seulement une commande ou un correctif à
  appliquer à la main) : il a dû le redemander plusieurs fois.
- Il colle la sortie console ; lire d'abord les lignes « coupe confirmée », « peu concluantes », la vérification
  des segments courts, les trous et les segments retenus.
- Pour un défaut d'écoute demander : le temps (min:s), le sens (voix en avance/en retard), l'ampleur, et si le
  défaut est avant ou après le raccord. Ne pas deviner : plusieurs hypothèses ont été fausses.
- L'écoute finale est toujours faite par l'utilisateur ; ne pas la présenter comme faite.
- Répartition des rôles : l'utilisateur lance les commandes, colle la sortie et dit **où ça ne va pas** (temps,
  symptôme). Claude **gère le reste** : diagnostic, choix des options (`--cut`, `--stair`, `--pauses`, réglages
  Viterbi…), modification du script si besoin, et commande prête à copier. Claude ne dit pas « c'est bon » sans
  que l'utilisateur ait écouté, et n'a pas accès aux fichiers vidéo : il ne peut pas lancer le script lui-même.
- Ne pas modifier le script à tout bout de champ : seulement si c'est vraiment nécessaire, en privilégiant les
  options existantes.
- Le « ok » de l'utilisateur = aucun problème à l'écoute ; « semble ok » = à reprendre s'il trouve un défaut.
- Les trois commandes : (1) `--dry-run` = audio seul, rapide, facultatif ; (2) `--video --dry-run` = analyse
  fiable sans rien produire, à lancer en premier ; (3) rendu complet avec `--video --fill-ref --out … --tmp …`
  (refait l'analyse, produit le `.mka` puis `final.mkv`).

## 7. Reprendre sur un autre film (ou un autre épisode)
1. `python autosync_audio.py --ref VO.mp4 --src VF.mp4 --video --dry-run` (ajouter `--ratio 1` si même vitesse),
   puis lire la sortie (durées, ratio, segments, raccords, avertissements). L'utilisateur la colle dans le chat
   avec le script, ce fichier et `README.md`.
2. Vérifier les sauts : insertions plausibles (0,5 à 4 s) et somme ≈ durée VF remise à vitesse − durée VO.
3. Rendu (mkvmerge inclus), puis écoute de chaque raccord (± 10 s), du début et de la fin (dérive).
4. Raccord audio seul qui coupe des mots : `--pauses TEMPS` (avec `--pauses-offset` = décalage avant le raccord,
   `--pauses-need` = durée du saut, `--ratio` = ratio du film), puis `--stair "DEBUT_PAUSE=durée,…,DEBUT_PAUSE=reste"` ; si les pauses ne suffisent pas, garder la coupe
   automatique et accepter un léger retard.

## 8. Pistes non faites
Choix automatique des pauses pour répartir un raccord ; rapport Markdown de fin d'exécution ; affinage 48 kHz des
raccords ; calage des coupes sur les changements de plan ; comparaison automatique avec la piste de l'autre
resynchro (corrélation par tranches de 30 s).
