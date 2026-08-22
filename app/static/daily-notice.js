(function () {
  const STORAGE_KEY = "tipitaka.noticeSeen";

  function localDateKey(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function setupDailyNotice(modal) {
    if (!modal) return;
    const version = modal.dataset.noticeVersion || "1";
    const seenValue = `${version}:${localDateKey(new Date())}`;
    let seen = null;
    try {
      seen = window.localStorage.getItem(STORAGE_KEY);
    } catch (_error) {
      // Nếu storage bị chặn, thông báo sẽ hiện lại ở lần truy cập sau.
    }
    if (seen === seenValue) return;

    modal.classList.remove("hidden");
    document.body.classList.add("modalOpen");
    const dismiss = () => {
      modal.classList.add("hidden");
      document.body.classList.remove("modalOpen");
      try {
        window.localStorage.setItem(STORAGE_KEY, seenValue);
      } catch (_error) {
        // Vẫn đóng được thông báo trong phiên hiện tại khi storage không khả dụng.
      }
    };

    modal.querySelector("#noticeClose")?.addEventListener("click", dismiss);
    modal.addEventListener("click", (event) => {
      if (event.target === modal) dismiss();
    });
  }

  window.setupDailyNotice = setupDailyNotice;
})();
