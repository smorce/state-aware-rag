# ADR-001 HelixDB と Python の結合方針

## 決定

HelixDB は Rust ライブラリとして Python に直接リンクせず、Rust 製 DB サーバを別プロセスとして起動し、Python から `POST /v1/query` を呼び出す。

## 理由

HelixDB の公式 README は、ローカルインスタンスを `helix start dev` で起動し、Rust SDK または TypeScript SDK で作成した dynamic query JSON を `/v1/query` に送る構成を前提にしている。Python から Rust FFI を直接組むと、Windows のビルドツールチェーン、ABI、ライフサイクル、クラッシュ境界が複雑になる。今回の RAG 実装では DB の責務は永続化・検索・関係探索なので、HTTP 境界で分離する方が単純で運用しやすい。

## 実装方針

- `vendor/helix-db` に HelixDB 本体を shallow clone する。
- Python 側は `HelixHttpClient` で `/v1/query` を呼ぶ。
- Dynamic query JSON は、必要に応じて `HelixTypeScriptQueryBuilder` が同梱 TypeScript SDK を sidecar として呼び出して生成する。
- 標準のテストとローカル実行は SQLite backend で完結させる。HelixDB がインストール・起動済みの環境では HTTP adapter に差し替える。

## 代替案

- PyO3 / maturin で Rust crate を Python 拡張にする案は、HelixDB が DB サーバ・CLI・SDK を中心に公開している現状では過剰。
- Python から CLI を毎回起動する案は、プロセス起動コストが大きく、エラー処理もしにくい。
- TypeScript アプリへ全面移行する案は、今回の Python LLM クライアント資産を活かしにくい。

## 現時点の制約

HelixDB のローカル実行は Docker または Podman を必要とする。Helix CLI 3.0.5 はホスト OS に応じて使い分ける。

- Windows ホスト: `.tools/helix.exe` (PE32+, x86_64)。Docker Desktop が無いため、当初は `helix init` までしか確認できなかった。
- WSL2 Linux: `.tools/helix` (ELF64, x86_64)。Docker Engine 29.1.3 が動いているため `helix start dev` で `ghcr.io/helixdb/enterprise-dev` コンテナが起動し、`http://localhost:6969/v1/query` へ動的クエリを投げられる。

WSL2 Linux 側で Docker 上 HelixDB の end-to-end を検証済み (`docs/verification.md`)。Node.js は両方の OS で利用できるため、TypeScript SDK sidecar による dynamic query JSON 生成は問題ない。
