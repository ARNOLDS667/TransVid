from flask import Flask, render_template, request, send_from_directory, abort
from flask_socketio import SocketIO
import os, time, subprocess
import yt_dlp
import whisper
import sys
from cipher import get_initial_function_name
from pytube.cipher import get_initial_function_name as _get_initial_function_name
sys.modules['pytube.cipher'].get_initial_function_name = get_initial_function_name
from deep_translator import GoogleTranslator
from gtts import gTTS
import ffmpeg
import pysrt

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

# Création des répertoires
for folder in ["videos", "subtitles", "translated_videos", "voices", "voices/temp"]:
    os.makedirs(folder, exist_ok=True)

# ==================== Fonctions ====================

def get_video_duration(video_path):
    """Retourne la durée de la vidéo en secondes avec ffprobe"""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", video_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    output = (result.stdout or "").strip()
    if not output:
        raise RuntimeError("ffprobe n'a pas retourné la durée de la vidéo.")
    try:
        return float(output)
    except ValueError as e:
        raise RuntimeError(f"Impossible de parser la durée renvoyée par ffprobe: {output}") from e

def download_youtube_video(url, output_path="videos/"):
    socketio.emit('log', "🔄 Tentative de téléchargement de la vidéo...")
    try:
        # Configuration de yt-dlp
        ydl_opts = {
            'format': 'best[ext=mp4]',
            'outtmpl': os.path.join(output_path, '%(title)s.%(ext)s'),
            'progress_hooks': [lambda d: socketio.emit('log', f"📥 Téléchargement : {d['_percent_str']} - {d.get('_speed_str', 'calcul...')}")],
            'no_warnings': True,
            'quiet': True
        }

        # Téléchargement de la vidéo
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # Récupérer les informations de la vidéo
                info = ydl.extract_info(url, download=False)
                socketio.emit('log', f"✨ Vidéo trouvée : {info['title']}")
                
                # Télécharger la vidéo
                socketio.emit('log', "🎥 Démarrage du téléchargement...")
                ydl.download([url])
                
                # Construire le chemin du fichier téléchargé
                output_file = os.path.join(output_path, f"{info['title']}.mp4")
                socketio.emit('log', "✅ Téléchargement terminé")
                return output_file

            except Exception as e:
                error_msg = str(e)
                if "Private video" in error_msg:
                    raise Exception("Cette vidéo est privée")
                elif "Video unavailable" in error_msg:
                    raise Exception("Cette vidéo n'est pas disponible")
                elif "age-restricted" in error_msg:
                    raise Exception("Cette vidéo nécessite une vérification d'âge")
                else:
                    raise Exception(f"Erreur lors du téléchargement : {error_msg}")

    except Exception as e:
        socketio.emit('log', f"❌ {str(e)}")
        raise


def transcribe_audio(video_path, mode="auto"):
    model_size = "tiny" if mode=="short" else "base"
    model = whisper.load_model(model_size)
    socketio.emit('log', f"🎧 Transcription avec Whisper ({model_size})...")
    result = model.transcribe(video_path, language="en")
    socketio.emit('log', f"✅ Transcription terminée ({len(result['segments'])} segments)")
    return result["segments"]

def translate_segments(segments, mode="auto"):
    translator = GoogleTranslator(source='en', target='fr')
    total = len(segments)
    socketio.emit('log', "🌐 Traduction des segments...")
    for i, seg in enumerate(segments):
        try:
            seg["text_fr"] = translator.translate(seg["text"])
        except Exception:
            seg["text_fr"] = "[Erreur de traduction]"
        socketio.emit('progress', {'current': i+1, 'total': total, 'step': 'Traduction'})
        time.sleep(0.05 if mode=="short" else 0.3)
    socketio.emit('log', "✅ Traduction terminée")
    return segments

def generate_voice(segments, mode="auto", output_audio="voices/voice_fr.mp3"):
    total = len(segments)
    if mode=="short":
        text_total = " ".join([seg["text_fr"] for seg in segments])
        tts = gTTS(text=text_total, lang="fr")
        tts.save(output_audio)
        socketio.emit('log', f"✅ Voix française générée : {output_audio}")
    else:
        temp_files = []
        for i, seg in enumerate(segments):
            text = seg.get("text_fr","").strip()
            if not text: continue
            temp_path = f"voices/temp/segment_{i}.mp3"
            tts = gTTS(text=text, lang="fr")
            tts.save(temp_path)
            temp_files.append(temp_path)
            socketio.emit('progress', {'current': i+1, 'total': total, 'step': 'Génération voix'})
        if temp_files:
            inputs = [ffmpeg.input(f) for f in temp_files]
            ffmpeg.concat(*inputs, v=0, a=1).output(output_audio).run(overwrite_output=True)
        for f in temp_files: os.remove(f)
        socketio.emit('log', f"✅ Voix française complète générée : {output_audio}")
    return output_audio

def generate_srt(segments, output_srt="subtitles/subtitles.srt"):
    subs = pysrt.SubRipFile()
    for i, seg in enumerate(segments):
        subs.append(pysrt.SubRipItem(
            index=i+1,
            start=pysrt.SubRipTime(seconds=seg["start"]),
            end=pysrt.SubRipTime(seconds=seg["end"]),
            text=seg["text_fr"]
        ))
    subs.save(output_srt, encoding='utf-8')
    return output_srt

def replace_audio(video_path, new_audio_path, output_path="translated_videos/output.mp4"):
    try:
        socketio.emit('log', "🎬 Fusion de la vidéo avec la nouvelle audio...")
        # Utiliser subprocess directement pour plus de contrôle
        cmd = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-i', new_audio_path,
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-map', '0:v:0',
            '-map', '1:a:0',
            '-shortest',
            output_path
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        socketio.emit('log', "✅ Fusion audio/vidéo terminée")
        return output_path
    except subprocess.CalledProcessError as e:
        socketio.emit('log', f"❌ Erreur lors de la fusion : {e.stderr}")
        raise Exception("Erreur lors de la fusion audio/vidéo")

# ==================== Routes Flask ====================

@app.route("/", methods=["GET"])
def index():
    try:
        return render_template("index.html")
    except Exception as e:
        # Log l'erreur complète
        print(f"Erreur lors du chargement de l'index: {str(e)}")
        # Retourner un message d'erreur détaillé
        return f"""
        <h1>Erreur de chargement du template</h1>
        <p>Une erreur est survenue lors du chargement de la page :</p>
        <pre style="background:#f8f9fa;padding:15px;border-radius:5px">{str(e)}</pre>
        <h2>Vérifiez que :</h2>
        <ul>
            <li>Le fichier <code>templates/index.html</code> existe</li>
            <li>Le dossier <code>templates/</code> est au même niveau que <code>app.py</code></li>
            <li>Les permissions des fichiers sont correctes</li>
        </ul>
        """, 500

@app.route("/process_video", methods=["POST"])
def process_video():
    youtube_url = request.form["youtube_url"]
    try:
        # Créer un identifiant unique pour cette vidéo
        import uuid
        video_id = str(uuid.uuid4())[:8]
        
        # Téléchargement
        video_path = download_youtube_video(youtube_url)
        
        # Durée via FFmpeg
        duration_min = get_video_duration(video_path)/60
        mode = "short" if duration_min < 30 else "long"
        socketio.emit('log', f"Mode choisi : {mode} ({duration_min:.2f} min)")

        # Processus complet
        segments = transcribe_audio(video_path, mode=mode)
        translated_segments = translate_segments(segments, mode=mode)
        
        # Generate subtitles and voice
        srt_path = generate_srt(translated_segments, f"subtitles/sous_titres_{video_id}.srt")
        voice_path = generate_voice(translated_segments, mode=mode, output_audio=f"voices/voix_{video_id}.mp3")
        
        # Create final video
        output_path = f"translated_videos/video_traduite_{video_id}.mp4"
        output_video = replace_audio(video_path, voice_path, output_path)
        
        filename = os.path.basename(output_video)
        socketio.emit('finished', {'video_file': filename})
        return "ok"
        
    except Exception as e:
        socketio.emit('error', str(e))
        return "error"

@app.route("/translated_videos/<filename>")
def download_video(filename):
    return send_from_directory("translated_videos", filename, as_attachment=True)

if __name__ == "__main__":
    print("🌐 Démarrage du serveur...")
    print("📝 Accédez à l'application sur : http://127.0.0.1:5000")
    socketio.run(app, host='127.0.0.1', port=5000, debug=True)
