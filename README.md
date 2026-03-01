# katuotube かつおTube(Project Katuo)

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8%2B-brightgreen.svg)
![Flask](https://img.shields.io/badge/flask-2.0%2B-lightgrey.svg)

かつおtubeは、Flaskフレームワークを使用した軽量なYouTubeフロントエンド・プロキシサーバーです。
複数の外部APIを統合し、ネットワーク制限のある環境下でも、YouTubeコンテンツを視聴・取得することを目的としています。



## 🌟 主な機能

- **マルチインスタンス・ストリーミング:**
  - **Invidious API:** Googleのサーバーに直接アクセスせず、世界中のプロキシインスタンスを経由して動画情報を取得。
  - **HLS (m3u8) 対応:** 通信環境に応じて最適なビットレートを選択可能なストリーミング再生。
- **高画質 (Adaptive) モード:**
  - 映像と音声を別々のストリームで取得し、ブラウザ側で同期。最大4K（2160p）までの高解像度再生に対応。
- **教育フィルタ回避:**
  - YouTube Education APIやしあわせパラメータを適用し、制限の厳しいネットワーク内での再生成功率を向上。
- **統合ユーティリティ:**
  - **Downloader:** 動画情報の詳細解析および、動画・音声ファイルの抽出・保存。
  - **Web Proxy:** 指定したURLのHTMLを取得し、簡易的に表示するプロキシ機能。
- **エンタメ:** 2048、Snow、リンクチェッカーなどのミニゲームとツールを搭載。

## 🛠 システムアーキテクチャ

本アプリケーションは、パフォーマンスを最大化するために以下の工夫を凝らしています。

- **並列処理 (Concurrency):** `concurrent.futures.ThreadPoolExecutor` を使用。M3U8 APIとSTREAM APIへ同時にリクエストを送信し、最も速いレスポンスを動的に採用します。
- **キャッシング:** `functools.lru_cache` を利用して、APIキーや教育用設定情報の頻繁な外部通信を抑制。
- **リトライアルゴリズム:** `urllib3` のRetry戦略を実装し、不安定な外部サーバーとの接続時も安定性を確保。

## 🚀 デプロイ

### プリリクエスト
- Python 3.8以上
- `pip` (Pythonパッケージマネージャー)

### インストール

```bash
# リポジトリをクローン
git clone [https://github.com/youtubekosub/Katuotube-1/](https://github.com/youtubekosub/Katuotube-1)
cd katuotube

# 依存関係のインストール
自動
