**ドキュメントから「人名・会社名・商品名・契約名・日付・関係」などを取り出す部分は、基本的にはLLM側、またはアプリ側の前処理でやる**と考えるのが自然です。

ただし、HelixDB自体が「文書を入れたら自動でエンティティを抽出する検索エンジン」というより、**抽出したエンティティや関係を、グラフ・ベクトル・全文検索で保存して検索するデータベース**です。

【根拠】
HelixDBの公式説明では、データは「ラベル付きの点と線」として保存されます。点はノード、線はエッジです。ラベルは `$label` として保存され、型ごとの絞り込みや、ベクトル検索・全文検索の対象指定に使われます。つまり、`Person`、`Company`、`Document`、`Chunk` などのエンティティ型は、アプリ側が決めて保存します。([docs.helix-db.com][1])

また、公式の例では、`g().addN("User", { username: "alice" })` のように、アプリ側がラベルとプロパティを指定してノードを追加しています。HelixDBが文書を読んで `User` を自動生成しているわけではありません。([docs.helix-db.com][2])

ベクトル検索も同じです。公式例では、`Doc` ノードに `embedding` という数値配列を入れ、そのプロパティにベクトルインデックスを作っています。つまり、文章をベクトルにする処理も、通常はHelixDBの外側で行い、その結果をHelixDBへ保存します。([docs.helix-db.com][3])

全文検索についても、`Doc` の `body` に文字列を保存し、そのプロパティにテキストインデックスを作る形です。HelixDBは保存済みの文字列を検索しやすくしますが、文書の意味を読んでエンティティを切り出す役割は、公式例からは確認できません。([docs.helix-db.com][4])

実装の流れは、だいたいこうです。

```text
PDF / Word / HTML / Markdown
  ↓
本文を取り出す
  ↓
チャンクに分ける
  ↓
LLMでエンティティと関係を抽出する
  ↓
埋め込みベクトルを作る
  ↓
HelixDBに保存する
  ↓
グラフ検索 + ベクトル検索 + 全文検索で取り出す
```

たとえば、こういう形です。

```text
Document
 ├─ title
 ├─ body
 └─ embedding

Chunk
 ├─ text
 ├─ embedding
 └─ document_id

Person
 ├─ name
 └─ aliases

Company
 ├─ name
 └─ aliases

関係:
Person -[WORKS_AT]-> Company
Chunk  -[MENTIONS]-> Person
Chunk  -[MENTIONS]-> Company
Document -[HAS_CHUNK]-> Chunk
```

この場合、LLMがやるのは主にここです。

```json
{
  "entities": [
    { "type": "Company", "name": "ABC株式会社" },
    { "type": "Person", "name": "山田太郎" }
  ],
  "relations": [
    {
      "from": "山田太郎",
      "type": "WORKS_AT",
      "to": "ABC株式会社"
    }
  ]
}
```

HelixDBがやるのは、その結果を保存して、あとで高速に検索できるようにする部分です。

【注意点・例外】
★抽出したエンティティの表記揺れや重複名寄せをどうするか？
エンティティのリストを運用しながら更新していって、このリストを正として表記揺れを正したり、重複はまとめたりする？
→ここは考えて実装してください。

**LLMは「読む・抜き出す・構造化する」役割。**
**HelixDBは「保存する・つなぐ・検索する」役割。**
