(function () {
  "use strict";

  function isLightboxCandidate(img) {
    if (!img || !img.src) return false;
    if (img.closest(".os-lightbox")) return false;
    if (img.closest(".no-lightbox")) return false;
    if (img.closest("a")) return false;
    if (img.closest(".wy-side-nav-search")) return false;
    if (img.classList.contains("logo")) return false;
    return true;
  }

  function createLightbox() {
    var overlay = document.createElement("div");
    overlay.className = "os-lightbox";
    overlay.setAttribute("aria-hidden", "true");

    var closeButton = document.createElement("button");
    closeButton.className = "os-lightbox-close";
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", "Close image");
    closeButton.textContent = "×";

    var image = document.createElement("img");
    image.alt = "";

    overlay.appendChild(closeButton);
    overlay.appendChild(image);
    document.body.appendChild(overlay);

    return { overlay: overlay, image: image, closeButton: closeButton };
  }

  function init() {
    var ui = createLightbox();
    var overlay = ui.overlay;
    var image = ui.image;
    var closeButton = ui.closeButton;
    var previousOverflow = "";

    function openLightbox(src, alt) {
      image.src = src;
      image.alt = alt || "";
      overlay.classList.add("is-open");
      overlay.setAttribute("aria-hidden", "false");
      previousOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      closeButton.focus();
    }

    function closeLightbox() {
      overlay.classList.remove("is-open");
      overlay.setAttribute("aria-hidden", "true");
      image.src = "";
      document.body.style.overflow = previousOverflow;
    }

    document.addEventListener("click", function (event) {
      var target = event.target;
      if (target && target.tagName === "IMG" && isLightboxCandidate(target)) {
        openLightbox(target.currentSrc || target.src, target.alt);
      }
    });

    closeButton.addEventListener("click", closeLightbox);

    overlay.addEventListener("click", function () {
      closeLightbox();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && overlay.classList.contains("is-open")) {
        closeLightbox();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
