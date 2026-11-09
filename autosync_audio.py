#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
autosync_audio.py - recale la piste audio d'une version (--src) sur la timeline
d'une autre version du même film (--ref), même si le montage est différent
(scènes coupées, vitesse PAL 25 i/s vs 24 i/s, etc.).

Principe :
  1. décodage des deux pistes en mono 4 kHz (ffmpeg) -> enveloppes d'attaque (100 Hz)
  2. choix du ratio de vitesse (ratios classiques), puis correction de la dérive
  3. découverte des décalages par corrélation croisée (FFT) sur des fenêtres de 30 s
  4. affectation de chaque tranche de la source à un décalage (lissage de Viterbi)
  5. affinage des décalages (+/-0,4 s) et des bords de chaque segment
  6. rendu : on recolle les segments sur la timeline de --ref (trous = silence,
     ou audio de --ref avec --fill-ref) et on encode en .mka

Dépendances : python 3.9+, numpy, scipy, ffmpeg + ffprobe (dans le dossier du script, dans un sous-dossier
ffmpeg\\ ou ffmpeg\\bin\\, ou dans le PATH).
Exemple :
  python autosync_audio.py --ref film_vo.mkv --src telefilm_fr.mkv --out piste_fr.mka --fill-ref
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from scipy.signal import fftconvolve


def _find_tool(name):
    """ffmpeg/ffprobe : d'abord dans le dossier du script (ou son sous-dossier ffmpeg), puis dans le PATH."""
    here = os.path.dirname(os.path.abspath(__file__))
    exe = name + (".exe" if os.name == "nt" else "")
    for d in (here, os.path.join(here, "ffmpeg"), os.path.join(here, "ffmpeg", "bin")):
        c = os.path.join(d, exe)
        if os.path.isfile(c):
            return c
    return shutil.which(name) or name


FFMPEG = _find_tool("ffmpeg")
FFPROBE = _find_tool("ffprobe")

SR = 4000        # fréquence de décodage pour l'analyse
ENV_HZ = 100     # résolution de l'enveloppe : 10 ms
TW, TH = 32, 18  # taille des miniatures pour la comparaison vidéo
# Raccords déplacés à la main (utilisés si --cut n'est pas donné). Format ANCIEN=NOUVEAU.
# Vide = coupes trouvées automatiquement. Utiliser --pauses pour trouver une pause réelle où couper.
DEFAULT_CUTS = []

# Raccords répartis en plusieurs petites coupes dans des pauses (voir --stair et --pauses). Format :
# "DEBUT_DE_PAUSE=DUREE_SUPPRIMEE,..." (DEBUT_DE_PAUSE lu dans la sortie de --pauses, un peu après son début) ;
# la dernière durée peut être "reste". Liste vide = coupe automatique unique.
# Version générique : vide. Pour « Meet The Santas » (raccord de 1:12:01, 0,87 s) la valeur validée était :
#   DEFAULT_STAIRS = ["1:11:39.30=0.27,1:11:58.26=0.13,1:11:58.88=0.24,1:12:00.10=reste"]
# (à passer en ligne de commande avec --stair "..." et --pauses-offset -14.22)
DEFAULT_STAIRS = []
CLASSIC = [1.0, 24 / 25, 25 / 24, 23.976 / 25, 25 / 23.976, 23.976 / 24, 24 / 23.976]


def log(*a):
    print(*a, flush=True)


# ----------------------------------------------------------------- ffmpeg ---
def probe(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def audio_stream(info, idx):
    streams = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if idx >= len(streams):
        sys.exit(f"Piste audio {idx} introuvable ({len(streams)} piste(s) dans le fichier).")
    return streams[idx]


def decode(path, track):
    cmd = [FFMPEG, "-v", "error", "-i", path, "-map", f"0:a:{track}", "-vn",
           "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


# -------------------------------------------------------------- signaux -----
def zscore(a):
    return (a - a.mean()) / (a.std() + 1e-9)


def envelope(x):
    """Enveloppe d'attaque : début des sons (hausse du log-RMS), à 100 Hz."""
    step = SR // ENV_HZ
    n = len(x) // step
    f = x[:n * step].reshape(n, step).astype(np.float64)
    lg = np.log(np.sqrt((f ** 2).mean(axis=1)) + 1e-5)
    on = np.minimum(np.maximum(np.diff(lg, prepend=lg[0]), 0), 2.0)
    on = np.convolve(on, np.ones(3) / 3, mode="same")
    return zscore(on)


def stretch(env, f):
    n = int(len(env) * f)
    return np.interp(np.arange(n) / f, np.arange(len(env)), env)


def pearson(a, b):
    a = a - a.mean()
    b = b - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 1e-9 else 0.0


# ------------------------------------------------------------- recherche ----
def search(ref, w, topk=3):
    """Corrélation de Pearson de w contre tous les décalages de ref.
    Retourne [(position_frames, z, corr)] pour les topk pics (z = écart en sigmas)."""
    n = len(w)
    if len(ref) < n:
        return []
    w0 = w - w.mean()
    sw = np.sqrt((w0 ** 2).sum())
    if sw < 1e-9:
        return []
    num = fftconvolve(ref, w0[::-1], mode="valid")
    c1 = np.concatenate(([0.0], np.cumsum(ref)))
    c2 = np.concatenate(([0.0], np.cumsum(ref ** 2)))
    s1 = c1[n:] - c1[:-n]
    s2 = c2[n:] - c2[:-n]
    var = np.maximum(s2 - s1 ** 2 / n, 1e-9)
    corr = num / (sw * np.sqrt(var))
    mu, sd = corr.mean(), corr.std() + 1e-9
    peaks = []
    c = corr.copy()
    for _ in range(topk):
        p = int(np.argmax(c))
        peaks.append((p, (c[p] - mu) / sd, float(c[p])))
        c[max(0, p - ENV_HZ):p + ENV_HZ] = -9
    return peaks


def discover(ref, src, win_s, hop_s, topk, zmin, lo=0, hi=None):
    """Fenêtres glissantes sur src. Retourne [(u0_s, décalage_s, z)]."""
    W, H = int(win_s * ENV_HZ), int(hop_s * ENV_HZ)
    hi = len(src) if hi is None else hi
    hits = []
    for i in range(lo, hi - W + 1, H):
        for p, z, _ in search(ref, src[i:i + W], topk):
            if z >= zmin:
                hits.append((i / ENV_HZ, p / ENV_HZ - i / ENV_HZ, z))
    return hits


def pick_ratio(ref, src0):
    best = None
    for r in CLASSIC:
        hits = discover(ref, stretch(src0, 1 / r), 30, 60, 1, 7.0)
        sc = sum(h[2] for h in hits)
        log(f"  ratio {r:.6f} : score {sc:6.0f} ({len(hits)} fenêtres)")
        if best is None or sc > best[0]:
            best = (sc, r)
    return best[1]


def refine_ratio(ratio, hits):
    """Si le décalage dérive régulièrement dans une même scène, le ratio est un peu faux."""
    chains = []
    for u0, o, z in sorted(hits):
        for ch in chains:
            if 0 < u0 - ch[-1][0] <= 40 and abs(o - ch[-1][1]) < 0.25:
                ch.append((u0, o, z))
                break
        else:
            chains.append([(u0, o, z)])
    slopes = []
    for ch in chains:
        if len(ch) >= 6 and ch[-1][0] - ch[0][0] >= 90:
            t = np.array([h[0] + h[1] for h in ch])
            o = np.array([h[1] for h in ch])
            slopes.append(np.polyfit(t, o, 1)[0])
    if not slopes:
        return None
    m = float(np.median(slopes))
    return ratio * (1 - m) if abs(m) > 2e-5 else None


def cluster(hits, tol=0.15, min_hits=2):
    groups = []
    for o in sorted(h[1] for h in hits):
        if groups and o - groups[-1][-1] <= tol:
            groups[-1].append(o)
        else:
            groups.append([o])
    return [float(np.median(g)) for g in groups if len(g) >= min_hits]


def emissions(src, ref, cands, win, hop, bias):
    """Pour chaque fenêtre de src et chaque décalage candidat : corrélation avec ref, moins un biais.
    Dernière colonne = état « trou » (scène présente seulement dans src), émission nulle."""
    n = len(src)
    starts = np.arange(0, n - win + 1, hop)
    a, b = starts, starts + win

    def wsum(x):
        c = np.concatenate(([0.0], np.cumsum(x)))
        return c[b] - c[a]
    S, SS = wsum(src), wsum(src ** 2)
    E = np.full((len(starts), len(cands) + 1), -1.0)
    E[:, -1] = 0.0
    for k, o in enumerate(cands):
        oi = int(round(o * ENV_HZ))
        lo, hi = max(0, -oi), min(n, len(ref) - oi)
        if hi - lo < win:
            continue
        r = np.zeros(n)
        r[lo:hi] = ref[lo + oi:hi + oi]
        R, RR, SRp = wsum(r), wsum(r ** 2), wsum(src * r)
        cov = SRp / win - (S / win) * (R / win)
        vs = np.maximum(SS / win - (S / win) ** 2, 1e-9)
        vr = np.maximum(RR / win - (R / win) ** 2, 1e-9)
        ok = (a >= lo) & (b <= hi)
        E[ok, k] = (cov / np.sqrt(vs * vr))[ok] - bias
    return starts, E


def viterbi(E, switch):
    """Chemin d'états maximisant la somme des émissions, avec un coût à chaque changement d'état.
    Un décalage reste donc retenu à travers les passages peu corrélés (dialogues)."""
    T, K = E.shape
    score = E[0].copy()
    back = np.zeros((T, K), dtype=np.int32)
    idx = np.arange(K)
    for t in range(1, T):
        bp = int(np.argmax(score))
        jump = score[bp] - switch
        use = jump > score
        back[t] = np.where(use, bp, idx)
        score = np.where(use, jump, score) + E[t]
    labels = np.empty(T, dtype=int)
    labels[-1] = int(np.argmax(score))
    for t in range(T - 1, 0, -1):
        labels[t - 1] = back[t, labels[t]]
    return labels


def runs_from(starts, labels, win, hop, n, gap):
    runs, off = [], (win - hop) // 2
    for u, l in zip(starts, labels):
        l = -1 if l == gap else int(l)
        a, b = int(u) + off, int(u) + off + hop
        if runs and runs[-1][2] == l:
            runs[-1][1] = b
        else:
            runs.append([a, b, l])
    if runs:
        runs[0][0], runs[-1][1] = 0, n
    return runs


def absorb(runs, max_len):
    """Un décalage qui revient à l'identique après une courte excursion (trou ou autre décalage) n'a
    jamais bougé : une vraie coupe change le décalage de façon durable. On referme ces excursions."""
    i = 0
    while i < len(runs):
        r, absorbed = runs[i], False
        if r[2] >= 0:
            for j in range(i + 2, len(runs)):
                if runs[j - 1][1] - r[1] > max_len:
                    break
                if runs[j][2] == r[2]:
                    r[1] = runs[j][1]
                    del runs[i + 1:j + 1]
                    absorbed = True
                    break
        if not absorbed:
            i += 1
    return runs


def refine_offset(src, ref, a, b, oi, span=40):
    best = (-2.0, oi)
    for d in range(-span, span + 1):
        x = a + oi + d
        L = min(b - a, len(ref) - x)           # le dernier segment peut dépasser la fin de ref
        if x < 0 or L < max(200, (b - a) // 2):
            continue
        c = pearson(src[a:a + L], ref[x:x + L])
        if c > best[0]:
            best = (c, oi + d)
    return best[1], best[0]


def refine_edges(src, ref, a, b, oi, corr, margin=300):
    """Cherche où la corrélation locale apparait/disparait autour de a et de b."""
    tau = max(0.5 * corr, 0.02)

    def prod(lo, hi):
        idx = np.arange(lo, hi)
        ok = (idx >= 0) & (idx < len(src)) & (idx + oi >= 0) & (idx + oi < len(ref))
        p = np.zeros(hi - lo)
        p[ok] = src[idx[ok]] * ref[idx[ok] + oi]
        return p - tau
    p = prod(a - margin, a + margin)
    s = a - margin + int(np.argmax(np.cumsum(p[::-1])[::-1]))
    q = prod(b - margin, b + margin)
    e = b - margin + int(np.argmax(np.cumsum(q))) + 1
    return s, e


# ----------------------------------------------------------- vidéo -------
def thumbs(path, t0, dur, fps):
    """Miniatures 32x18 en niveaux de gris, à `fps` images/s à partir de t0 : tableau (n, 576)."""
    cmd = [FFMPEG, "-v", "error", "-ss", f"{t0:.4f}", "-t", f"{dur:.4f}", "-i", path, "-an", "-sn",
           "-vf", f"fps={fps:.6f},scale={TW}:{TH}:flags=area,format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.uint8)
    n = len(a) // (TW * TH)
    return a[:n * TW * TH].reshape(n, TW * TH).astype(np.float32)


def frame_sim(R, S):
    """Corrélation image par image (1 = même image, 0 = sans rapport)."""
    n = min(len(R), len(S))
    if n == 0:
        return np.zeros(0)
    R = R[:n] - R[:n].mean(axis=1, keepdims=True)
    S = S[:n] - S[:n].mean(axis=1, keepdims=True)
    d = np.linalg.norm(R, axis=1) * np.linalg.norm(S, axis=1)
    return np.where(d > 1e-3, (R * S).sum(axis=1) / np.maximum(d, 1e-9), 0.0)


def sim_curves(args, w0, dur, fps, A, B, ratio):
    """Similarité image par image entre ref et src, avec le décalage de A puis celui de B."""
    R = thumbs(args.ref, w0, dur, fps)
    out = []
    for seg in (A, B):
        S = thumbs(args.src, (w0 - seg["o"]) * ratio, dur * ratio, fps / ratio)
        out.append(frame_sim(R, S))
    n = min(len(out[0]), len(out[1]))
    return out[0][:n], out[1][:n]


def refine_cut(args, A, B, ratio, margin=90.0, F=4.0, F2=24.0):
    """Cherche sur les images où le décalage A s'arrête (xA) et où B commence (xB).
    Retourne (xA, xB) ou None si les images ne tranchent pas."""
    lo = max(A["t1"] - margin, A["t0"], A["o"], B["o"], 0.0)
    hi = min(B["t0"] + margin, B["t1"])
    log(f"  zone {fmt(lo)} -> {fmt(hi)} ({hi - lo:.0f} s) : comparaison des images...")
    if hi - lo < 30:
        return None
    sA, sB = sim_curves(args, lo, hi - lo, F, A, B, ratio)
    m = int(10 * F)
    if len(sA) < 4 * m:
        log("    pas assez d'images (vidéo absente ?), raccord audio conservé")
        return None
    mA, nA = np.median(sA[:m]), np.median(sA[-m:])      # A doit coller au début, plus à la fin
    mB, nB = np.median(sB[-m:]), np.median(sB[:m])      # B l'inverse
    if mA - nA < args.video_contrast or mB - nB < args.video_contrast:
        log(f"    images peu concluantes (contraste A {mA - nA:.2f}, B {mB - nB:.2f}), raccord audio conservé")
        return None
    tA, tB = (mA + nA) / 2, (mB + nB) / 2
    xA = lo + int(np.argmax(np.concatenate(([0.0], np.cumsum(sA - tA))))) / F
    xB = lo + int(np.argmax(np.concatenate((np.cumsum((sB - tB)[::-1])[::-1], [0.0])))) / F

    def fine_A(x):                                       # image près : dernière image qui colle à A
        w0 = max(x - 3, lo)
        a, _ = sim_curves(args, w0, 6.0, F2, A, B, ratio)
        return w0 + int(np.argmax(np.concatenate(([0.0], np.cumsum(a - tA))))) / F2 if len(a) else x

    def fine_B(x):                                       # première image qui colle à B
        w0 = max(x - 3, lo)
        _, b = sim_curves(args, w0, 6.0, F2, A, B, ratio)
        return w0 + int(np.argmax(np.concatenate((np.cumsum((b - tB)[::-1])[::-1], [0.0])))) / F2 if len(b) else x
    xA, xB = fine_A(xA), fine_B(xB)
    if abs(xA - xB) <= 0.5:
        xA = xB = (xA + xB) / 2
    return xA, xB


# ------------------------------------------------- validation des segments ---
def seg_audio_score(src, ref, s, o, trim=3.0):
    """Corrélation d'enveloppes sur l'intérieur du segment s si on lui applique le décalage o."""
    a, b = int((s["u0"] + trim) * ENV_HZ), int((s["u1"] - trim) * ENV_HZ)
    oi = int(round(o * ENV_HZ))
    a, b = max(a, 0, -oi), min(b, len(src), len(ref) - oi)
    if b - a < 5 * ENV_HZ:
        return None
    return pearson(src[a:b], ref[a + oi:b + oi])


def seg_video_score(args, s, o, ratio, trim=4.0, F=2.0):
    """Similarité d'images médiane sur l'intérieur du segment s avec le décalage o."""
    t0, t1 = s["t0"] + trim, s["t1"] - trim
    if t1 - t0 < 8:
        return None
    R = thumbs(args.ref, t0, t1 - t0, F)
    S = thumbs(args.src, max(t0 - o, 0.0) * ratio, (t1 - t0) * ratio, F / ratio)
    sim = frame_sim(R, S)
    return float(np.median(sim)) if len(sim) >= 8 else None


def verify_segments(args, segs, src, ref, ratio):
    """Un segment court a-t-il vraiment son propre décalage, ou celui d'un voisin ?
    On compare, sur ce segment, son décalage à ceux des deux voisins (audio, puis images si --video).
    Si un voisin colle mieux, le segment lui est rattaché (les raccords sont ensuite réaffinés)."""
    changed, done = True, set()
    while changed:
        changed = False
        for i, s in enumerate(segs):
            if s["t1"] - s["t0"] >= args.verify_len:
                continue
            key = (round(s["t0"], 1), round(s["o"], 2))
            if key in done:
                continue
            nb = [segs[j]["o"] for j in (i - 1, i + 1) if 0 <= j < len(segs)]
            offs = [s["o"]] + [o for o in nb if abs(o - s["o"]) > 0.05]
            if len(offs) < 2:
                continue
            log(f"  segment {fmt(s['t0'])} -> {fmt(s['t1'])} (décalage {s['o']:+.2f} s) : test contre "
                + ", ".join(f"{o:+.2f}" for o in offs[1:]))
            done.add(key)
            best = None
            for name, fn, margin in (
                    ("images", (lambda o: seg_video_score(args, s, o, ratio)) if args.video else None, 0.05),
                    ("audio", lambda o: seg_audio_score(src, ref, s, o), args.verify_margin)):
                if fn is None:
                    continue
                sc = [fn(o) for o in offs]
                if any(x is None for x in sc):
                    continue
                log(f"    {name} : " + " | ".join(f"{o:+.2f} -> {x:.3f}" for o, x in zip(offs, sc)))
                k = int(np.argmax(sc))
                if k > 0 and sc[k] - sc[0] > margin:
                    best = offs[k]
                    break
                if k == 0 and sc[0] - max(sc[1:]) > margin:
                    break                                   # son propre décalage est nettement confirmé
            if best is None:
                continue
            log(f"    -> décalage {best:+.2f} s retenu (le segment est rattaché à son voisin)")
            s["o"] = best
            s["u0"], s["u1"] = s["t0"] - best, s["t1"] - best
            merged = [segs[0]]
            for x in segs[1:]:
                m = merged[-1]
                if abs(m["o"] - x["o"]) < 0.05:
                    m["t1"] = max(m["t1"], x["t1"])
                    m["u1"] = m["t1"] - m["o"]
                    m["corr"] = max(m["corr"], x["corr"])
                else:
                    merged.append(x)
            segs[:] = merged
            changed = True
            break
    return segs


def close_gaps(segs, src, max_gap):
    """Raccord non confirmé par l'image : le trou entre deux segments (bords d'affinage prudents) est
    comblé en coupant au point le plus calme (peu d'attaques sonores des deux côtés)."""
    for A, B in zip(segs, segs[1:]):
        g = B["t0"] - A["t1"]
        if A.get("vc") or not (0.05 < g <= max_gap):
            continue
        xs = np.arange(A["t1"], B["t0"] + 1e-6, 0.05)

        def cost(x):
            c = 0.0
            for o in (A["o"], B["o"]):
                i = int(round((x - o) * ENV_HZ))
                w = src[max(i - 15, 0):max(i + 15, 0)]
                c += float(np.abs(w).mean()) if len(w) else 9.0
            return c
        x = float(xs[int(np.argmin([cost(v) for v in xs]))])
        log(f"  trou de {g:.2f} s comblé : coupe à {fmt(x)} ({A['o']:+.2f} -> {B['o']:+.2f} s)")
        A["t1"] = B["t0"] = x
    return segs


def parse_t(txt):
    """'1:12:00.5', '72:00.5' ou '4320.5' -> secondes."""
    t = 0.0
    for part in txt.strip().split(":"):
        t = t * 60 + float(part)
    return t


def apply_cuts(segs, specs):
    """--cut ANCIEN=NOUVEAU : déplace le raccord le plus proche de ANCIEN (à 3 s près) en NOUVEAU."""
    for spec in specs:
        try:
            old, new = (parse_t(x) for x in spec.split("="))
        except ValueError:
            sys.exit(f"--cut : format attendu 1:12:01=1:11:59.8 (reçu : {spec})")
        pairs = [(min(abs(A["t1"] - old), abs(B["t0"] - old)), A, B) for A, B in zip(segs, segs[1:])]
        d, A, B = min(pairs, key=lambda x: x[0]) if pairs else (99, None, None)
        if d > 3.0:
            log(f"  --cut {spec} : aucun raccord à moins de 3 s de {fmt(old)}, ignoré")
            continue
        if not (A["t0"] + 1 < new < B["t1"] - 1):
            log(f"  --cut {spec} : position hors des segments voisins, ignoré")
            continue
        log(f"  raccord {A['o']:+.2f} -> {B['o']:+.2f} s déplacé de {fmt(A['t1'])} à {fmt(new)}")
        A["t1"] = B["t0"] = new
        A["vc"] = True
    return segs


def apply_stair(segs, spec):
    """Répartit un raccord en plusieurs petites coupes, chacune dans une pause de la source.
    spec = "P1=d1,P2=d2,...,Pn=reste" : en P_i (début d'une pause, repère de l'ancien décalage), on supprime
    d_i secondes de source ; la somme des d_i vaut le saut total du raccord."""
    items = []
    for it in spec.split(","):
        p, d = it.split("=")
        items.append((parse_t(p), None if d.strip().lower() == "reste" else float(d)))
    if not items:
        return segs
    ref_t = items[-1][0]
    pairs = [(abs(A["t1"] - ref_t), i) for i, A in enumerate(segs[:-1])]
    dist, i = min(pairs) if pairs else (99, -1)
    if dist > 30:
        log(f"  --stair : aucun raccord à moins de 30 s de {fmt(ref_t)}, ignoré")
        return segs
    A, B = segs[i], segs[i + 1]
    total = A["o"] - B["o"]
    if total <= 0:
        log("  --stair : ce raccord ne supprime pas de source, ignoré")
        return segs
    new, c, cur = [], 0.0, dict(A)
    for k, (p, d) in enumerate(items):
        if d is None or k == len(items) - 1:
            d = total - c
        T = p - c                                           # temps de --ref de la coupe
        if d <= 0 or T <= cur["t0"] + 0.05 or T >= B["t1"] - 1:
            log("  --stair : valeurs incohérentes, raccord laissé tel quel")
            return segs
        cur["t1"] = T
        new.append(cur)
        log(f"  coupe {k + 1}/{len(items)} à {fmt(T)} : {d:.2f} s de source supprimés (pause vers {fmt(p)})")
        c += d
        cur = dict(A)
        cur.update(t0=T, o=A["o"] - c, vc=True)
    cur["o"], cur["t1"] = B["o"], B["t1"]
    new.append(cur)
    segs[i:i + 2] = new
    return segs


# ----------------------------------------------------------------- rendu ----
def layout_for(stream, forced):
    if forced:
        return forced
    return {1: "mono", 2: "stereo", 6: "5.1"}.get(int(stream.get("channels", 2)), "stereo")


def ffrun(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit("Erreur ffmpeg :\n" + r.stderr[-1500:])


def render(args, segs, ratio, ref_dur, src_stream, lay):
    if args.tmp:
        os.makedirs(args.tmp, exist_ok=True)            # crée le dossier s'il n'existe pas
    tmp = tempfile.mkdtemp(prefix="autosync_", dir=args.tmp)
    pieces, cur = [], 0.0
    for s in segs:
        if s["t0"] - cur > 0.01:
            pieces.append(("gap", cur, s["t0"], 0.0))
        pieces.append(("src", s["t0"], s["t1"], s["o"]))
        cur = s["t1"]
    if ref_dur - cur > 0.01:
        pieces.append(("gap", cur, ref_dur, 0.0))

    sr = int(src_stream.get("sample_rate", 48000))
    speed = f"asetrate={sr * ratio:.4f}" if args.pitch == "resample" else f"atempo={ratio:.6f}"
    common = f"aresample=48000,aformat=sample_fmts=s16:channel_layouts={lay}"
    files = []
    for k, (kind, t0, t1, o) in enumerate(pieces):
        D = t1 - t0
        out = os.path.join(tmp, f"p{k:03d}.flac")
        tail = f"apad=whole_dur={D:.6f},atrim=duration={D:.6f}"
        if kind == "src":
            s0, s1 = (t0 - o) * ratio, (t1 - o) * ratio
            ffrun([FFMPEG, "-v", "error", "-y", "-ss", f"{s0:.4f}", "-t", f"{s1 - s0:.4f}", "-i", args.src,
                   "-map", f"0:a:{args.src_track}", "-vn",
                   "-af", f"{speed},{common},{tail}", "-c:a", "flac", out])
            what = "source"
        elif args.fill_ref:
            ffrun([FFMPEG, "-v", "error", "-y", "-ss", f"{t0:.4f}", "-t", f"{D:.4f}", "-i", args.ref,
                   "-map", f"0:a:{args.ref_track}", "-vn",
                   "-af", f"{common},{tail}", "-c:a", "flac", out])
            what = "audio de --ref"
        else:
            ffrun([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r=48000:cl={lay}",
                   "-t", f"{D:.6f}", "-af", common, "-c:a", "flac", out])
            what = "silence"
        files.append(out)
        log(f"  morceau {k + 1}/{len(pieces)} : {fmt(t0)} -> {fmt(t1)} ({what})")

    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in files:
            f.write("file '" + p.replace("\\", "/").replace("'", r"'\''") + "'\n")
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c:a", args.codec]
    if args.codec != "flac":
        cmd += ["-b:a", args.bitrate]
    ffrun(cmd + [args.out])
    shutil.rmtree(tmp, ignore_errors=True)


def fmt(t):
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:06.3f}"


def find_mkvmerge(forced):
    if forced:
        return forced
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("mkvmerge.exe", "mkvmerge"):
        c = os.path.join(here, name)
        if os.path.isfile(c):
            return c
    exe = shutil.which("mkvmerge")
    if exe:
        return exe
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        c = os.path.join(base, "MKVToolNix", "mkvmerge.exe")
        if os.path.exists(c):
            return c
    return None


def mux(args):
    """Ajoute la piste synchronisée au fichier --ref avec mkvmerge."""
    exe = find_mkvmerge(args.mkvmerge)
    if not exe:
        log("mkvmerge introuvable (installer MKVToolNix ou donner --mkvmerge CHEMIN) ; commande à lancer à la main :")
        log(f'  mkvmerge -o "{args.mux_out}" "{args.ref}" --language 0:{args.lang} --track-name 0:"{args.track_name}" "{args.out}"')
        return
    cmd = [exe, "-o", args.mux_out, args.ref, "--language", f"0:{args.lang}",
           "--track-name", f"0:{args.track_name}", args.out]
    log(f"\nMuxage -> {args.mux_out}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode >= 2:
        log(r.stdout.strip() or r.stderr.strip())
        sys.exit("mkvmerge a échoué.")
    log(f"Terminé : {args.mux_out} (VO + piste {args.track_name})")


def list_pauses(args):
    """Affiche les pauses (silences) de la source autour d'un instant de la timeline de --ref, avec le
    décalage donné : c'est là, et seulement là, qu'un raccord peut tomber sans perdre de mots."""
    import re
    T, o, ratio = parse_t(args.pauses), args.pauses_offset, args.ratio or 0.95904  # donner --ratio pour un autre film
    real0 = max((T - 20 - o) * ratio, 0.0)
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-ss", f"{real0:.3f}", "-t", f"{40 * ratio:.3f}", "-i", args.src,
           "-map", f"0:a:{args.src_track}", "-vn",
           "-af", f"silencedetect=noise={args.pauses_db}dB:d=0.15", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = re.findall(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", err)
    log(f"Pauses de la source (< {args.pauses_db} dB, >= 0,15 s) vers {fmt(T)}, décalage {o:+.2f} s, "
        f"timeline de --ref :")
    if not ends:
        log("  aucune pause trouvée (essayer --pauses-db -25)")
        return
    for st, (en, du) in zip(starts, ends):
        a_, b_ = (real0 + st) / ratio + o, (real0 + float(en)) / ratio + o
        flag = f"  <- assez longue pour un raccord de {args.pauses_need:g} s" if args.pauses_need > 0 and float(du) / ratio >= args.pauses_need else ""
        log(f"  {fmt(a_)} -> {fmt(b_)}  ({float(du) / ratio:.2f} s){flag}")


# ------------------------------------------------------------------ main ----
def main():
    ap = argparse.ArgumentParser(description="Recale une piste audio sur la timeline d'une autre version.")
    ap.add_argument("--ref", required=True, help="fichier de référence (sa timeline est conservée)")
    ap.add_argument("--src", required=True, help="fichier dont on récupère la piste audio")
    ap.add_argument("--ref-track", type=int, default=0, help="n° de piste audio dans --ref (défaut 0)")
    ap.add_argument("--src-track", type=int, default=0, help="n° de piste audio dans --src (défaut 0)")
    ap.add_argument("--out", help="fichier de sortie .mka (défaut : <src>_sync.mka)")
    ap.add_argument("--ratio", type=float, help="force le ratio de vitesse (ex. 0.959041)")
    ap.add_argument("--fill-ref", action="store_true",
                    help="les trous (scènes absentes de --src) sont remplis avec l'audio de --ref, sinon silence")
    ap.add_argument("--video", action="store_true",
                    help="affine chaque raccord en comparant les images des deux vidéos (plus lent)")
    ap.add_argument("--pitch", choices=["resample", "keep"], default="resample",
                    help="resample : rétablit la hauteur d'origine (accéléré PAL) ; keep : atempo")
    ap.add_argument("--bias", type=float, default=0.05,
                    help="corrélation en dessous de laquelle une fenêtre compte contre un décalage (défaut 0.05)")
    ap.add_argument("--switch-cost", type=float, default=0.8,
                    help="coût d'un changement de décalage : plus haut = segments plus longs/moins nombreux")
    ap.add_argument("--absorb", type=float, default=120.0,
                    help="durée max (s) d'une excursion refermée quand le même décalage revient ensuite (0 = désactivé)")
    ap.add_argument("--min-seg", type=float, default=6.0, help="durée minimale d'un segment (s)")
    ap.add_argument("--verify-len", type=float, default=150.0,
                    help="un segment plus court (s) est vérifié contre les décalages de ses voisins (0 = désactivé)")
    ap.add_argument("--verify-margin", type=float, default=0.0,
                    help="avance minimale (corrélation audio) pour qu'un voisin remplace le décalage d'un segment")
    ap.add_argument("--video-contrast", type=float, default=0.15,
                    help="contraste minimal des images pour valider un raccord (défaut 0.15)")
    ap.add_argument("--close-gaps", type=float, default=6.0,
                    help="comble les trous (s) entre segments dont le raccord n'est pas confirmé par l'image (0 = non)")
    ap.add_argument("--cut", action="append", default=None, metavar="ANCIEN=NOUVEAU",
                    help="déplace un raccord (répétable), ex. --cut 1:12:01=1:11:59.8 ; "
                         "remplace DEFAULT_CUTS du script ; --cut none = aucun déplacement")
    ap.add_argument("--pauses", metavar="TEMPS", help="liste les pauses de la source autour de TEMPS (ex. 1:11:50) et s'arrête")
    ap.add_argument("--pauses-offset", type=float, default=0.0,
                    help="décalage (s) de la source juste avant le raccord, lu dans la sortie du script (défaut 0)")
    ap.add_argument("--pauses-need", type=float, default=0.0,
                    help="durée (s) du saut à supprimer : marque les pauses assez longues pour l'absorber (défaut 0 = pas de marque)")
    ap.add_argument("--pauses-db", type=float, default=-30.0, help="seuil de silence de --pauses en dB (défaut -30)")
    ap.add_argument("--stair", action="append", default=None, metavar="P=d,P=d,...",
                    help="répartit un raccord en plusieurs coupes dans des pauses (voir DEFAULT_STAIRS) ; --stair none = désactivé")
    ap.add_argument("--no-mux", action="store_true", help="ne pas lancer mkvmerge à la fin")
    ap.add_argument("--mux-out", default="final.mkv", help="fichier produit par mkvmerge (défaut final.mkv)")
    ap.add_argument("--mkvmerge", help="chemin de mkvmerge si introuvable automatiquement")
    ap.add_argument("--lang", default="fra", help="langue de la piste ajoutée (défaut fra)")
    ap.add_argument("--track-name", default="VF", help="nom de la piste ajoutée (défaut VF)")
    ap.add_argument("--codec", default="aac", help="codec de sortie (aac, ac3, eac3, flac...)")
    ap.add_argument("--bitrate", default="192k")
    ap.add_argument("--layout", help="forcer la disposition des canaux (stereo, 5.1...)")
    ap.add_argument("--tmp", help="dossier temporaire (les morceaux intermédiaires sont volumineux)")
    ap.add_argument("--dry-run", action="store_true", help="analyse seulement, sans produire d'audio")
    args = ap.parse_args()
    if args.pauses:
        list_pauses(args)
        return
    if not args.out:
        args.out = os.path.splitext(os.path.basename(args.src))[0] + "_sync.mka"

    log("== Autosync audio ==")
    ref_info, src_info = probe(args.ref), probe(args.src)
    src_stream = audio_stream(src_info, args.src_track)
    audio_stream(ref_info, args.ref_track)
    ref_dur = float(ref_info["format"]["duration"])
    src_dur = float(src_info["format"]["duration"])

    log("Décodage 4000 Hz des deux fichiers...")
    ref = envelope(decode(args.ref, args.ref_track))
    src0 = envelope(decode(args.src, args.src_track))
    log(f"Durées : ref {fmt(ref_dur)}, src {fmt(src_dur)} (rapport {src_dur / ref_dur:.5f})")

    if args.ratio:
        ratio = args.ratio
        log(f"Ratio de vitesse imposé : {ratio:.6f}")
    else:
        log("Test des ratios de vitesse classiques...")
        ratio = pick_ratio(ref, src0)
        log(f"Ratio retenu : {ratio:.6f}")
    src = stretch(src0, 1 / ratio)

    log("Découverte des décalages...")
    hits = discover(ref, src, 30, 15, 3, 7.0)
    if not args.ratio:
        new = refine_ratio(ratio, hits)
        if new:
            ratio = new
            log(f"Dérive détectée : ratio corrigé {ratio:.6f}, nouvelle passe...")
            src = stretch(src0, 1 / ratio)
            hits = discover(ref, src, 30, 15, 3, 7.0)
    cands = cluster(hits, min_hits=2)
    log(f"Décalages candidats : {len(cands)}")
    if not cands:
        sys.exit("Aucune correspondance trouvée : vérifier les pistes (--ref-track / --src-track) ou --ratio.")

    win, hop = 10 * ENV_HZ, 2 * ENV_HZ
    min_len = int(args.min_seg * ENV_HZ)
    for p in range(1, 5):
        starts, E = emissions(src, ref, cands, win, hop, args.bias)
        labels = viterbi(E, args.switch_cost)
        runs = runs_from(starts, labels, win, hop, len(src), len(cands))
        if p == 4:
            break
        found = []
        for a, b, l in runs:
            if l == -1 and b - a >= 8 * ENV_HZ:
                found += discover(ref, src, 8, 4, 2, 8.0, lo=a, hi=b)
        new = [o for o in cluster(found, min_hits=2) if all(abs(o - c) > 0.15 for c in cands)]
        if not new:
            break
        cands += new
        log(f"  Passe {p + 1} : {len(new)} décalage(s) supplémentaire(s) trouvé(s)")

    if args.absorb > 0:
        runs = absorb(runs, int(args.absorb * ENV_HZ))
    log("Affinage des raccords...")
    segs = []
    for a, b, l in runs:
        if l < 0 or b - a < min_len:
            continue
        oi, c = refine_offset(src, ref, a, b, int(round(cands[l] * ENV_HZ)))
        s, e = refine_edges(src, ref, a, b, oi, c)
        segs.append({"u0": s / ENV_HZ, "u1": e / ENV_HZ, "o": oi / ENV_HZ, "corr": c,
                     "t0": (s + oi) / ENV_HZ, "t1": (e + oi) / ENV_HZ})
    segs.sort(key=lambda s: s["t0"])
    clipped, prev = [], 0.0
    for s in segs:
        s["t0"] = max(s["t0"], prev, s["o"])                       # pas de chevauchement, pas de temps source < 0
        s["t1"] = min(s["t1"], ref_dur, len(src) / ENV_HZ + s["o"])
        if s["t1"] - s["t0"] >= 1.0:
            clipped.append(s)
            prev = s["t1"]
    segs = clipped
    if not segs:
        sys.exit("Aucun segment exploitable (essayer --bias plus bas ou --switch-cost plus bas).")

    if args.verify_len > 0 and len(segs) > 1:
        log("\nVérification des segments courts...")
        segs = verify_segments(args, segs, src, ref, ratio)

    if args.video:
        log("\nComparaison vidéo autour des raccords...")
        for A, B in zip(segs, segs[1:]):
            res = refine_cut(args, A, B, ratio)
            if not res:
                continue
            xA, xB = res
            if not (A["t0"] + 1 < xA and xB < B["t1"] - 1 and xA <= xB):
                log("    résultat incohérent, raccord audio conservé")
                continue
            A["t1"], B["t0"] = xA, xB
            A["vc"] = True
            if xB - xA > 0.1:
                log(f"    coupe confirmée par l'image : scène de --ref absente de --src de {fmt(xA)} à {fmt(xB)}")
            else:
                log(f"    coupe confirmée par l'image à {fmt(xA)} (décalage {A['o']:+.2f} -> {B['o']:+.2f} s)")

    if args.close_gaps > 0:
        log("\nTrous entre segments non confirmés par l'image...")
        close_gaps(segs, src, args.close_gaps)

    cuts = DEFAULT_CUTS if args.cut is None else [c for c in args.cut if c.lower() != "none"]
    if cuts:
        log("\nRaccords déplacés à la main (--cut)...")
        apply_cuts(segs, cuts)

    stairs = DEFAULT_STAIRS if args.stair is None else [x for x in args.stair if x.lower() != "none"]
    if stairs:
        log("\nRaccords répartis dans les pauses (--stair)...")
        for spec in stairs:
            apply_stair(segs, spec)

    log("\nSegments retenus (temps ref <- temps src corrigé de la vitesse) :")
    covered = 0.0
    for s in segs:
        covered += s["t1"] - s["t0"]
        log(f"  ref {fmt(s['t0'])} -> {fmt(s['t1'])}  | décalage {s['o']:+8.2f} s | corr {s['corr']:.3f}")
    log(f"Couverture : {covered / ref_dur * 100:.1f} % de la durée de --ref")
    if args.dry_run:
        return

    lay = layout_for(src_stream, args.layout)
    log(f"\nRendu ({lay}, {args.codec}) -> {args.out}")
    render(args, segs, ratio, ref_dur, src_stream, lay)
    if args.no_mux:
        log("\nTerminé. Pour l'ajouter au MKV de référence, par exemple :")
        log(f'  mkvmerge -o final.mkv "{args.ref}" --language 0:{args.lang} --track-name 0:"{args.track_name}" "{args.out}"')
    else:
        mux(args)

if __name__ == "__main__":
    main()
