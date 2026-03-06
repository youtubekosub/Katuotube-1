import os
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib.parse
import datetime
import random
import time
import tempfile
import subprocess
import re
from functools import lru_cache, wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, session, send_file, after_this_request
import yt_dlp
from urllib.parse import quote
import zipfile
import shutil
from flask import send_from_directory
from werkzeug.utils import secure_filename


# Vercel/Renderのディレクトリ構造に対応するためのパス設定
base_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(base_dir, 'templates')
if not os.path.exists(template_dir):
    template_dir = os.path.join(os.path.dirname(base_dir), 'templates')

# ゲームの解凍先ディレクトリ
GAMES_DIR = os.path.join(base_dir, 'games_data')
if not os.path.exists(GAMES_DIR):
    os.makedirs(GAMES_DIR)

app = Flask(__name__, template_folder=template_dir)
app.config['JSON_AS_ASCII'] = False
app.secret_key = os.environ.get('SESSION_SECRET', os.environ.get('SECRET_KEY', 'katuotube-key'))

# セッション設定
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('RENDER', False) or os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

PASSWORD = os.environ.get('APP_PASSWORD', 'katuo')

# --- 共通設定・API ---

# 最優先インスタンス
PRIORITY_INSTANCE = "https://yt.omada.cafe"

INVIDIOUS_INSTANCES = [
    'https://inv.nadeko.net/',
    'https://invidious.f5.si/',
    'https://invidious.lunivers.trade/',
    'https://invidious.ducks.party/',
    'https://iv.melmac.space/',
    'https://invidious.nerdvpn.de/',
    "https://invidious.privacyredirect.com",
    "https://invidious.technicalvoid.dev",
    "https://invidious.darkness.services",
    "https://invidious.nikkosphere.com",
    "https://invidious.schenkel.eti.br",
    "https://invidious.tiekoetter.com",
    "https://invidious.perennialte.ch",
    "https://invidious.reallyaweso.me",
    "https://invidious.private.coffee",
    "https://invidious.privacydev.net",
]

M3U8_API = "https://yudlp.vercel.app/m3u8/"
STREAM_API = "https://ytdlpinstance-vercel.vercel.app/stream/"
EDU_VIDEO_API = "https://siawaseok.duckdns.org/api/video2/"

EDU_PARAM_SOURCES = {
    'siawaseok': {'url': 'https://raw.githubusercontent.com/siawaseok3/wakame/master/video_config.json', 'type': 'json_params'},
    'kahoot': {'url': 'https://apis.kahoot.it/media-api/youtube/key', 'type': 'kahoot_key'}
}

# HTTPセッションの設定 (高速化のためプールサイズを拡大)
http_session = requests.Session()
retry_strategy = Retry(total=2, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=50, pool_maxsize=50)
http_session.mount("http://", adapter)
http_session.mount("https://", adapter)

# --- ユーティリティ関数 ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_random_headers():
    return {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

def request_invidious_api(path, timeout=(2, 4)):
    # 優先インスタンスを先頭にし、残りをシャッフル
    others = [i for i in INVIDIOUS_INSTANCES if i.rstrip('/') != PRIORITY_INSTANCE.rstrip('/')]
    random.shuffle(others)
    instances = [PRIORITY_INSTANCE] + others

    for instance in instances:
        base_url = instance.rstrip('/')
        try:
            res = http_session.get(f"{base_url}/api/v1{path}", timeout=timeout, headers=get_random_headers())
            if res.status_code == 200: 
                return res.json()
        except: 
            continue
    return None

@lru_cache(maxsize=1)
def get_edu_params(source='siawaseok'):
    config = EDU_PARAM_SOURCES.get(source, EDU_PARAM_SOURCES['siawaseok'])
    try:
        res = http_session.get(config['url'], timeout=3)
        data = res.json()
        if config['type'] == 'kahoot_key':
            return f"autoplay=1&rel=0&key={data.get('key', '')}"
        return data.get('params', '').replace('&amp;', '&')
    except: 
        return "autoplay=1&rel=0"

# --- 動画ソース取得 ---
def fetch_api_data(url):
    try:
        res = http_session.get(url, timeout=2.5)
        return res.json() if res.status_code == 200 else None
    except:
        return None

def get_stream_url(video_id, edu_source='siawaseok', video_info=None):
    edu_params = get_edu_params(edu_source)
    
    sources = {
        'primary': None, 'fallback': None, 'm3u8': None, 'high': None, 'backup': None, 'dash': None,
        'embed': f"https://www.youtube-nocookie.com/embed/{video_id}?autoplay=1",
        'education': f"https://www.youtubeeducation.com/embed/{video_id}?{edu_params}"
    }

    # APIリクエストを並列実行して高速化
    api_urls = [f"{M3U8_API}{video_id}", f"{STREAM_API}{video_id}"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_to_url = {executor.submit(fetch_api_data, url): url for url in api_urls}
        
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            data = future.result()
            if not data: continue

            if M3U8_API in url:
                if data.get('m3u8_formats'):
                    sources['m3u8'] = data['m3u8_formats'][0].get('url')
                    sources['high'] = sources['m3u8']
            elif STREAM_API in url:
                formats = data.get('formats', [])
                itag_18 = next((f.get('url') for f in formats if str(f.get('itag')) == '18'), None)
                if itag_18:
                    sources['primary'] = itag_18
                elif formats:
                    sources['primary'] = formats[0].get('url')
                
                for f in formats:
                    if f.get('ext') == 'webm' and not sources['fallback']:
                        sources['fallback'] = f.get('url')

    # 外部APIで失敗した場合、Invidiousのデータを使用
    if not sources['m3u8'] and not sources['primary'] and video_info:
        if video_info.get("hlsUrl"):
            sources['m3u8'] = video_info["hlsUrl"]
            sources['high'] = video_info["hlsUrl"]

        if video_info.get('formatStreams'):
            itag_18_inv = next((f.get('url') for f in video_info['formatStreams'] if str(f.get('itag')) == '18'), None)
            sources['primary'] = itag_18_inv if itag_18_inv else video_info['formatStreams'][0].get('url')
        
        adaptive = video_info.get("adaptiveFormats", [])
        best_audio = None
        best_videos = {}

        for f in adaptive:
            mime = f.get("type", "")
            if mime.startswith("audio/"):
                if not best_audio or f.get("bitrate", 0) > best_audio.get("bitrate", 0):
                    best_audio = f
            elif mime.startswith("video/"):
                h = f.get("height")
                if h:
                    if str(h) not in best_videos or "mp4" in mime:
                        best_videos[str(h)] = f

        if best_audio and best_videos:
            sources['dash'] = {
                "audio": {"url": best_audio["url"], "mime": best_audio["type"]},
                "videos": {str(h): {"url": v["url"], "mime": v["type"]} for h, v in best_videos.items()}
            }

    return sources

# --- ルート定義 ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        return "パスワードが違います", 401
    return render_template('login.html')

@app.route('/')
@login_required
def index():
    videos = request_invidious_api("/popular") or []
    theme = request.cookies.get('theme', 'dark')
    return render_template('home.html', videos=videos, theme=theme)

@app.route('/search')
@login_required
def search():
    query = request.args.get('q', '')
    if not query: return redirect(url_for('index'))
    results = request_invidious_api(f"/search?q={urllib.parse.quote(query)}") or []
    theme = request.cookies.get('theme', 'dark')
    return render_template('search.html', results=results, query=query, theme=theme)

@app.route('/watch')
@login_required
def watch():
    v_id = request.args.get('v')
    if not v_id: return redirect(url_for('index'))
    
    # 高速化のため、Invidious APIとEDU APIを検討
    video_info = request_invidious_api(f"/videos/{v_id}")
    if not video_info:
        try:
            edu_res = http_session.get(f"{EDU_VIDEO_API}{v_id}", timeout=5)
            video_info = edu_res.json()
        except:
            return redirect(f"/sub/watch?v={v_id}")

    edu_source = request.cookies.get('edu_source', 'siawaseok')
    sources = get_stream_url(v_id, edu_source, video_info)
    
    if not sources.get('m3u8'):
        sources['m3u8'] = ""

    if not sources.get('m3u8') and not sources.get('primary'):
         return redirect(f"/sub/watch?v={v_id}")

    comments_data = request_invidious_api(f"/comments/{v_id}")
    comments = comments_data.get('comments', []) if comments_data else []
    
    theme = request.cookies.get('theme', 'dark')
    
    return render_template('watch.html', 
                           video=video_info, 
                           video_id=v_id,
                           sources=sources, 
                           streams=sources,
                           comments=comments,
                           theme=theme,
                           mode=request.args.get('mode', 'stream'))

@app.route('/proxy/thumb')
def thumb_proxy():
    v_id = request.args.get('v')
    url = f"https://i.ytimg.com/vi/{v_id}/mqdefault.jpg"
    try:
        res = http_session.get(url, timeout=5)
        return Response(res.content, mimetype='image/jpeg')
    except: return "", 404

# --- 修正版 suggest ルート ---
@app.route('/suggest')
@app.route('/api/suggestions') # 両方のパスに対応させる
def suggest():
    # 'keyword' または 'q' の両方のパラメータに対応
    keyword = request.args.get('keyword') or request.args.get('q') or ''
    if not keyword:
        return jsonify([])
        
    try:
        # タイムアウトを短くしてレスポンスを高速化
        res = http_session.get(
            f"https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={urllib.parse.quote(keyword)}", 
            timeout=1.5 
        )
        if res.status_code == 200:
            return jsonify(res.json()[1])
        return jsonify([])
    except:
        return jsonify([])


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/tool.html')
def tool():
    return render_template('tool.html')

@app.route('/html.html')
def html_tool():
    return render_template('html.html')

# プロキシ経由でHTMLを取得するAPI
@app.route('/api/proxy')
def proxy():
    target_url = request.args.get('url')
    if not target_url:
        return jsonify({"error": "URLを指定してください"}), 400
    
    try:
        # ユーザーエージェントを偽装してブロックを防ぐ
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(target_url, headers=headers, timeout=10)
        response.raise_for_status()
        return jsonify({"html": response.text})
    except Exception as e:
        return jsonify({"error": f"取得失敗: {str(e)}"}), 500

@app.route('/history.html')
def history():
    # 本来はデータベースから取得しますが、まずはページを表示します
    return render_template('history.html')

@app.route('/settings.html')
def settings():
    return render_template('settings.html')

@app.route('/game.html')
def game_list():
    # ゲーム一覧ページを表示
    return render_template('game.html')

@app.route('/snow.html')
def snow_game():
    # Snowゲームの本体ページを表示
    return render_template('snow.html')

@app.route('/2048.html')
def game_2048():
    # 2048ゲームの本体ページを表示
    return render_template('2048.html')

@app.route('/link.html')
def link_checker():
    return render_template('link.html')

# --- YouTubeダウンローダーのページを表示 ---
@app.route('/download.html')
@login_required
def downloader():
    return render_template('download.html')

# --- 動画情報を解析して返すAPI (予備機能付き) ---
@app.route('/api/analyze', methods=['POST'])
@login_required
def analyze_video():
    data = request.json
    video_url = data.get('url')

    if not video_url:
        return jsonify({"error": "URLを入力してください"}), 400

    # 動画IDを抽出
    video_id_match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', video_url)
    video_id = video_id_match.group(1) if video_id_match else None

    # 1. まず yudlp インスタンスを試行
    try:
        api_url = f"https://yudlp.vercel.app/stream/{urllib.parse.quote(video_url, safe='')}"
        res = http_session.get(api_url, timeout=8)
        
        if res.status_code == 200:
            video_data = res.json()
            formats = []
            for f in video_data.get('formats', []):
                filesize = f.get('filesize')
                size_str = f"{round(filesize / 1024 / 1024, 1)}MB" if filesize else "不明"
                formats.append({
                    'url': f.get('url'),
                    'ext': f.get('ext'),
                    'resolution': f.get('resolution') or f.get('format_note') or 'audio',
                    'size': size_str,
                    'type': '🎬 動画' if f.get('vcodec') != 'none' else '🎵 音声'
                })
            return jsonify({
                "title": video_data.get('title'),
                "thumbnail": video_data.get('thumbnail'),
                "duration": video_data.get('duration'),
                "formats": formats[::-1]
            })
    except Exception:
        pass # 失敗した場合は次のステップへ

    # 2. Invidious インスタンスを試行 (yt.omada.cafe, inv.nadeko.net)
    if video_id:
        fallback_instances = ["https://yt.omada.cafe", "https://inv.nadeko.net"]
        for instance in fallback_instances:
            try:
                inv_api_url = f"{instance}/api/v1/videos/{video_id}"
                res = http_session.get(inv_api_url, timeout=5, headers=get_random_headers())
                
                if res.status_code == 200:
                    inv_data = res.json()
                    formats = []
                    
                    # Invidiousの形式を統合
                    # formatStreams (動画+音声)
                    for f in inv_data.get('formatStreams', []):
                        formats.append({
                            'url': f.get('url'),
                            'ext': f.get('container') or 'mp4',
                            'resolution': f.get('qualityLabel') or '720p',
                            'size': '不明',
                            'type': '🎬 動画'
                        })
                    # adaptiveFormats (高画質/音声のみ)
                    for f in inv_data.get('adaptiveFormats', []):
                        is_audio = f.get('type', '').startswith('audio/')
                        formats.append({
                            'url': f.get('url'),
                            'ext': f.get('container') or ( 'm4a' if is_audio else 'webm'),
                            'resolution': f.get('qualityLabel') or (f"{f.get('bitrate','')}kbps" if is_audio else 'adaptive'),
                            'size': '不明',
                            'type': '🎵 音声' if is_audio else '🎬 動画(映像のみ)'
                        })

                    return jsonify({
                        "title": inv_data.get('title'),
                        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
                        "duration": inv_data.get('lengthSeconds'),
                        "formats": formats
                    })
            except Exception:
                continue

    return jsonify({"error": "すべてのインスタンスで取得に失敗しました。時間をおいて試してください。"}), 500


# --- 追加：高画質再生ルート（修正版） ---
@app.route('/high')
@login_required
def high_quality_watch():
    v_id = request.args.get('v')
    if not v_id:
        return redirect(url_for('index'))

    # 設定の読み込み (デフォルトは hls)
    preferred_mode = request.cookies.get('player_mode', 'hls')

    target_instance = "https://yt.omada.cafe"
    video_info = None

    try:
        res = http_session.get(f"{target_instance}/api/v1/videos/{v_id}", timeout=5, headers=get_random_headers())
        if res.status_code == 200:
            video_info = res.json()
    except Exception:
        pass

    if not video_info:
        video_info = request_invidious_api(f"/videos/{v_id}")

    if not video_info:
        return "動画データの取得に失敗しました。時間をおいて試してください。", 404

    edu_source = request.cookies.get('edu_source', 'siawaseok')
    base_sources = get_stream_url(v_id, edu_source, video_info)

    adaptive = video_info.get("adaptiveFormats", [])
    video_url = None
    audio_url = None

    for res in ["2160p", "1440p", "1080p", "720p"]:
        v_stream = next((f for f in adaptive if f.get("resolution") == res and "video" in f.get("type", "")), None)
        if v_stream:
            # quoteを使用してURLエンコードを行い、プロキシを確実に通す
            video_url = f"/proxy/video?url={quote(v_stream.get('url'))}"
            break

    a_stream = next((f for f in adaptive if f.get("audioQuality") == "AUDIO_QUALITY_MEDIUM"), 
                    next((f for f in adaptive if "audio" in f.get("type", "")), None))
    if a_stream and isinstance(a_stream, dict):
        audio_url = f"/proxy/video?url={quote(a_stream.get('url'))}"

    # HLS(m3u8)の取得ロジック
    m3u8_url = None
    try:
        hls_res = requests.get(f"https://yudlp.vercel.app/m3u8/{v_id}", timeout=10)
        hls_data = hls_res.json()
        m3u8_formats = hls_data.get("m3u8_formats", [])
        if m3u8_formats:
            # 解像度（高さ）でソートして最高画質を取得
            sorted_formats = sorted(
                m3u8_formats,
                key=lambda x: int(x.get("resolution", "0x0").split("x")[-1] if "x" in x.get("resolution", "") else 0),
                reverse=True
            )
            m3u8_url = sorted_formats[0].get("url")
    except Exception:
        # 取得失敗時は base_sources の m3u8 をフォールバックとして使用
        m3u8_url = base_sources.get('m3u8')

    return render_template('high.html', 
                           video_title=video_info.get('title', '高画質再生'),
                           video_id=v_id,
                           video_url=video_url,
                           audio_url=audio_url,
                           m3u8_url=m3u8_url,
                           fallback_url=base_sources.get('primary'),
                           preferred_mode=preferred_mode)

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(Exception)
def handle_exception(e):
    return render_template('error.html'), 500

@app.route('/paperio.html')
@login_required
def paperio_game():
    return render_template('paperio.html')

@app.route('/channel/<cid>')
@login_required
def channel(cid):
    # APIからデータを取得
    channel_info = request_invidious_api(f"/channels/{cid}")
    if not channel_info:
        return "チャンネルが見つかりません", 404
    
    # チャンネル内の最新動画を取得
    videos = channel_info.get('latestVideos', [])
    
    # ダークモード設定の有無を確認（テンプレート側のJSと連動）
    return render_template('channel.html', 
                           channel=channel_info, 
                           videos=videos)

@app.route('/contact.html')
@login_required
def contact():
    return render_template('contact.html')

@app.route('/faq.html')
@login_required
def faq():
    return render_template('faq.html')

@app.route('/bbs.html')
@login_required
def bbs():
    return render_template('bbs.html')

@app.route('/snowrider.html')
def snowrider():
    return render_template('snowrider.html')

@app.route('/padlet.html')
@login_required
def padlet_page():
    # Padletページを表示
    return render_template('padlet.html')

@app.route('/block.html')
def block_blast():
    return render_template('block.html')

# --- ゲーム実行機能 ---

@app.route('/play_hoyo')
@login_required
def play_hoyo():
    """リポジトリ内のhoyo.zipを解凍して実行するルート"""
    target_zip = os.path.join(base_dir, 'hoyo.zip')
    game_id = "hoyo"
    game_path = os.path.join(GAMES_DIR, game_id)

    # 1. ZIPの存在確認
    if not os.path.exists(target_zip):
        return "エラー: リポジトリのルートに hoyo.zip が見つかりません。", 404

    # 2. 解凍処理（フォルダがない場合のみ実行）
    if not os.path.exists(game_path):
        os.makedirs(game_path, exist_ok=True)
        try:
            with zipfile.ZipFile(target_zip, 'r') as zip_ref:
                zip_ref.extractall(game_path)
        except Exception as e:
            return f"解凍エラーが発生しました: {str(e)}", 500

    # 3. プレイヤー画面へ遷移
    return redirect(url_for('play_game', game_id=game_id))

@app.route('/play_game/<game_id>')
@login_required
def play_game(game_id):
    """ゲームを表示するHTMLプレイヤー"""
    # ゲーム内の index.html へのURLを生成
    game_url = url_for('serve_game_files', game_id=game_id, path='index.html')
    return render_template('game_player.html', game_url=game_url, game_id=game_id)

@app.route('/games_content/<game_id>/<path:path>')
@login_required
def serve_game_files(game_id, path):
    """解凍されたゲームファイルをブラウザに配信する"""
    return send_from_directory(os.path.join(GAMES_DIR, game_id), path)

@app.route('/upload_game', methods=['POST'])
@login_required
def upload_game():
    """新しいZIPをアップロードして実行する場合のルート"""
    if 'game_zip' not in request.files:
        return "ファイルが選択されていません", 400
    
    file = request.files['game_zip']
    if file.filename == '' or not file.filename.endswith('.zip'):
        return "無効なファイルです（.zipのみ）", 400

    filename = secure_filename(file.filename)
    game_id = filename.rsplit('.', 1)[0]
    game_path = os.path.join(GAMES_DIR, game_id)

    if os.path.exists(game_path):
        shutil.rmtree(game_path)
    os.makedirs(game_path)

    zip_path = os.path.join(GAMES_DIR, filename)
    file.save(zip_path)
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(game_path)
        os.remove(zip_path)
    except Exception as e:
        return f"解凍エラー: {str(e)}", 500

    return redirect(url_for('play_game', game_id=game_id))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
