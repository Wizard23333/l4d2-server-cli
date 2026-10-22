(function () {
  var theme = 'dark';
  try { if (localStorage.getItem('l4d2-theme') === 'light') theme = 'light'; } catch (_) {}
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.backgroundColor = theme === 'light' ? '#f3f5f7' : '#0c0f12';
})();
