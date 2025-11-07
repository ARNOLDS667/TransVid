# TransVid

TransVid est une application Flask simple pour télécharger une vidéo YouTube, transcrire l'audio (Whisper), traduire le texte, générer une voix française (gTTS), et créer une vidéo doublée finale avec FFmpeg.

## Fonctionnalités actuelles

- Télécharger une vidéo YouTube en utilisant `yt-dlp` (optionnal `aria2c` pour accélérer)
- Transcription via Whisper (`tiny` pour les courts, `base` pour les longs)
- Traduction via `deep-translator` (GoogleTranslator)
- Synthèse vocale en français via `gTTS`
- Fusion audio/vidéo via `ffmpeg` (appel subprocess)
- Génération automatique de sous-titres (.srt)
- Options UI :
  - Sélecteur de qualité : `best`, `medium` (<=720p), `low` (<=360p)
  - Option `Utiliser aria2` pour accélérer le téléchargement si `aria2c` est installé
  - Option `Supprimer la vidéo source` pour garder uniquement la vidéo finale
  - Bouton pour purger manuellement les fichiers temporaires

## Comment l'utiliser

1. Installez les dépendances Python (préférer un environnement virtuel):

```powershell
C:/Users/you/AppData/Local/Programs/Python/Python313/python.exe -m pip install -r requirements.txt
```

Si vous n'avez pas encore `requirements.txt`, installez manuellement:

```powershell
pip install flask flask-socketio yt-dlp openai-whisper deep-translator gTTS ffmpeg-python pysrt eventlet
```

2. Installez FFmpeg sur Windows et ajoutez-le au PATH.
3. (Optionnel) Installez `aria2` et `aria2c` pour accélérer les téléchargements:

```powershell
choco install aria2
```

4. Lancez l'application:

```powershell
C:/Users/you/AppData/Local/Programs/Python/Python313/python.exe app.py
```

5. Ouvrez `http://127.0.0.1:5000` dans votre navigateur.

## Changements récents (log)

- Migré de `pytube` vers `yt-dlp` pour des téléchargements plus robustes.
- Ajout d'un sélecteur de qualité dans l'UI et prise en charge d'`aria2`.
- Option pour supprimer automatiquement la vidéo source et fichiers temporaires après génération.
- Ajout d'un endpoint `/purge_temp` pour purger manuellement les fichiers temporaires.
- Remplacement de ffmpeg-python par appels `subprocess` pour la fusion audio/vidéo.
- Fix de compatibilité numpy/numba pour Whisper sur Windows.

## Améliorations prévues

- [ ] Choix du genre de la voix (féminine/masculine) pour le doublage
  - Utilisation de gTTS avec différents domaines régionaux (.fr, .ca, .be) pour varier les voix
  - Possibilité d'intégrer pyttsx3 (voix système Windows) ou espeak (multi-voix)
  - Tests comparatifs des différentes voix gratuites disponibles
- [ ] Ajouter support pour choisir la langue cible (pas seulement français)
- [ ] Interface pour visualiser/éditer les sous-titres avant génération
- [ ] Ajouter endpoints API pour intégration externe
- [ ] Ajouter tests unitaires et CI

## Notes techniques et limites

- Whisper (openai-whisper) peut être lent et gourmand en CPU; pour de meilleures performances envisagez d'utiliser des modèles quantifiés ou des services cloud.
- `gTTS` utilise l'API Google Text-to-Speech non-officielle; pour plus de variété dans les voix :
  - Utiliser différents domaines régionaux (.fr, .ca, .be) qui ont des voix différentes
  - Alternative : pyttsx3 qui utilise les voix système Windows (SAPI5)
  - Alternative : espeak-ng qui offre plusieurs voix mais qualité plus synthétique
- `aria2c` accélère les téléchargements en multipliant les connexions mais nécessite l'installation de l'outil.
- Les noms de fichier sont dérivés du titre YouTube et nettoyés, mais des collisions rares peuvent arriver.

## Contribution

Si vous voulez ajouter des fonctionnalités, créez une branche, mettez à jour la TODO dans ce README et ouvrez une PR.
