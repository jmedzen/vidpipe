// 必須在第一次繪製之前跑，否則淺色主題會先閃一下深色。
// 放在獨立檔案而不是 inline，這樣外殼的 CSP 才能維持 script-src 'self'。
(function () {
  try {
    var t = localStorage.getItem('vidpipe.theme');
    if (!t && window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) t = 'paper';
    t = t || 'midnight';
    document.documentElement.dataset.theme = t;
    var light = ['paper', 'mist', 'sakura'].indexOf(t) >= 0;
    document.documentElement.style.colorScheme = light ? 'light' : 'dark';
  } catch (e) {}
})();
