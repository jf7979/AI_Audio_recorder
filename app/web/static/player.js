(function () {
  "use strict";

  function seekAndPlay(audio, seconds) {
    function doSeek() {
      audio.currentTime = seconds;
      audio.play().catch(function () { /* autoplay may be blocked; that's fine */ });
    }
    // Setting currentTime before metadata (duration/seekability) is loaded is
    // unreliable across browsers - wait for it when starting from a cold <audio>.
    if (audio.readyState >= 1) {
      doSeek();
    } else {
      audio.addEventListener("loadedmetadata", doSeek, { once: true });
    }
  }

  // Click a transcript word to jump the recording's own audio there.
  document.addEventListener("click", function (e) {
    const word = e.target.closest(".word");
    if (!word) return;
    const recording = word.closest(".recording");
    const audio = recording && recording.querySelector("audio");
    if (!audio) return;
    seekAndPlay(audio, parseFloat(word.dataset.start || "0"));
  });

  // Flags page: toggle done state without a full page reload.
  document.addEventListener("change", function (e) {
    const checkbox = e.target.closest(".flag-checkbox");
    if (!checkbox) return;
    const url = checkbox.dataset.toggleUrl;
    const snippet = checkbox.closest("label").querySelector(".flag-snippet");
    fetch(url, { method: "POST" })
      .then((r) => r.json())
      .then((data) => {
        if (data.ok) {
          snippet.classList.toggle("done", data.is_done);
        }
      })
      .catch(() => {
        checkbox.checked = !checkbox.checked;
      });
  });

  // Arriving from a search result: ?recording=<id>&t=<seconds> seeks that clip.
  document.addEventListener("DOMContentLoaded", function () {
    const params = new URLSearchParams(window.location.search);
    const recordingId = params.get("recording");
    if (!recordingId) return;
    const el = document.getElementById("recording-" + recordingId);
    if (!el) return;
    const audio = el.querySelector("audio");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    if (audio) {
      seekAndPlay(audio, parseFloat(params.get("t") || "0"));
    }
  });
})();
