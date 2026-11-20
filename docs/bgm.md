# 背景音樂：自己的曲庫，以及去哪裡找可以合法使用的音樂

vidpipe 不會自己下載音樂。曲庫裡只有你放進去的檔案，所以「能不能用」由你決定。這份說明整理了常見的來源與授權要注意的地方。

## 怎麼用

1. 打開網頁的「背景音樂」頁，按「建立資料夾」（預設是 vidpipe 目錄下的 `bgm/`，在「設定 → 聲音」可以換）。
2. 把音檔（mp3、m4a、aac、wav、flac、ogg、opus）放進去。**子資料夾名稱就是標籤**：

   ```
   bgm/
     古琴/   流水.mp3  平沙落雁.mp3
     笛簫/   竹林晨風.mp3
     頌缽/   …
     明亮/   …
     _停用/  …        ← 底線開頭的資料夾不會被挑到
   ```

3. 回到「背景音樂」頁按「重新掃描」，就能試聽。按「編輯」填授權、作者與標註文字，也能多加標籤、調起點與音量、暫停使用某首。
   這些資料存成音檔旁邊的同名 `.json`。
4. 出片時：
   - 文案直出勾「放背景音樂」，「音樂類型」選全部輪流或某個標籤。
   - 指令列：`vidpipe quick 文案.txt --music 古琴`（`古琴,笛簫` 是任一個都可以）；批次 CSV 的 `music` 欄也可以寫標籤。
   - studio：`pipeline.json` 的 `music.tags`，例如 `"tags": ["古琴", "笛簫"]`。
5. **輪流**：每支片挑同一類裡「最久沒用過」的那首，所以曲庫有 10 首，連續 10 支片都不會重複。同一支片重做會拿到同一首。
   輪替紀錄在 `bgm/.vidpipe/history.json`，網頁和 studio 共用。
6. 曲庫是空的時候，照舊從 vidgrab 的音樂庫找（CC0／CC BY）。

## 授權欄位怎麼填

| 選項 | 意思 | 成片的 credits |
|---|---|---|
| 免標註（`free`） | CC0、公有領域、Pixabay 這類「不必署名」的授權 | 列出，不打 ⚠ |
| 要標註（`free-attribution`） | CC BY 之類，**影片說明一定要寫作者與出處** | 列出並標「必須標註」，標註文字會放進說明欄範本 |
| 自己做的（`own`） | 你自己作曲、錄音的 | 列出 |
| 未標示 | 還沒確認 | 打 ⚠，`warnings` 提醒你公開前確認 |

**避開**：CC BY-NC（非商業：有營利的頻道不能用）、CC BY-ND（禁止改作：剪接、壓低音量都可能算改作）、
寫著「僅供個人使用」或來源不明的「無版權音樂」合輯。

## 去哪裡找（授權會改，下載前到原網站再看一次）

| 來源 | 授權重點 | 適合找 |
|---|---|---|
| [YouTube 音效庫](https://support.google.com/youtube/answer/3376882)（YouTube 工作室 → 音效庫） | 免費；每首會註明需不需要標註，可以篩「不需要標註」。條款主要針對 YouTube 上的影片 | 冥想、氛圍、鋼琴；可用「心情」篩平靜、溫暖 |
| [Pixabay Music](https://pixabay.com/music/)（[授權說明](https://pixabay.com/service/license-summary/)） | Pixabay Content License：可商用、不必標註；不能把音樂本身單獨轉賣。少數曲子註冊了 Content ID，被認領時照網站說明申訴 | 搜 meditation、zen、guzheng、bamboo flute、singing bowl |
| [Free Music Archive](https://freemusicarchive.org/) | 每首授權不同：只挑 CC BY 或 CC0 | 實驗、氛圍、民族風 |
| [Musopen](https://musopen.org/music/) | 古典樂錄音，多數是公有領域（每首確認） | 鋼琴、弦樂的慢板 |
| [Incompetech](https://incompetech.com/music/royalty-free/music.html)（Kevin MacLeod） | CC BY 4.0，要標註作者 | 氛圍、東方風格 |
| 自己錄 | 木魚、磬、頌缽、環境音（流水、鳥鳴），授權最單純 | 填 `own` |

## 風格方向（舒緩、不憂鬱）

挑歌時可以先決定一兩個方向，各放 5–10 首進同名資料夾：

1. **古琴／古箏・明亮調式**：五聲音階、大調感，像〈流水〉〈漁舟唱晚〉的氣質。搜：guqin、guzheng peaceful、chinese zen。
2. **笛簫・竹林晨風**：竹笛或簫配環境音，空靈但不沉重。搜：bamboo flute meditation、dizi relaxing。
3. **頌缽、鐘聲、環境音**：幾乎沒有旋律，旁白最清楚。搜：singing bowl、temple bell ambience、nature sounds stream。
4. **溫暖鋼琴、Lo-fi**：大調、慢速、溫柔，較現代。搜：warm piano calm、lofi chill meditation。
5. **梵唄、佛曲演奏版**：法味最濃；注意很多是有版權的錄音，只用標明 CC0／CC BY 或自己取得授權的。

想避開「憂鬱」感：選大調、速度不要太慢、少用低音大提琴與長小調和弦；試聽時注意前 10 秒，因為 Shorts 最常只聽到開頭
（可以在「編輯」裡用「從第幾秒放」跳過前奏）。
