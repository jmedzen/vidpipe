你是一位 YouTube 頻道的編輯。請為下面這支佛法{{format_name}}準備上架資料,只輸出一個 JSON 物件。

# 腳本
```json
{{script}}
```

# 要求
- 繁體中文、台灣用語。不聳動、不騙點擊、不保證任何效果、沒有宗教勸誘。
- `titles`:3 個標題候選,每個 20 個字以內(不含標籤),第一個就是影片的常駐標題或它的變化。可以是問題或反直覺的一句話。
- `summary`:一到兩句話說這支影片在講什麼。
- `practice`:影片裡的小練習,用一句話寫給觀眾。
- `sources_text`:出處說明,照腳本的 sources 寫,意譯的要寫「意譯」。不得增加腳本沒有的出處。
- `tags`:8 到 12 個標籤(不加 # 號),含主題詞與一般搜尋詞。
- `hashtags`:3 個放在標題後面的標籤(含 # 號){{shorts_hint}}。
- `pinned_comment`:置頂留言草稿,一到兩句,用一個開放式問題邀請觀眾分享,不要求訂閱。

# 輸出格式
{"titles": ["", "", ""], "summary": "", "practice": "", "sources_text": "", "tags": [], "hashtags": [], "pinned_comment": ""}
