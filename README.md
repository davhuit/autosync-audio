# autosync_audio.py — recaler une piste audio sur une autre version d'un film

Prend la piste audio d'une version (`--src`, ex. VF télé 25 i/s) et la recale sur la timeline d'une autre
(`--ref`, ex. VO 24 i/s), même si le montage diffère (insertions, scènes coupées, vitesse PAL). Produit une
piste `.mka`, puis un `.mkv` final (VO + nouvelle piste) via mkvmerge.

## Prérequis

- Python 3.9+ : `python -m pip install numpy scipy`
- `ffmpeg` et `ffprobe` : le script les cherche d'abord **dans son propre dossier** (ou `ffmpeg\` / `ffmpeg\bin\`
  à côté de lui), puis dans le PATH (ex. `C:\Apps\ffmpeg\bin`). On peut donc les poser à côté du script.
- `mkvmerge` (MKVToolNix) pour l'assemblage final : le script le cherche **dans son propre dossier**, puis dans
  le PATH, puis dans `Program Files\MKVToolNix`. Sinon : `--no-mux` (pas d'assemblage), ou `--mkvmerge CHEMIN`.

L'archive Windows de la Release contient déjà `ffmpeg.exe`, `ffprobe.exe` et `mkvmerge.exe` : il ne reste
alors que Python et `numpy` / `scipy` à installer.

## Pour un nouveau film : pas à pas

### 0. Préparer

Mets dans un dossier : le script, la référence (`VO.mp4`) et la source (`VF.mp4`). Les noms n'ont pas
d'importance, on les donne en arguments.

### 1. Réglages propres au premier film

Cette version du script est **générique** : `DEFAULT_STAIRS` et `DEFAULT_CUTS` sont vides, rien à neutraliser.
(La valeur validée pour « Meet The Santas » est gardée en commentaire en haut du script.)
Avec l'ancienne version, ajouter `--stair none --cut none`.

### 2. Analyse seule (`--dry-run`)

```
python autosync_audio.py --ref VO.mp4 --src VF.mp4 --video --dry-run --tmp tmp
```

Aucun audio produit. Lis la sortie :

| À regarder | Ce qu'on attend |
|---|---|
| **Ratio de vitesse** | 24/25 = 0.96, 23.976/25 = 0.95904, ou ≈ 1.0 si même vitesse. Sinon forcer avec `--ratio`. |
| **Décalages candidats / segments** | Quelques segments longs, couverture proche de 100 %. |
| **Sauts entre segments** | Insertions plausibles (≈ 0,5 à 4 s). Leur somme ≈ durée de la source remise à vitesse − durée de la référence. |
| **Lignes « coupe confirmée » / « peu concluantes »** | Les raccords « peu concluants » sont à surveiller à l'écoute. |
| **Vérification des segments courts** | Un segment court peut être rattaché à son voisin : c'est normal. |
| **Trous** | Scènes de la ref absentes de la source (rempli par la VO avec `--fill-ref`). |

### 3. Rendu

```
python autosync_audio.py --ref VO.mp4 --src VF.mp4 --video --fill-ref --out VF_sync.mka --tmp tmp
```

Produit `VF_sync.mka`, puis `final.mkv` (VO + piste « VF », langue `fra`).

### 4. Écoute (indispensable)

Le script ne peut pas juger à ta place. Écoute :

- **le début et la fin** (une dérive de vitesse s'y voit le mieux) ;
- **chaque raccord, ± 10 s** (les temps sont dans la sortie console).

### 5. Si un raccord coupe des mots

Le cas typique : la parole est continue autour d'un raccord, donc toute coupe unique perd des mots.

1. Lister les silences autour du raccord :
   ```
   python autosync_audio.py --ref VO.mp4 --src VF.mp4 --pauses 1:11:50 --pauses-offset -14.22 --pauses-need 0.87 --ratio 0.95904
   ```
   `--pauses-offset` = décalage de la source **avant** le raccord (affiché dans la sortie ; défaut 0),
   `--pauses-need` = durée du saut à supprimer (marque les pauses assez longues), `--ratio` = ratio du film
   (sinon 0.95904 est supposé). Les valeurs ci-dessus sont celles du premier film. `--pauses-db` règle le seuil de silence (−30 dB).
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
| `--ratio X` | Force le ratio de vitesse (ex. `0.959041`). |
| `--pitch resample\|keep` | `resample` (défaut) : rend la hauteur d'origine ; `keep` : atempo. |
| `--switch-cost` (0.8) | Plus haut = moins de segments, plus longs. |
| `--min-seg` (6 s) | Durée minimale d'un segment. |
| `--verify-len` (150 s) | Les segments plus courts sont vérifiés contre leurs voisins (0 = off). |
| `--close-gaps` (6 s) | Comble les petits trous non confirmés par l'image (0 = off). |
| `--absorb` (120 s) | Referme les excursions courtes (0 = off). |
| `--codec`, `--bitrate`, `--layout` | Sortie audio (défaut aac 192k). |
| `--lang`, `--track-name` | Langue et nom de la piste dans le `.mkv` (défaut `fra`, `VF`). |
| `--no-mux`, `--mux-out`, `--mkvmerge` | Contrôle de l'étape mkvmerge. |

Formats de temps acceptés : `1:12:01.36`, `72:01.36` ou `4321.36`.

## Dépannage

- **« Aucune correspondance trouvée »** : mauvaise piste (`--ref-track` / `--src-track`) ou ratio faux
  (`--ratio`). Pour une source sans dérive, essaie `--ratio 1`.
- **Trop de segments minuscules** : monte `--switch-cost` ou `--min-seg`.
- **Raccord absent / mal placé** : relis les lignes « peu concluantes », puis écoute ; utilise `--cut`.
- **Fichiers temporaires volumineux** : mets `--tmp` sur un disque avec de la place (le dossier est créé s'il
  n'existe pas).
- **Défaut d'écoute à signaler** : note le temps (min:s), le sens (voix en avance/en retard), l'ampleur, et si
  le défaut est avant ou après le raccord.

## Licences

Les binaires `ffmpeg.exe` et `ffprobe.exe` fournis dans la Release sont des builds non modifiées de gyan.dev (https://www.gyan.dev/ffmpeg/builds/), sous licence GPL v3 (voir `LICENSE-ffmpeg.txt`). Le code source est disponible sur https://ffmpeg.org et dans le dépôt https://github.com/GyanD/codexffmpeg.

Le binaire `mkvmerge.exe` fourni dans la Release est une build non modifiée de MKVToolNix (https://mkvtoolnix.download), sous licence GPL v2 (voir `LICENSE-mkvtoolnix.txt`). Le code source est disponible sur https://gitlab.com/mbunkus/mkvtoolnix.

Le script `autosync_audio.py` lui-même est sous licence MIT (voir `LICENSE`). Cet outil ne distribue aucun contenu vidéo ou audio : il sert à recaler des fichiers que l'on possède.
