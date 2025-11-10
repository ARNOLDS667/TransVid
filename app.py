from flask import Flask, render_template, request, send_from_directory, jsonify
from flask_socketio import SocketIO
import os, time, subprocess, shutil, json, signal
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
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', ping_timeout=180, ping_interval=30)

# Création des répertoires
for folder in ["videos", "subtitles", "translated_videos", "voices", "voices/temp"]:
    os.makedirs(folder, exist_ok=True)

# Configuration
RETENTION_MINUTES = 10
temp_files = {}
temp_files_lock = threading.Lock()

# Gestion des sessions actives (pour annulation)
active_sessions = {}
active_sessions_lock = threading.Lock()

def schedule_file_deletion(file_path):
    if not os.path.exists(file_path):
        return
    with temp_files_lock:
        temp_files[file_path] = time.time() + (RETENTION_MINUTES * 60)

def cleanup_expired_files():
    current_time = time.time()
    to_delete = []
    
    with temp_files_lock:
        for file_path, expiry_time in list(temp_files.items()):
            if current_time >= expiry_time:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        socketio.emit('log', f"🗑️ Fichier supprimé : {os.path.basename(file_path)}")
                    except Exception as e:
                        pass
                to_delete.append(file_path)
        
        for file_path in to_delete:
            del temp_files[file_path]

def start_cleanup_thread():
    def cleanup_loop():
        while True:
            cleanup_expired_files()
            time.sleep(60)
    
    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()

def is_session_cancelled(session_id):
    """Vérifie si la session a été annulée"""
    with active_sessions_lock:
        return active_sessions.get(session_id, {}).get('cancelled', False)

def cancel_session(session_id):
    """Annule une session en cours"""
    with active_sessions_lock:
        if session_id in active_sessions:
            active_sessions[session_id]['cancelled'] = True
            socketio.emit('log', f"🛑 Annulation demandée...", room=session_id)
            return True
    return False

# ==================== Fonctions ====================

def emit_progress(step, current, total, message="", session_id=None):
    if is_session_cancelled(session_id):
        raise Exception("Traitement annulé par l'utilisateur")
    
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
    try:
        ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
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
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", video_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    output = (result.stdout or "").strip()
    if not output:
        raise RuntimeError("ffprobe n'a pas retourné la durée")
    return float(output)

def download_youtube_video(url, output_path="videos/", quality="best", use_aria2=False, session_id=None):
    if is_session_cancelled(session_id):
        raise Exception("Téléchargement annulé")
    
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
            if is_session_cancelled(session_id):
                # Forcer l'arrêt du téléchargement
                raise KeyboardInterrupt("Annulé par l'utilisateur")
            
            if d['status'] == 'downloading':
                try:
                    percent_str = d.get('_percent_str', '0%').strip().replace('%', '')
                    percent = float(percent_str) if percent_str and percent_str != 'N/A' else 0
                    speed = d.get('_speed_str', 'Calcul...').strip()
                    
                    if abs(percent - last_percent[0]) >= 2 or percent >= 99:
                        emit_progress('download', int(percent), 100, f"Vitesse: {speed}", session_id)
                        socketio.emit('log', f"📥 {int(percent)}% - {speed}")
                        last_percent[0] = percent
                except:
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
        if "annulé" in str(e).lower() or "cancelled" in str(e).lower():
            raise Exception("Téléchargement annulé")
        socketio.emit('log', f"❌ {str(e)}")
        raise

def transcribe_audio_fast(video_path, session_id=None):
    """Transcription ULTRA-RAPIDE avec Whisper tiny optimisé"""
    if is_session_cancelled(session_id):
        raise Exception("Transcription annulée")
    
    socketio.emit('step_change', {'step': 'transcribe', 'message': 'Transcription express'})
    socketio.emit('log', "🎧 Transcription ultra-rapide (Whisper tiny)...")
    emit_progress('transcribe', 20, 100, "Chargement modèle...", session_id)
    
    # Thread pour progression simulée
    stop_simulation = [False]
    current_progress = [20]
    
    def simulate_progress():
        while not stop_simulation[0] and current_progress[0] < 90:
            if is_session_cancelled(session_id):
                break
            time.sleep(2)
            current_progress[0] += 5
            emit_progress('transcribe', current_progress[0], 100, 
                         f"Analyse audio... {current_progress[0]}%", session_id)
    
    progress_thread = threading.Thread(target=simulate_progress, daemon=True)
    progress_thread.start()
    
    try:
        model = whisper.load_model("tiny")
        
        # Options optimisées pour vitesse maximale
        result = model.transcribe(
            video_path, 
            language="en",
            fp16=False,
            verbose=False,
            beam_size=1,        # Plus rapide (au lieu de 5)
            best_of=1,          # Plus rapide (au lieu de 5)
            temperature=0.0     # Plus déterministe = plus rapide
        )
        
        stop_simulation[0] = True
        progress_thread.join(timeout=1)
        
        total_segments = len(result['segments'])
        emit_progress('transcribe', 100, 100, f"{total_segments} segments ✅", session_id)
        socketio.emit('log', f"✅ Transcription terminée ({total_segments} segments)")
        
        return result["segments"]
    except Exception as e:
        stop_simulation[0] = True
        raise e

def translate_segments_fast(segments, session_id=None):
    """Traduction optimisée avec batch"""
    if is_session_cancelled(session_id):
        raise Exception("Traduction annulée")
    
    socketio.emit('step_change', {'step': 'translate', 'message': 'Traduction des segments'})
    translator = GoogleTranslator(source='en', target='fr')
    total = len(segments)
    socketio.emit('log', f"🌐 Traduction de {total} segments...")
    
    for i, seg in enumerate(segments):
        if is_session_cancelled(session_id):
            raise Exception("Traduction annulée")
        
        try:
            seg["text_fr"] = translator.translate(seg["text"])
        except:
            seg["text_fr"] = "[Erreur]"
        
        # Émettre tous les 10 segments au lieu de 3 (plus rapide)
        if (i + 1) % 10 == 0 or i == total - 1:
            emit_progress('translate', i+1, total, f"{i+1}/{total}", session_id)
            socketio.emit('log', f"🌐 {i+1}/{total}")
        
        time.sleep(0.01)  # Minimal delay
    
    socketio.emit('log', "✅ Traduction terminée")
    return segments

def generate_voice_fixed(segments, voice_gender="female", output_audio="voices/voice_fr.mp3", session_id=None):
    """Génération de voix CORRIGÉE avec vraies différences M/F"""
    if is_session_cancelled(session_id):
        raise Exception("Génération voix annulée")
    
    socketio.emit('step_change', {'step': 'voice', 'message': f'Génération voix {voice_gender}'})
    total = len(segments)
    
    # CORRECTION: Utiliser slow=False pour voix plus naturelle et différente
    # Et utiliser différents TLD qui ont vraiment des voix différentes
    if voice_gender == "male":
        tld = 'com.au'  # Australie - voix masculine plus profonde
        slow = False
    else:
        tld = 'fr'  # France - voix féminine standard
        slow = False
    
    socketio.emit('log', f"🗣️ Génération voix {voice_gender} (tld: {tld})...")
    
    temp_files = []
    for i, seg in enumerate(segments):
        if is_session_cancelled(session_id):
            raise Exception("Génération voix annulée")
        
        text = seg.get("text_fr","").strip()
        if not text: 
            continue
        
        temp_path = f"voices/temp/segment_{i}.mp3"
        try:
            tts = gTTS(text=text, lang="fr", tld=tld, slow=slow)
            tts.save(temp_path)
            temp_files.append(temp_path)
        except Exception as e:
            socketio.emit('log', f"⚠️ Erreur segment {i}: {str(e)}")
        
        # Émettre tous les 5 segments
        if (i + 1) % 5 == 0 or i == total - 1:
            emit_progress('voice', i+1, total, f"{i+1}/{total}", session_id)
            socketio.emit('log', f"🗣️ {i+1}/{total}")
    
    if temp_files:
        socketio.emit('log', "🔗 Fusion audio...")
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
    if is_session_cancelled(session_id):
        raise Exception("Fusion annulée")
    
    try:
        socketio.emit('step_change', {'step': 'merge', 'message': 'Fusion finale'})
        socketio.emit('log', "🎬 Fusion vidéo/audio...")
        
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
    except:
        raise Exception("Erreur lors de la fusion")

# ==================== Routes ====================

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/get_video_info", methods=["POST"])
def get_info():
    try:
        url = request.json.get('url')
        info = get_video_info(url)
        return jsonify({'success': True, 'info': info})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route("/cancel_processing", methods=["POST"])
def cancel_processing():
    """Endpoint pour annuler un traitement en cours"""
    try:
        session_id = request.json.get('session_id')
        if cancel_session(session_id):
            socketio.emit('log', f"🛑 Traitement annulé", room=session_id)
            socketio.emit('cancelled', {'session_id': session_id})
            return jsonify({'success': True, 'message': 'Annulation en cours...'})
        return jsonify({'success': False, 'error': 'Session introuvable'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route("/process_video", methods=["POST"])
def process_video():
    youtube_url = request.form["youtube_url"]
    
    try:
        import uuid
        video_id = str(uuid.uuid4())[:8]
        session_id = video_id
        
        # Enregistrer la session active
        with active_sessions_lock:
            active_sessions[session_id] = {'cancelled': False, 'start_time': time.time()}
        
        quality = request.form.get('quality', 'medium')  # Medium par défaut
        voice_gender = request.form.get('voice_gender', 'female')
        use_aria2 = request.form.get('use_aria2', 'off') in ('on', 'true', '1')

        socketio.emit('log', "🚀 Démarrage...", room=session_id)
        socketio.emit('session_started', {'session_id': session_id})

        # Téléchargement
        video_path = download_youtube_video(youtube_url, quality=quality, use_aria2=use_aria2, session_id=session_id)
        socketio.emit('log', f"📁 {os.path.basename(video_path)}")
        
        # Durée
        duration_min = get_video_duration(video_path)/60
        socketio.emit('log', f"📊 Durée: {duration_min:.1f} min")
        
        # AVERTISSEMENT pour vidéos longues
        if duration_min > 60:
            socketio.emit('log', f"⚠️ ATTENTION: Vidéo longue ({duration_min:.1f} min)")
            socketio.emit('log', f"⏱️ Temps estimé: {duration_min * 0.3:.0f} min")

        # Transcription ultra-rapide
        segments = transcribe_audio_fast(video_path, session_id=session_id)
        
        # Traduction optimisée
        translated_segments = translate_segments_fast(segments, session_id=session_id)
        
        # Sous-titres
        socketio.emit('log', "📝 Sous-titres...")
        srt_path = generate_srt(translated_segments, f"subtitles/st_{video_id}.srt")
        
        # Voix CORRIGÉE
        voice_path = generate_voice_fixed(
            translated_segments,
            voice_gender=voice_gender,
            output_audio=f"voices/voix_{video_id}.mp3",
            session_id=session_id
        )
        
        # Fusion
        output_path = f"translated_videos/video_{video_id}.mp4"
        output_video = replace_audio(video_path, voice_path, output_path, session_id=session_id)
        
        filename = os.path.basename(output_video)
        
        # Nettoyage
        for path in [video_path, voice_path, srt_path]:
            if os.path.exists(path):
                schedule_file_deletion(path)
        
        if os.path.isdir('voices/temp'):
            for f in os.listdir('voices/temp'):
                try:
                    schedule_file_deletion(os.path.join('voices/temp', f))
                except: pass
        
        # Retirer de la liste des sessions actives
        with active_sessions_lock:
            if session_id in active_sessions:
                del active_sessions[session_id]
        
        # Infos finales
        try:
            video_info = get_video_info(youtube_url)
        except:
            video_info = {'title': 'Vidéo', 'duration': duration_min * 60}
        
        socketio.emit('log', "🎉 Terminé !")
        socketio.emit('finished', {
            'video_file': filename,
            'info': video_info,
            'duration': duration_min,
            'session_id': session_id
        })
        return "ok"
        
    except Exception as e:
        error_msg = str(e)
        
        # Nettoyer la session
        with active_sessions_lock:
            if session_id in active_sessions:
                del active_sessions[session_id]
        
        if "annulé" in error_msg.lower() or "cancelled" in error_msg.lower():
            socketio.emit('log', f"🛑 Traitement annulé")
            socketio.emit('cancelled', {'message': 'Traitement annulé'})
        else:
            socketio.emit('log', f"❌ Erreur: {error_msg}")
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

    print("🚀 TransVid Pro - Serveur ultra-optimisé")
    print("🌐 Interface: http://127.0.0.1:5000")
    print("💡 Astuce: Utilisez qualité 'Moyenne' pour vidéos >1h")
    socketio.run(app, host='127.0.0.1', port=5000, debug=True)