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
    phraseIntro: "Đã nhập nhiều hơn một từ - đây là nghĩa ngắn gọn của từng từ.",
    phraseNotFound: "không có trong từ điển",
    phraseMoreSensesTemplate: "+{count} nghĩa khác",
    phraseWordError: "Chưa tra được từ này, thử bấm lại.",
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

  // Chặn gửi trùng: bấm đúp hoặc chuột bị "click chatter" (lỗi phần cứng khá phổ biến,
  // một cú bấm sinh hai sự kiện click) gửi hai yêu cầu giống hệt nhau cách nhau vài chục
  // mili-giây - đo thật bằng cách mô phỏng 2 click liên tiếp: 86ms. Server vẫn xử lý đúng
  // cả hai (không có gì hỏng), chỉ là lịch sử tra cứu bị ghi trùng dòng. Không phân biệt
  // được với "muốn tra lại đúng từ đó ngay lập tức", nhưng tình huống đó cực hiếm trong
  // chưa đầy 1 giây.
  var DUPLICATE_GUARD_MS = 700;
  var lastSubmitted = { key: null, time: 0 };
  function isDuplicateSubmit(key) {
    var now = Date.now();
    if (lastSubmitted.key === key && now - lastSubmitted.time < DUPLICATE_GUARD_MS) return true;
    lastSubmitted = { key: key, time: now };
    return false;
  }

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

  // dpdict.net chỉ hiểu MỘT từ khoá mỗi lượt tra (đã kiểm chứng: gõ thẳng một câu vào ô
  // tìm của chính họ cũng ra rỗng). Nên nhiều hơn một từ phải rẽ sang tra theo câu.
  function isPhrase(value) {
    return value.trim().split(/\s+/).filter(Boolean).length > 1;
  }

  function submit() {
    var value = input.value.trim();
    if (!value) return;
    if (isPhrase(value)) lookupPhrase(value);
    else lookup(value);
  }

  function lookup(word, skipHistory) {
    // Chặn ở cửa vào: mọi lối gọi tới đây đều ghi thẳng `word` vào ô nhập, nên một giá trị
    // không phải chuỗi sẽ biến thành từ khoá "undefined"/"null" và đem đi tra thật.
    if (typeof word !== "string" || !word.trim()) return;
    word = word.trim();
    if (isDuplicateSubmit("word:" + word)) return;

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

    // Ẩn mục "Tóm tắt" theo yêu cầu: nội dung trùng lặp với phần chi tiết bên dưới.
    // if (data.summaryHtml) {
    //   var summary = document.createElement("section");
    //   summary.className = "dictPanel dictSummary";
    //   var heading = document.createElement("h2");
    //   heading.textContent = strings.summaryTitle;
    //   summary.appendChild(heading);
    //   var body = document.createElement("div");
    //   body.innerHTML = data.summaryHtml;
    //   summary.appendChild(body);
    //   results.appendChild(summary);
    // }

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

  /* -------------------------------------------------- tra nguyên câu/dòng kệ */

  function lookupPhrase(text) {
    if (isDuplicateSubmit("phrase:" + text)) return;

    var mine = ++requestId;
    hideSuggestions();
    input.value = text;
    status(strings.searching);

    var url =
      "/api/dictionary/lookup-phrase?q=" + encodeURIComponent(text) + "&lang=" + encodeURIComponent(lang);
    fetch(url)
      .then(function (res) {
        if (!res.ok) throw new Error("lookup-phrase " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (mine !== requestId) return;
        renderPhrase(data);
        // Không ghi vào "Đã tra gần đây": danh sách đó là để tra nhanh lại MỘT từ, còn
        // đây là cả câu - ghi vào sẽ khiến người dùng bấm nhầm tưởng tra được nguyên câu.
      })
      .catch(function () {
        if (mine !== requestId) return;
        status(strings.error, true);
      });

    var target = "/dictionary?q=" + encodeURIComponent(text);
    if (window.location.pathname + window.location.search !== target) {
      window.history.pushState({ q: text }, "", target);
    }
  }

  function renderPhrase(data) {
    results.innerHTML = "";
    if (!data.words || !data.words.length) {
      status(strings.empty);
      return;
    }

    var wrap = document.createElement("section");
    wrap.className = "dictPanel dictPhrase";

    var intro = document.createElement("p");
    intro.className = "dictPhraseIntro";
    intro.textContent = strings.phraseIntro;
    wrap.appendChild(intro);

    var list = document.createElement("div");
    list.className = "dictPhraseWords";

    // MỘT khối chi tiết dùng chung cho cả câu, đặt cố định ngay dưới danh sách từ - không
    // phải mỗi từ một khối riêng chèn ngay sau nó. Chèn riêng từng chỗ sẽ đẩy các từ phía
    // sau xuống xa (một mục từ như "sabbe" có thể dài cả nghìn pixel vì kèm bảng biến
    // cách/tần suất), làm đứt mạch đọc của cả câu.
    var detail = document.createElement("div");
    detail.className = "dictPhraseDetail hidden";
    var cache = Object.create(null); // surface -> node đã dựng sẵn, tránh gọi lại API

    data.words.forEach(function (word) {
      list.appendChild(buildPhraseWord(word, data.bilingual, detail, cache));
    });
    wrap.appendChild(list);
    wrap.appendChild(detail);

    results.appendChild(wrap);
  }

  function buildPhraseWord(word, bilingual, detail, cache) {
    var canExpand = word.found || word.hasEntries;

    var wrap = document.createElement("div");
    wrap.className = "dictPhraseWord" + (canExpand ? "" : " notFound");

    var row = document.createElement("button");
    row.type = "button";
    row.className = "dictPhraseWordButton";
    if (!canExpand) row.disabled = true;

    var surface = document.createElement("span");
    surface.className = "dictPhraseSurface";
    surface.textContent = word.surface || "";
    row.appendChild(surface);

    var gloss = document.createElement("span");
    gloss.className = "dictPhraseGloss";
    if (word.found) {
      gloss.textContent = formatGrammar((bilingual && word.translation) ? word.translation : word.meaningText);
    } else if (word.error) {
      gloss.textContent = strings.phraseWordError;
    } else {
      gloss.classList.add("dictPhraseMissing");
      gloss.textContent = strings.phraseNotFound;
    }
    row.appendChild(gloss);

    if (word.senseCount > 1) {
      var more = document.createElement("span");
      more.className = "dictPhraseMore";
      more.textContent = strings.phraseMoreSensesTemplate.replace("{count}", word.senseCount - 1);
      row.appendChild(more);
    }
    wrap.appendChild(row);

    if (canExpand) {
      row.addEventListener("click", function () {
        var reopening = row.classList.contains("active") && !detail.classList.contains("hidden");
        // Các nút nằm trong `.dictPhraseWords`, không phải trong `detail` (khối chi tiết
        // dùng chung nằm riêng, phía dưới danh sách) - phải tìm đúng chỗ.
        row.closest(".dictPhraseWords").querySelectorAll(".dictPhraseWordButton.active").forEach(function (el) {
          el.classList.remove("active");
        });
        if (reopening) {
          // Bấm lại đúng từ đang mở: đóng lại, không tải gì thêm.
          detail.classList.add("hidden");
          row.classList.remove("active");
          return;
        }
        row.classList.add("active");
        detail.classList.remove("hidden");
        showPhraseWordDetail(word.surface, detail, cache);
        detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    }
    return wrap;
  }

  function showPhraseWordDetail(surface, detail, cache) {
    if (cache[surface]) {
      detail.innerHTML = "";
      detail.appendChild(cache[surface]);
      return;
    }
    detail.innerHTML = "";
    var loading = document.createElement("p");
    loading.className = "dictStatus";
    loading.textContent = strings.searching;
    detail.appendChild(loading);

    var url =
      "/api/dictionary/lookup?q=" + encodeURIComponent(surface) + "&lang=" + encodeURIComponent(lang);
    fetch(url)
      .then(function (res) {
        if (!res.ok) throw new Error("lookup " + res.status);
        return res.json();
      })
      .then(function (data) {
        var built = document.createElement("div");
        if (!data.found || !data.entries.length) {
          var empty = document.createElement("p");
          empty.className = "dictStatus";
          empty.textContent = strings.empty;
          built.appendChild(empty);
        } else {
          data.entries.forEach(function (entry) {
            built.appendChild(buildEntry(entry, data.bilingual));
          });
        }
        cache[surface] = built;
        // Người dùng có thể đã bấm sang từ khác trong lúc chờ - chỉ vẽ nếu khối chi tiết
        // vẫn đang hiện đúng từ này.
        if (detail.dataset.pending === surface || !detail.dataset.pending) {
          detail.innerHTML = "";
          detail.appendChild(built);
        }
      })
      .catch(function () {
        detail.innerHTML = "";
        var errorMsg = document.createElement("p");
        errorMsg.className = "dictStatus error";
        errorMsg.textContent = strings.error;
        detail.appendChild(errorMsg);
      });
    detail.dataset.pending = surface;
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
        viText.textContent = formatGrammar(entry.translation);
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

  function formatGrammar(text) {
    if (!text) return text;
    // Chuyển định dạng viết tắt ngữ pháp/giới tính ở đầu (vd: masc., giống đực., danh từ.) vào ngoặc đơn
    return text.replace(/^([\p{L}\s/-]{2,30})[.:]\s+/iu, function(match, p1) {
      var lower = p1.toLowerCase();
      var isGrammar = /masc|fem|nt|adj|pron|adv|verb|part|conj|interj|prep|prefix|suffix|idiom|sandhi|ptp|ppr|aor|pass|grd|caus|denom|desid|giống|từ|ngữ|tố/i.test(lower);
      if (isGrammar || p1.trim().length <= 8) {
        return '(' + p1.trim() + ') ';
      }
      return match;
    });
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
    var value = (event.state && event.state.q) || "";
    if (!value) {
      input.value = "";
      results.innerHTML = "";
    } else if (isPhrase(value)) {
      lookupPhrase(value);
    } else {
      lookup(value, true);
    }
  });

  renderHistory();
  if (root.dataset.initialQuery) {
    if (isPhrase(root.dataset.initialQuery)) lookupPhrase(root.dataset.initialQuery);
    else lookup(root.dataset.initialQuery, true);
  } else {
    input.focus();
  }
})();
