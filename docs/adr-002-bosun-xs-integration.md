# ADR-002 Bosun XS の使用方針

## 決定

Bosun XS は自由回答モデルとして使わず、仕様書どおり yes/no 判定をスコア化する judge としてだけ使う。

## 一次情報からの制約

Hugging Face のモデルカードでは、Bosun XS は instruction と 2 つの finding を受け取り、`score = sigmoid(logits[yes_id] - logits[no_id])` で `0..1` の yes/no 判定スコアを得る judge と説明されている。GGUF 版では llama.cpp の `--rerank` mode は `<Instruct>` を捨てるため使ってはいけない、と明記されている。

参照:
- https://huggingface.co/Hanno-Labs/bosun-xs
- https://huggingface.co/Hanno-Labs/bosun-xs-GGUF

## 実装方針

- `NativeBosunXSScorer` は Transformers + PEFT で Bosun XS を Python プロセス内に読み、最終 token logits を直接使う。これを CLI の既定 (`--bosun xs`) として常用する。
- prompt は Bosun XS の `serving.json` にある `prefix` と `suffix` で包み、本文を `<Instruct>`, `<Query>`, `<Document>` で組み立てる。
- `<Document>` は `FINDING A` と `FINDING B` の ordered pair にする。
- native backend は `logits_to_keep=1` を指定し、最終位置の logits から公式 `yes_id` / `no_id` を読む。
- スコアは `sigmoid(logit_yes - logit_no)` として扱う。
- `yes_id` / `no_id` の token id が読み出せない場合は失敗させる。別モデルの yes/no 生成を Bosun XS として扱わない。
- CLI の既定は `--bosun xs` (native)。`--bosun rule` は外部モデルなしの開発検証用に限定する。

## やらないこと

- 別 llama-server に Bosun XS GGUF を載せて `completions.create(... logprobs=...)` で yes/no を読む経路は、本プロジェクトでは原則使わない。GGUF/llama.cpp 側のサンプリング・量子化が公式 native logits と完全一致する保証が弱く、judge スコアの再現性を落とす。
- llama.cpp の `--rerank` mode は `<Instruct>` を捨てるため使わない (HuggingFace モデルカード明記)。
- 上記理由から `BosunLlamaServerScorer` クラスと `--bosun-backend` CLI フラグは削除済み。Bosun XS GGUF を別サーバーで使いたくなった時点で改めて ADR を起こす。

## 環境変数

- `BOSUN_REPO` (既定 `Hanno-Labs/bosun-xs`)
- `BOSUN_BASE_MODEL` (既定 `Qwen/Qwen3-Reranker-0.6B`)
- `BOSUN_DEVICE` (未指定なら `cuda` if available else `cpu`)
- `BOSUN_SERVING_JSON` (公式 `serving.json` を上書きしたい場合のみ)
