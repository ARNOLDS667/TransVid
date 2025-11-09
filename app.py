from flask import Flask, render_template, request, send_from_directory, jsonify
from flask_socketio import SocketIO
import os, time, subprocess, shutil, json
import yt_dlp
import whisper
import sys
import threading
from datetime import datetime
from cipher import get_initial_function_name
from pytube.cipher import get_initial_function_name as _get_initial_function_name
sys.modules['pytube.cipher'].get_initial_function_name = get_initial_function_name
from deep_translator import GoogleTranslator
from gtts import gTTS
import ffmpeg
import pysrt

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', ping_timeout=120, ping_interval=25)

# Création des répertoires
for folder in ["videos", "subtitles", "translated_videos", "voices", "voices/temp"]:
    os.makedirs(folder, exist_ok=True)

# Configuration de la rétention des fichiers
RETENTION_MINUTES = 10
temp_files = {}
temp_files_lock = threading.Lock()

def schedule_file_deletion(file_path):
    """Programme la suppression d'un fichier après RETENTION_MINUTES"""
    if not os.path.exists(file_path):
        return
    with temp_files_lock:
        temp_files[file_path] = time.time() + (RETENTION_MINUTES * 60)

def cleanup_expired_files():
    """Nettoie les fichiers qui ont dépassé leur durée de rétention"""
    current_time = time.time()
    to_delete = []
    
    with temp_files_lock:
        for file_path, expiry_time in list(temp_files.items()):
            if current_time >= expiry_time:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        socketio.emit('log', f"🗑️ Fichier expiré supprimé : {os.path.basename(file_path)}")
                    except Exception as e:
                        socketio.emit('log', f"⚠️ Erreur lors de la suppression : {str(e)}")
                to_delete.append(file_path)
        
        for file_path in to_delete:
            del temp_files[file_path]

def start_cleanup_thread():
    """Démarre le thread de nettoyage périodique"""
    def cleanup_loop():
        while True:
            cleanup_expired_files()
            time.sleep(60)
    
    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()

# ==================== Fonctions améliorées ====================

def emit_progress(step, current, total, message="", session_id=None):
    """Émet une progression détaillée"""
    percent = int((current / total * 100)) if total > 0 else 0
    data = {
        'step': step,
        'current': current,
        'total': total,
        'percent': percent,
        'message': message
    }
    if session_id:
        data['session_id'] = session_id
    socketio.emit('progress', data)

def get_video_info(url):
    """Récupère les informations de la vidéo sans la télécharger"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                'title': info.get('title', 'Vidéo sans titre'),
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail', ''),
                'channel': info.get('uploader', 'Chaîne inconnue'),
                'view_count': info.get('view_count', 0)
            }
    except Exception as e:
        raise Exception(f"Impossible de récupérer les infos : {str(e)}")

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
        raise RuntimeError(f"Impossible de parser la durée : {output}") from e

def download_youtube_video(url, output_path="videos/", quality="best", use_aria2=False, session_id=None):
    socketio.emit('log', "📥 Démarrage du téléchargement...")
    socketio.emit('step_change', {'step': 'download', 'message': 'Téléchargement de la vidéo'})
    
    try:
        if quality == 'best':
            fmt = 'best[ext=mp4]'
        elif quality == 'medium':
            fmt = 'best[height<=720][ext=mp4]/best[ext=mp4]'
        elif quality == 'low':
            fmt = 'best[height<=360][ext=mp4]/best[ext=mp4]'
        else:
            fmt = 'best[ext=mp4]'

        last_percent = [0]
        
        def progress_hook(d):
            if d['status'] == 'downloading':
                try:
                    percent_str = d.get('_percent_str', '0%').strip().replace('%', '')
                    percent = float(percent_str) if percent_str and percent_str != 'N/A' else 0
                    speed = d.get('_speed_str', 'Calcul...').strip()
                    eta = d.get('_eta_str', '?').strip()
                    
                    if abs(percent - last_percent[0]) >= 2 or percent >= 99:
                        emit_progress('download', int(percent), 100, 
                                    f"Vitesse: {speed} | Reste: {eta}", session_id)
                        socketio.emit('log', f"📥 {int(percent)}% - {speed}")
                        last_percent[0] = percent
                        
                except Exception as e:
                    pass
            elif d['status'] == 'finished':
                socketio.emit('log', "✅ Téléchargement terminé")

        ydl_opts = {
            'format': fmt,
            'outtmpl': os.path.join(output_path, '%(title)s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'no_warnings': False,
            'quiet': False,
            'no_color': True,
        }

        if use_aria2 and shutil.which('aria2c'):
            ydl_opts['external_downloader'] = 'aria2c'
            ydl_opts['external_downloader_args'] = ['-x', '16', '-s', '16', '-k', '1M']

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                socketio.emit('log', f"✨ Vidéo : {info.get('title')}")
                
                ydl.download([url])
                
                title = info.get('title') or 'video'
                ext = info.get('ext') or 'mp4'
                safe_title = title.replace('/', '_').replace('\\', '_')
                output_file = os.path.join(output_path, f"{safe_title}.{ext}")
                
                emit_progress('download', 100, 100, "Téléchargement terminé ✅", session_id)
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
                    raise Exception(f"Erreur de téléchargement : {error_msg}")

    except Exception as e:
        socketio.emit('log', f"❌ {str(e)}")
        raise

def transcribe_audio(video_path, mode="auto", session_id=None):
    """Transcription avec progression simulée car Whisper ne donne pas de feedback"""
    socketio.emit('step_change', {'step': 'transcribe', 'message': 'Transcription audio (patientez...)'})
    
    # TOUJOURS utiliser tiny pour la rapidité
    model_size = "tiny"  # tiny est 10x plus rapide que base !
    
    socketio.emit('log', f"🎧 Chargement du modèle Whisper ({model_size})...")
    socketio.emit('log', "⏳ Transcription en cours (peut prendre 2-5 min)...")
    emit_progress('transcribe', 20, 100, f"Chargement du modèle {model_size}...", session_id)
    
    # Thread pour simuler la progression pendant que Whisper travaille
    stop_simulation = [False]
    current_progress = [20]
    
    def simulate_progress():
        """Simule la progression pendant la transcription"""
        while not stop_simulation[0] and current_progress[0] < 95:
            time.sleep(3)  # Toutes les 3 secondes
            current_progress[0] += 5
            emit_progress('transcribe', current_progress[0], 100, 
                         f"Analyse audio en cours... {current_progress[0]}%", session_id)
            socketio.emit('log', f"🎧 Transcription... {current_progress[0]}%")
    
    progress_thread = threading.Thread(target=simulate_progress, daemon=True)
    progress_thread.start()
    
    try:
        model = whisper.load_model(model_size)
        result = model.transcribe(video_path, language="en", fp16=False, verbose=False)
        
        # Arrêter la simulation
        stop_simulation[0] = True
        progress_thread.join(timeout=1)
        
        total_segments = len(result['segments'])
        emit_progress('transcribe', 100, 100, f"{total_segments} segments détectés ✅", session_id)
        socketio.emit('log', f"✅ Transcription terminée ({total_segments} segments)")
        
        return result["segments"]
    except Exception as e:
        stop_simulation[0] = True
        raise e

def translate_segments(segments, mode="auto", session_id=None):
    socketio.emit('step_change', {'step': 'translate', 'message': 'Traduction des segments'})
    translator = GoogleTranslator(source='en', target='fr')
    total = len(segments)
    socketio.emit('log', f"🌐 Traduction de {total} segments...")
    
    for i, seg in enumerate(segments):
        try:
            seg["text_fr"] = translator.translate(seg["text"])
        except Exception as e:
            seg["text_fr"] = "[Erreur de traduction]"
        
        if (i + 1) % 3 == 0 or i == total - 1:
            emit_progress('translate', i+1, total, f"Segment {i+1}/{total}", session_id)
            socketio.emit('log', f"🌐 {i+1}/{total} segments traduits")
        
        time.sleep(0.05)
    
    socketio.emit('log', "✅ Traduction terminée")
    return segments

def generate_voice(segments, mode="auto", voice_gender="female", output_audio="voices/voice_fr.mp3", session_id=None):
    """Génération de voix avec choix du genre"""
    socketio.emit('step_change', {'step': 'voice', 'message': f'Génération voix {voice_gender}'})
    total = len(segments)
    
    # Configuration TLD selon le genre
    # female: .fr (France - voix féminine par défaut)
    # male: .ca (Canada - voix plus grave)
    tld = 'fr' if voice_gender == 'female' else 'ca'
    
    socketio.emit('log', f"🗣️ Génération voix {voice_gender} (domaine: .{tld})...")
    
    if mode=="short":
        text_total = " ".join([seg["text_fr"] for seg in segments])
        tts = gTTS(text=text_total, lang="fr", tld=tld)
        tts.save(output_audio)
        emit_progress('voice', 100, 100, "Voix générée ✅", session_id)
    else:
        temp_files = []
        for i, seg in enumerate(segments):
            text = seg.get("text_fr","").strip()
            if not text: continue
            temp_path = f"voices/temp/segment_{i}.mp3"
            tts = gTTS(text=text, lang="fr", tld=tld)
            tts.save(temp_path)
            temp_files.append(temp_path)
            
            if (i + 1) % 3 == 0 or i == total - 1:
                emit_progress('voice', i+1, total, f"Segment {i+1}/{total}", session_id)
                socketio.emit('log', f"🗣️ {i+1}/{total} segments")
        
        if temp_files:
            socketio.emit('log', "🔗 Fusion des segments audio...")
            inputs = [ffmpeg.input(f) for f in temp_files]
            ffmpeg.concat(*inputs, v=0, a=1).output(output_audio).run(overwrite_output=True, quiet=True)
        
        for f in temp_files: 
            try: os.remove(f)
            except: pass
    
    socketio.emit('log', f"✅ Voix {voice_gender} générée")
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

def replace_audio(video_path, new_audio_path, output_path="translated_videos/output.mp4", session_id=None):
    try:
        socketio.emit('step_change', {'step': 'merge', 'message': 'Fusion vidéo et audio'})
        socketio.emit('log', "🎬 Fusion de la vidéo avec le doublage...")
        
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
        emit_progress('merge', 100, 100, "Fusion terminée ✅", session_id)
        socketio.emit('log', "✅ Vidéo finale créée")
        return output_path
    except subprocess.CalledProcessError as e:
        socketio.emit('log', f"❌ Erreur fusion : {e.stderr}")
        raise Exception("Erreur lors de la fusion audio/vidéo")

# ==================== Routes Flask ====================

@app.route("/", methods=["GET"])
def index():
    try:
        return render_template("index.html")
    except Exception as e:
        print(f"Erreur template: {str(e)}")
        return f"<h1>Erreur</h1><pre>{str(e)}</pre>", 500

@app.route("/get_video_info", methods=["POST"])
def get_info():
    """Endpoint pour récupérer les infos d'une vidéo"""
    try:
        url = request.json.get('url')
        info = get_video_info(url)
        return jsonify({'success': True, 'info': info})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route("/process_video", methods=["POST"])
def process_video():
    youtube_url = request.form["youtube_url"]
    
    try:
        import uuid
        video_id = str(uuid.uuid4())[:8]
        session_id = video_id
        
        quality = request.form.get('quality', 'best')
        voice_gender = request.form.get('voice_gender', 'female')  # Nouveau paramètre
        use_aria2 = request.form.get('use_aria2', 'off') in ('on', 'true', '1')
        delete_original = True

        socketio.emit('log', "🚀 Démarrage du traitement...")

        # Téléchargement
        video_path = download_youtube_video(youtube_url, quality=quality, use_aria2=use_aria2, session_id=session_id)
        socketio.emit('log', f"📁 Fichier : {os.path.basename(video_path)}")
        
        # Durée et mode
        socketio.emit('log', "⏱️ Analyse de la durée...")
        duration_min = get_video_duration(video_path)/60
        mode = "short" if duration_min < 30 else "long"
        socketio.emit('log', f"📊 Mode: {mode} ({duration_min:.1f} min)")

        # Transcription
        segments = transcribe_audio(video_path, mode=mode, session_id=session_id)
        
        # Traduction
        translated_segments = translate_segments(segments, mode=mode, session_id=session_id)
        
        # Sous-titres
        socketio.emit('log', "📝 Génération des sous-titres...")
        srt_path = generate_srt(translated_segments, f"subtitles/sous_titres_{video_id}.srt")
        
        # Voix avec genre
        voice_path = generate_voice(
            translated_segments, 
            mode=mode, 
            voice_gender=voice_gender,
            output_audio=f"voices/voix_{video_id}.mp3", 
            session_id=session_id
        )
        
        # Vidéo finale
        output_path = f"translated_videos/video_traduite_{video_id}.mp4"
        output_video = replace_audio(video_path, voice_path, output_path, session_id=session_id)
        
        filename = os.path.basename(output_video)
        
        # Nettoyage
        if delete_original:
            try:
                socketio.emit('log', "🧹 Nettoyage...")
                for path in [video_path, voice_path, srt_path]:
                    if os.path.exists(path):
                        schedule_file_deletion(path)
                
                if os.path.isdir('voices/temp'):
                    for f in os.listdir('voices/temp'):
                        try:
                            schedule_file_deletion(os.path.join('voices/temp', f))
                        except: pass
            except Exception as e:
                socketio.emit('log', f"⚠️ Erreur nettoyage : {str(e)}")
        
        # Infos finales
        try:
            video_info = get_video_info(youtube_url)
        except:
            video_info = {'title': 'Vidéo', 'channel': 'N/A', 'duration': duration_min * 60}
        
        socketio.emit('log', "🎉 Traitement terminé !")
        socketio.emit('finished', {
            'video_file': filename,
            'info': video_info,
            'duration': duration_min,
            'session_id': session_id
        })
        return "ok"
        
    except Exception as e:
        error_msg = f"Erreur : {str(e)}"
        socketio.emit('log', f"❌ {error_msg}")
        socketio.emit('error', error_msg)
        print(f"ERREUR: {e}")
        import traceback
        traceback.print_exc()
        return "error"

@app.route("/translated_videos/<filename>")
def download_video(filename):
    return send_from_directory("translated_videos", filename, as_attachment=True)

if __name__ == "__main__":
    cleanup_expired_files()
    start_cleanup_thread()

    print("🚀 TransVid Pro - Serveur démarré")
    print("🌐 Interface: http://127.0.0.1:5000")
    socketio.run(app, host='127.0.0.1', port=5000, debug=True)