/* Trang tra cứu từ điển Pāḷi.
 *
 * Ba việc: gợi ý khi gõ, gọi tra cứu, và làm sống lại các nút của DPD.
 *
 * Việc thứ ba dễ bị bỏ sót: HTML do dpdict.net trả về có sẵn nút "grammar", "examples",
 * "declension"... trỏ tới khối ẩn bằng `data-target`, NHƯNG phần JS xử lý chúng nằm trong
 * trang của họ, không đi kèm dữ liệu. Không nối lại thì các nút đó bấm không ra gì.
 */
(function () {
  "use strict";

  var root = document.getElementById("dictPage");
  if (!root) return;

  // Nhãn giao diện do máy chủ nhúng vào `data-strings`. Đọc phòng hờ: nếu thuộc tính hỏng
  // hoặc thiếu một khoá, `strings.x` sẽ là `undefined` và JS in thẳng chữ "undefined" ra
  // màn hình người đọc. Thà hiện nhãn tiếng Việt mặc định còn hơn.
  var FALLBACK = {
    summaryTitle: "Tóm tắt",
    viTitle: "Bản dịch",
    enTitle: "Bản tiếng Anh gốc",
    searching: "Đang tra cứu...",
    empty: "Không tìm thấy từ này trong từ điển.",
    error: "Chưa kết nối được tới từ điển DPD. Vui lòng thử lại sau ít phút.",
    translationMissing: "Chưa dịch được phần này.",
    historyEmpty: "Chưa tra từ nào.",
    credit: "Nguồn: Digital Pāḷi Dictionary (dpdict.net), giấy phép CC BY-NC-SA.",
    openInDpd: "Xem trên dpdict.net",
  };
  var strings = (function () {
    var parsed = {};
    try {
      parsed = JSON.parse(root.dataset.strings || "{}") || {};
    } catch (error) {
      parsed = {};
    }
    Object.keys(FALLBACK).forEach(function (key) {
      if (typeof parsed[key] !== "string" || !parsed[key]) parsed[key] = FALLBACK[key];
    });
    return parsed;
  })();
  var lang = root.dataset.lang || "vi";

  var input = document.getElementById("dictInput");
  var suggestList = document.getElementById("dictSuggest");
  var results = document.getElementById("dictResults");
  var historyList = document.getElementById("dictHistoryList");
  var historyClear = document.getElementById("dictHistoryClear");

  var HISTORY_KEY = "tipitaka.dictHistory";
  var HISTORY_MAX = 25;
  var SUGGEST_DELAY_MS = 120;

  var suggestTimer = null;
  var activeIndex = -1;
  var currentWords = [];
  // Mỗi lượt tra mang một số thứ tự: người dùng gõ nhanh có thể bắn nhiều yêu cầu, và
  // yêu cầu cũ về SAU yêu cầu mới thì sẽ đè kết quả đúng bằng kết quả cũ.
  var requestId = 0;

  /* ------------------------------------------------------------ gợi ý khi gõ */

  function hideSuggestions() {
    suggestList.hidden = true;
    suggestList.innerHTML = "";
    currentWords = [];
    activeIndex = -1;
  }

  function renderSuggestions(words) {
    currentWords = words;
    activeIndex = -1;
    if (!words.length) {
      hideSuggestions();
      return;
    }
    suggestList.innerHTML = "";
    words.forEach(function (word, index) {
      var item = document.createElement("li");
      item.textContent = word;
      item.setAttribute("role", "option");
      item.addEventListener("mousedown", function (event) {
        // mousedown chứ không phải click: blur của ô nhập xảy ra trước click và đã đóng
        // danh sách, nên handler click không bao giờ chạy.
        event.preventDefault();
        choose(index);
      });
      suggestList.appendChild(item);
    });
    suggestList.hidden = false;
  }

  function highlight(index) {
    var items = suggestList.querySelectorAll("li");
    items.forEach(function (item) {
      item.setAttribute("aria-selected", "false");
    });
    if (index >= 0 && index < items.length) {
      items[index].setAttribute("aria-selected", "true");
      items[index].scrollIntoView({ block: "nearest" });
    }
    activeIndex = index;
  }

  function choose(index) {
    if (index < 0 || index >= currentWords.length) return;
    // Giữ từ ra biến TRƯỚC khi gọi hideSuggestions(): hàm đó đặt `currentWords = []`, nên
    // đọc `currentWords[index]` sau nó sẽ ra `undefined`, và `lookup` lấy đúng chữ
    // "undefined" đó làm từ khoá. Lỗi này khó thấy vì dpdict.net tra ngược cả tiếng Anh,
    // nên "undefined" vẫn trả về mục từ trông như thật.
    var word = currentWords[index];
    hideSuggestions();
    lookup(word);
  }

  function fetchSuggestions(prefix) {
    fetch("/api/dictionary/suggest?q=" + encodeURIComponent(prefix))
      .then(function (res) {
        // Trình xử lý lỗi của ứng dụng vẫn trả JSON hợp lệ, nên không kiểm tra `res.ok`
        // thì lỗi sẽ đi tiếp và hỏng ở chỗ khác, xa nguyên nhân.
        if (!res.ok) throw new Error("suggest " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (input.value.trim() !== prefix) return; // người dùng đã gõ tiếp
        renderSuggestions(data.words || []);
      })
      .catch(function () {
        hideSuggestions();
      });
  }

  input.addEventListener("input", function () {
    var value = input.value.trim();
    window.clearTimeout(suggestTimer);
    if (!value) {
      hideSuggestions();
      return;
    }
    suggestTimer = window.setTimeout(function () {
      fetchSuggestions(value);
    }, SUGGEST_DELAY_MS);
  });

  input.addEventListener("keydown", function (event) {
    if (suggestList.hidden) {
      if (event.key === "Enter") submit();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      highlight(Math.min(activeIndex + 1, currentWords.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      highlight(Math.max(activeIndex - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (activeIndex >= 0) choose(activeIndex);
      else submit();
    } else if (event.key === "Escape") {
      hideSuggestions();
    }
  });

  input.addEventListener("blur", function () {
    window.setTimeout(hideSuggestions, 120);
  });

  /* ---------------------------------------------------------------- tra cứu */

  function status(message, isError) {
    results.innerHTML = "";
    var paragraph = document.createElement("p");
    paragraph.className = isError ? "dictStatus error" : "dictStatus";
    paragraph.textContent = message;
    results.appendChild(paragraph);
  }

  function submit() {
    var value = input.value.trim();
    if (value) lookup(value);
  }

  function lookup(word, skipHistory) {
    // Chặn ở cửa vào: mọi lối gọi tới đây đều ghi thẳng `word` vào ô nhập, nên một giá trị
    // không phải chuỗi sẽ biến thành từ khoá "undefined"/"null" và đem đi tra thật.
    if (typeof word !== "string" || !word.trim()) return;
    word = word.trim();

    var mine = ++requestId;
    hideSuggestions();
    input.value = word;
    status(strings.searching);

    var url =
      "/api/dictionary/lookup?q=" + encodeURIComponent(word) + "&lang=" + encodeURIComponent(lang);
    fetch(url)
      .then(function (res) {
        if (!res.ok) throw new Error("lookup " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (mine !== requestId) return;
        render(data);
        if (!skipHistory && data.found) remember(word);
      })
      .catch(function () {
        if (mine !== requestId) return;
        status(strings.error, true);
      });

    var target = "/dictionary?q=" + encodeURIComponent(word);
    if (window.location.pathname + window.location.search !== target) {
      window.history.pushState({ q: word }, "", target);
    }
  }

  function render(data) {
    results.innerHTML = "";
    if (!data.found || !data.entries.length) {
      status(strings.empty);
      return;
    }

    if (data.summaryHtml) {
      var summary = document.createElement("section");
      summary.className = "dictPanel dictSummary";
      var heading = document.createElement("h2");
      heading.textContent = strings.summaryTitle;
      summary.appendChild(heading);
      var body = document.createElement("div");
      body.innerHTML = data.summaryHtml;
      summary.appendChild(body);
      results.appendChild(summary);
    }

    data.entries.forEach(function (entry) {
      results.appendChild(buildEntry(entry, data.bilingual));
    });

    var credit = document.createElement("p");
    credit.className = "dictCredit";
    credit.textContent = strings.credit + " ";
    var link = document.createElement("a");
    link.href = data.sourceUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = strings.openInDpd;
    credit.appendChild(link);
    results.appendChild(credit);
  }

  function buildEntry(entry, bilingual) {
    var article = document.createElement("article");
    article.className = "dictEntry";

    var heading = document.createElement("h3");
    heading.className = "dpd";
    // Gán id qua thuộc tính DOM chứ không ghép chuỗi HTML: id là chữ Pāḷi lấy từ dữ liệu
    // ngoài và có cả dấu cách lẫn dấu hai chấm ("grammar: bhagavatā").
    heading.id = entry.anchor || "";
    // `textContent = undefined` in ra đúng chữ "undefined", nên phải chặn ở đây.
    heading.textContent = entry.title || entry.anchor || "";
    article.appendChild(heading);

    if (entry.meaningHtml && !bilingual) {
      // Giao diện tiếng Anh: nghĩa của DPD vốn đã là tiếng Anh nên chỉ một khối, không
      // gắn nhãn "bản dịch"/"nguyên bản" cho hai thứ giống hệt nhau.
      var only = document.createElement("div");
      only.innerHTML = entry.meaningHtml;
      article.appendChild(only);
    } else if (entry.meaningHtml) {
      // Tiếng Việt ở TRÊN, tiếng Anh gốc ở DƯỚI - đúng thứ tự khách yêu cầu.
      var viBox = document.createElement("div");
      viBox.className = "dictVi";
      viBox.appendChild(label(strings.viTitle));
      if (entry.translation) {
        var viCard = document.createElement("div");
        viCard.className = "dpd summary";
        var viText = document.createElement("p");
        // textContent: bản dịch là văn bản do AI sinh ra, không phải HTML để chèn thẳng.
        viText.textContent = entry.translation;
        viCard.appendChild(viText);
        viBox.appendChild(viCard);
      } else {
        var missing = document.createElement("p");
        missing.className = "dictNoTranslation";
        missing.textContent = strings.translationMissing;
        viBox.appendChild(missing);
      }
      article.appendChild(viBox);

      var enBox = document.createElement("div");
      enBox.className = "dictEn";
      enBox.appendChild(label(strings.enTitle));
      var enCard = document.createElement("div");
      enCard.innerHTML = entry.meaningHtml;
      enBox.appendChild(enCard);
      article.appendChild(enBox);
    }

    if (entry.bodyHtml) {
      var body = document.createElement("div");
      body.innerHTML = entry.bodyHtml;
      article.appendChild(body);
    }
    return article;
  }

  function label(text) {
    var element = document.createElement("p");
    element.className = "dictBoxLabel";
    element.textContent = text;
    return element;
  }

  /* ------------------------------------------- nút của DPD và liên kết tóm tắt */

  results.addEventListener("click", function (event) {
    var button = event.target.closest("a.dpd-button");
    if (button) {
      var targetId = button.getAttribute("data-target");
      if (targetId) {
        event.preventDefault();
        var block = document.getElementById(targetId);
        if (block) {
          var opening = block.classList.contains("hidden");
          block.classList.toggle("hidden");
          button.classList.toggle("active", opening);
        }
        return;
      }
      if (button.classList.contains("play")) {
        event.preventDefault();
        playAudio(button.getAttribute("data-headword"));
        return;
      }
    }

    // Liên kết trong khối "Tóm tắt" trỏ tới "#grammar: bhagavatā". Trình duyệt không tự
    // cuộn tới id có dấu cách/hai chấm, nên tự tìm bằng getElementById rồi cuộn.
    var jump = event.target.closest('a[href^="#"]');
    if (jump) {
      var anchor = decodeURIComponent(jump.getAttribute("href").slice(1));
      var element = anchor ? document.getElementById(anchor) : null;
      if (element) {
        event.preventDefault();
        element.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  });

  function playAudio(headword) {
    if (!headword) return;
    var audio = new Audio(
      "https://www.dpdict.net/audio/" + encodeURIComponent(headword)
    );
    audio.play().catch(function () {
      /* Không phát được (chặn tự động phát, hoặc từ này không có file) - bỏ qua im lặng,
         phát âm chỉ là phần phụ, không đáng để bắn báo lỗi che mất phần nghĩa. */
    });
  }

  /* ------------------------------------------------------------- đã tra gần đây */

  function readHistory() {
    try {
      var raw = window.localStorage.getItem(HISTORY_KEY);
      var parsed = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(parsed)) return [];
      // Lọc rác: lỗi `choose()` trước đây đã ghi `undefined` vào danh sách của những người
      // đã dùng trang, và `JSON.stringify` biến nó thành `null`. Lọc ở đây để máy họ tự
      // sạch khi mở lại, không cần làm gì thêm.
      return parsed.filter(function (word) {
        return typeof word === "string" && word.trim() && word !== "undefined";
      });
    } catch (error) {
      return [];
    }
  }

  function writeHistory(words) {
    try {
      window.localStorage.setItem(HISTORY_KEY, JSON.stringify(words));
    } catch (error) {
      /* Trình duyệt ẩn danh hoặc bộ nhớ đầy - danh sách chỉ là tiện ích, không chặn tra cứu. */
    }
  }

  function remember(word) {
    var words = readHistory().filter(function (item) {
      return item !== word;
    });
    words.unshift(word);
    writeHistory(words.slice(0, HISTORY_MAX));
    renderHistory();
  }

  function renderHistory() {
    var words = readHistory();
    historyList.innerHTML = "";
    historyClear.hidden = !words.length;
    if (!words.length) {
      var empty = document.createElement("p");
      empty.className = "dictHistoryEmpty";
      empty.textContent = strings.historyEmpty;
      historyList.appendChild(empty);
      return;
    }
    var list = document.createElement("ul");
    words.forEach(function (word) {
      var item = document.createElement("li");
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = word;
      button.addEventListener("click", function () {
        lookup(word);
      });
      item.appendChild(button);
      list.appendChild(item);
    });
    historyList.appendChild(list);
  }

  historyClear.addEventListener("click", function () {
    writeHistory([]);
    renderHistory();
  });

  /* ------------------------------------------------------------------- khởi động */

  document.getElementById("dictSubmit").addEventListener("click", submit);
  document.getElementById("dictClear").addEventListener("click", function () {
    input.value = "";
    hideSuggestions();
    results.innerHTML = "";
    requestId += 1; // huỷ kết quả của lượt tra đang chờ
    input.focus();
  });

  window.addEventListener("popstate", function (event) {
    var word = (event.state && event.state.q) || "";
    if (word) lookup(word, true);
    else {
      input.value = "";
      results.innerHTML = "";
    }
  });

  renderHistory();
  if (root.dataset.initialQuery) {
    lookup(root.dataset.initialQuery, true);
  } else {
    input.focus();
  }
})();
