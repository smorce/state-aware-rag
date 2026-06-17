uv add budoux

import budoux

parser = budoux.load_default_japanese_parser()

def tokenize_jp_content(text: str) -> str:
    return " ".join(parser.parse(text))

   
tokenize_jp_content("今日は海老名駅に行った。")
#→ 今日は 海老名駅に 行った。


意味不明なところで切れないように、チャンクは自然な句読点で終わるべきです。