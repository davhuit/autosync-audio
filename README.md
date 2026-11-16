# autosync_audio.py — recaler une piste audio sur une autre version d'un film ou d'un épisode

Prend la piste audio d'une version (`--src`, ex. VF télé 25 i/s) et la recale sur la timeline d'une autre
(`--ref`, ex. VO 24 i/s), même si le montage diffère (insertions, scènes coupées, vitesse PAL). Produit une
piste `.mka`, puis un `.mkv` final (image de la ref + nouvelle piste) via mkvmerge.

Version 2.0 : fonctionne aussi bien sur les films que sur les épisodes de séries (même vitesse, début ou fin
non couverts, trous à fond noir, corrélation faible), avec de nouvelles options de correction manuelle
(`--extend-start`, `--extend-end`, `--start-at`, `--shift`, `--shift-after`).

## Le plus simple : passer par Claude (IA)

Tout ce qui suit peut se faire à la main, mais **le plus simple est de le faire avec Claude** (claude.ai) :
tu lances les commandes, tu lui colles **la sortie console de l'analyse**, et il fait le reste : lecture des
segments et des raccords, choix des options, commande de rendu prête à copier. Tu n'as plus qu'à écouter le
résultat et à lui dire **où ça ne va pas** (temps, voix en avance ou en retard, ampleur).

Dans une nouvelle conversation, joins :

1. `autosync_audio.py` (la version à jour) ;
2. `CLAUDE.md` (contexte du projet et façon de travailler) ;
3. `README.md` (ce fichier : toutes les options et les cas fréquents).

Avec ces trois fichiers, Claude te donne lui-même la commande d'analyse (`--dry-run`) adaptée à tes fichiers.
Tu la lances, puis tu lui colles la sortie console (tu peux aussi la joindre d'emblée si tu as déjà fait
l'analyse).

Claude ne peut pas lancer le script ni écouter l'audio à ta place : l'écoute finale reste la tienne.

## Prérequis

- Python 3.9+ : `python -m pip install numpy scipy`
- `ffmpeg` et `ffprobe` : le script les cherche d'abord **dans son propre dossier** (ou `ffmpeg\` / `ffmpeg\bin\`
  à côté de lui), puis dans le PATH (ex. `C:\Apps\ffmpeg\bin`). On peut donc les poser à côté du script.
- `mkvmerge` (MKVToolNix) pour l'assemblage final : le script le cherche **dans son propre dossier**, puis dans
  le PATH, puis dans `Program Files\MKVToolNix`. Sinon : `--no-mux` (pas d'assemblage), ou `--mkvmerge CHEMIN`.

L'archive Windows de la Release contient déjà `ffmpeg.exe`, `ffprobe.exe` et `mkvmerge.exe` : il ne reste
alors que Python et `numpy` / `scipy` à installer.

## Pas à pas (à la main, ou avec Claude)

### 0. Préparer

Mets dans un dossier : le script, la référence (`VO.mp4`) et la source (`VF.mp4`). Les noms n'ont pas
d'importance, on les donne en arguments. **Vérifie que ce sont les bons fichiers** : la sortie de l'analyse
affiche leur durée, compare-la à celle que tu attends (une erreur de copier-coller ou de renommage donne une
analyse absurde : couverture très basse, décalages de plusieurs dizaines de secondes).

### 1. Analyse seule (`--dry-run`)

```
python autosync_audio.py --ref VO.mp4 --src VF.mp4 --video --dry-run --tmp tmp
```

Si les deux fichiers ont la même vitesse (tous deux à 25 i/s, par exemple), ajoute `--ratio 1`.

Aucun audio produit. Lis la sortie (ou colle-la à Claude) :

| À regarder | Ce qu'on attend |
|---|---|
| **Durées** | Celles des bons fichiers. |
| **Ratio de vitesse** | 24/25 = 0.96, 23.976/25 = 0.95904, ou 1.0 si même vitesse. Sinon forcer avec `--ratio`. |
| **Segments retenus / couverture** | Quelques segments longs, couverture proche de 100 %. |
| **Sauts entre segments** | Insertions plausibles (≈ 0,5 à 4 s). Leur somme ≈ durée de la source remise à vitesse − durée de la référence. |
| **Lignes « coupe confirmée » / « peu concluantes »** | Les raccords « peu concluants » sont à surveiller à l'écoute. |
| **Vérification des segments courts** | Un segment court peut être rattaché à son voisin : c'est normal. |
| **Trous** | Scènes de la ref absentes de la source (silence, ou audio de la ref avec `--fill-ref`). |
| **Début et fin** | Heure du premier début et de la dernière fin de segment : sert à régler `--extend-start` et `--extend-end`. |

### 2. Rendu

```
python autosync_audio.py --ref VO.mp4 --src VF.mp4 --video --fill-ref --out VF_sync.mka --tmp tmp
```

Le rendu refait l'analyse : **remets-y les mêmes options que pour l'analyse** (`--ratio`, `--bias`, etc.).
Produit `VF_sync.mka`, puis `final.mkv` (image de la ref + piste « VF », langue `fra`). Pour traiter
plusieurs fichiers de suite, donne un nom différent à chacun avec `--mux-out`, sinon `final.mkv` est écrasé.

### 3. Écoute (indispensable)

Le script ne peut pas juger à ta place. Écoute :

- **le début et la fin** (une dérive de vitesse s'y voit le mieux) ;
- **chaque raccord, ± 10 s** (les temps sont dans la sortie console).

Pour signaler un défaut (à Claude ou pour toi-même), note : le temps (min:s), le sens (voix en avance ou en
retard), l'ampleur, et si le défaut est avant ou après le raccord.

## Cas fréquents et options

### Début ou fin non couverts

La corrélation est faible au début et à la fin (musique, doublage), le script les rogne parfois de quelques
secondes.

- `--extend-start SEC` : si le 1er segment commence avant SEC secondes, le prolonge jusqu'au début de la ref
  (même décalage). Prends au moins le temps non couvert indiqué par l'analyse.
- `--extend-end SEC` : même chose pour la fin (prolonge jusqu'à la fin de la ref, ou de la source si elle
  finit avant).

### Un mot coupé au début

`--start-at SEC` force le début du 1er segment à SEC secondes de la ref (avant : silence, ou audio de la ref
avec `--fill-ref`). À placer dans une pause de la source, pour ne pas garder la fin d'un mot coupé net.
Appliqué après `--extend-start`.

### Trou à fond noir entre deux scènes

Cas fréquent sur les séries : la ref a un fond noir de 2 à 4 s entre deux scènes, la source enchaîne
directement. L'image reste celle de la ref, donc l'audio doit attendre : le saut de décalage est voulu.
Le trou est rempli par du silence (par défaut) ou par l'audio de la ref avec `--fill-ref` (musique de la VO ;
à choisir à l'oreille, et `--fill-ref` remplit aussi le tout début et la fin).

### Analyse inutilisable (corrélation très faible, musique en boucle)

Fausses pistes (décalages multiples d'une même durée, très grands décalages, couverture très basse) :
essaie `--switch-cost 5 --min-seg 45 --bias 0.01`. `--min-seg 45` écarte les faux segments courts,
`--bias 0.01` laisse le début s'accrocher au bon décalage. La corrélation reste basse : seule l'écoute valide.
Si l'analyse reste absurde, vérifie d'abord que les deux fichiers sont les bons.

### Corriger un décalage mesuré à l'oreille

- `--shift SEC` : ajoute SEC secondes au décalage de **tous** les segments (négatif = son plus tôt). Relatif à
  l'analyse d'origine : il remplace un `--shift` précédent, il ne s'y ajoute pas.
- `--shift-after TEMPS=SEC` (répétable) : comme `--shift`, mais seulement pour les segments qui commencent à
  TEMPS ou après. Ex. `--shift-after 10:06.48=-0.20`.

Pour mesurer un décalage, un bruit net (claquement de porte…) repéré sur la ref puis sur la source est plus
précis que les lèvres. Un délai audio réglé dans VLC ou MPC-HC donne aussi la valeur directement.

### Un raccord qui coupe des mots

Le cas typique : la parole est continue autour d'un raccord, donc toute coupe unique perd des mots.

1. Lister les silences autour du raccord :
   ```
   python autosync_audio.py --ref VO.mp4 --src VF.mp4 --pauses 1:11:50 --pauses-offset -14.22 --pauses-need 0.87 --ratio 0.95904
   ```
   `--pauses-offset` = décalage de la source **avant** le raccord (affiché dans la sortie ; défaut 0),
   `--pauses-need` = durée du saut à supprimer (marque les pauses assez longues), `--ratio` = ratio du film
   (sinon 0.95904 est supposé). Les valeurs ci-dessus sont celles du premier film. `--pauses-db` règle le
   seuil de silence (−30 dB).
2. **Déplacer** le raccord dans une vraie pause : `--cut ANCIEN=NOUVEAU` (ex. `--cut 1:12:01=1:11:59.8`).
3. Si aucune pause n'est assez longue, **répartir** le saut en plusieurs petites coupes dans des pauses :
   `--stair "DEBUT_PAUSE=durée,DEBUT_PAUSE=durée,DEBUT_PAUSE=reste"`
   (le dernier élément prend « reste »).
4. Si rien ne marche : garder la coupe automatique et accepter un léger retard.

Convention : un raccord `oA → oB` supprime `oA − oB` secondes de la source (qui doivent être du silence).

## Options utiles

| Option | Rôle |
|---|---|
| `--video` | Affine les raccords en comparant les images (plus lent, plus fiable). |
| `--fill-ref` | Trous remplis avec l'audio de la ref (sinon silence). |
| `--ref-track` / `--src-track` | N° de piste audio (défaut 0). |
| `--ratio X` | Force le ratio de vitesse (ex. `0.959041`, ou `1` si même vitesse). |
| `--pitch resample\|keep` | `resample` (défaut) : rend la hauteur d'origine ; `keep` : atempo. |
| `--bias` (0.05) | Plus bas = le début s'accroche mieux au bon décalage (0.01 pour les séries). |
| `--switch-cost` (0.8) | Plus haut = moins de segments, plus longs. |
| `--min-seg` (6 s) | Durée minimale d'un segment. |
| `--verify-len` (150 s) | Les segments plus courts sont vérifiés contre leurs voisins (0 = off). |
| `--close-gaps` (6 s) | Comble les petits trous non confirmés par l'image (0 = off). |
| `--absorb` (120 s) | Referme les excursions courtes (0 = off). |
| `--extend-start` / `--extend-end` | Prolongent le 1er / dernier segment jusqu'au début / à la fin de la ref. |
| `--start-at` | Force le début du 1er segment (mot coupé). |
| `--shift` / `--shift-after` | Décalage manuel global / à partir d'un temps donné. |
| `--cut` / `--stair` / `--pauses` | Déplacer, répartir ou chercher les pauses d'un raccord. |
| `--codec`, `--bitrate`, `--layout` | Sortie audio (défaut aac 192k). |
| `--lang`, `--track-name` | Langue et nom de la piste dans le `.mkv` (défaut `fra`, `VF`). |
| `--no-mux`, `--mux-out`, `--mkvmerge` | Contrôle de l'étape mkvmerge (`--mux-out` : nom du `.mkv` final). |

Formats de temps acceptés : `1:12:01.36`, `72:01.36` ou `4321.36`.

## Dépannage

- **Couverture très basse, décalages aberrants** : vérifie les fichiers (durées), puis essaie
  `--switch-cost 5 --min-seg 45 --bias 0.01`.
- **« Aucune correspondance trouvée »** : mauvaise piste (`--ref-track` / `--src-track`) ou ratio faux
  (`--ratio`). Pour une source sans dérive, essaie `--ratio 1`.
- **Trop de segments minuscules** : monte `--switch-cost` ou `--min-seg`.
- **Raccord absent / mal placé** : relis les lignes « peu concluantes », puis écoute ; utilise `--cut`.
- **Début ou fin muets** : `--extend-start` / `--extend-end`.
- **Fichiers temporaires volumineux** : mets `--tmp` sur un disque avec de la place (le dossier est créé s'il
  n'existe pas). Les morceaux intermédiaires sont en WAV, ils sont volumineux.
- **Défaut d'écoute à signaler** : note le temps (min:s), le sens (voix en avance/en retard), l'ampleur, et si
  le défaut est avant ou après le raccord, puis donne-les à Claude avec la sortie de l'analyse.

## Licences

Les binaires `ffmpeg.exe` et `ffprobe.exe` fournis dans la Release sont des builds non modifiées de gyan.dev (https://www.gyan.dev/ffmpeg/builds/), sous licence GPL v3 (voir `LICENSE-ffmpeg.txt`). Le code source est disponible sur https://ffmpeg.org et dans le dépôt https://github.com/GyanD/codexffmpeg.

Le binaire `mkvmerge.exe` fourni dans la Release est une build non modifiée de MKVToolNix (https://mkvtoolnix.download), sous licence GPL v2 (voir `LICENSE-mkvtoolnix.txt`). Le code source est disponible sur https://gitlab.com/mbunkus/mkvtoolnix.

Le script `autosync_audio.py` lui-même est sous licence MIT (voir `LICENSE`). Cet outil ne distribue aucun contenu vidéo ou audio : il sert à recaler des fichiers que l'on possède.
