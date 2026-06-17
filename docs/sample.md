# State-Aware RAG サンプル

State-Aware RAG は、ユーザーの質問ごとに作業用メモを作り、検索で得た根拠を短い事実メモに変換してから回答する方式です。

検索では、ベクトル検索、全文検索、グラフ探索を組み合わせます。採用された候補だけが Evidence として保存され、Evidence から MemoryNote が作られます。

最終回答では、検索結果の原文を直接使わず、WorkingMemory に残った MemoryNote だけを根拠にします。
