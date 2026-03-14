#!/bin/sh

# 設定: 自分のリポジトリに合わせて書き換えてください
USER="htvoffcial"
REPO="htvoffcial"
BRANCH="main"
FILE_PATH="DiscussArchive.md"
RAW_URL="https://raw.githubusercontent.com/$USER/$REPO/$BRANCH/$FILE_PATH"

# 色設定
ORANGE='\033[38;5;208m'
GREEN='\033[38;5;118m'
RESET='\033[0m'
CLEAR='\033[H\033[J'

# 表示幅（電光掲示板の「窓」のサイズ）
WINDOW_WIDTH=30

while true; do
    # GitHub Raw から最新を取得 (キャッシュ対策でランダムなクエリ付与)
    TEMP_FILE="/tmp/discuss_raw.md"
    curl -sL "${RAW_URL}?nocache=$(date +%s)" -o "$TEMP_FILE"

    # ファイル解析
    current_date="--/--"
    grep -E "^## |^- \[" "$TEMP_FILE" | while read -r line; do
        if echo "$line" | grep -q "^## "; then
            # 日付抽出 (2026-03-10 -> 03/10)
            current_date=$(echo "$line" | sed 's/## [0-9]\{4\}-\([0-9]\{2\}\)-\([0-9]\{2\}\)/\1\/\2/')
        else
            # タイトル抽出
            title=$(echo "$line" | sed 's/- \[\(.*\)\](.*/\1/')
            
            # --- スクロール処理 ---
            # タイトルの長さを取得（マルチバイト文字考慮なしの簡易版）
            len=$(echo "$title" | wc -c)
            
            # 3秒間、文字を流す演出
            # 短い場合はそのまま表示、長い場合はスライド
            i=0
            display_count=0
            while [ $display_count -lt 15 ]; do # 約3秒間（0.2s * 15）
                printf "$CLEAR"
                echo "種別    日付    内容"
                echo "------------------------------------------"
                
                # 文字列を切り出し (1文字ずつずらす)
                # ${string:start:len} は bash 特有なので、cut を使用して sh 互換に
                visible_text=$(echo "$title" | cut -c "$((i + 1))-$((i + WINDOW_WIDTH))")
                
                printf "${ORANGE}会話    ${current_date}    ${GREEN}${visible_text}${RESET}\n"
                
                # スクロール位置の更新
                if [ "$len" -gt "$WINDOW_WIDTH" ]; then
                    i=$(( (i + 1) % (len - 5) )) # 少し余裕を持ってループ
                fi
                
                display_count=$((display_count + 1))
                sleep 0.2
            done
        fi
    done
    sleep 1
done
