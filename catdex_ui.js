/* =============================================================================
   Catdex - small browser extras. app.py passes this file to demo.launch(js=...),
   so it runs once when the page opens. No libraries needed (works on bad Wi-Fi).
     1. Force light mode (so a phone in dark mode keeps the peach & cream colours)
     2. Confetti for big moments (new cat, new badge)
     3. Camera: open it with one tap, and re-open it after each photo
     4. Scroll to the top when you switch pages
     5. Front / back camera switch (🔄 button on the camera view; starts with the back camera)
   ============================================================================= */

(function () {
  if (window.CatdexUI) return;              // only set things up once
  window.CatdexUI = {};

  const calm = window.matchMedia("(prefers-reduced-motion: reduce)");

  /* ---- 1. Force light mode ------------------------------------------------- */
  // Gradio adds a "dark" class when the phone is in dark mode. We take it off again.
  // (Gradio puts it on <body>, or on the box around the app when it's embedded in another page.)
  const darkSpots = () => [document.documentElement, document.body,
                           document.querySelector("gradio-app") && document.querySelector("gradio-app").parentElement];
  function stayLight() {
    darkSpots().forEach((el) => el && el.classList.contains("dark") && el.classList.remove("dark"));
  }
  stayLight();
  const lightWatcher = new MutationObserver(stayLight);
  darkSpots().forEach((el) => el && lightWatcher.observe(el, { attributes: true, attributeFilter: ["class"] }));

  /* ---- 2. Confetti ------------------------------------------------------------ */
  const COLOURS = ["#F6A57A", "#F9D5C5", "#E2A62A", "#86B98F", "#7A4E2D", "#FFFFFF", "#EE8F78"];
  const played = new Set();                 // each celebration plays once, even if the page redraws it

  function confetti(fromElement) {
    if (calm.matches) return;               // the person asked for less motion
    const canvas = document.createElement("canvas");
    canvas.className = "catdex-confetti";
    canvas.style.cssText = "position:fixed;inset:0;width:100vw;height:100vh;pointer-events:none;z-index:9999";
    document.body.appendChild(canvas);
    const ratio = window.devicePixelRatio || 1;
    canvas.width = innerWidth * ratio;
    canvas.height = innerHeight * ratio;
    const ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);

    // Start from the middle of the celebrated element (or the middle of the screen)
    const box = fromElement ? fromElement.getBoundingClientRect() : null;
    const startX = box ? box.left + box.width / 2 : innerWidth / 2;
    const startY = box ? Math.max(60, Math.min(innerHeight * 0.6, box.top + 80)) : innerHeight / 3;

    const pieces = [];
    for (let i = 0; i < 130; i++) {
      const angle = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.1;
      const speed = 6 + Math.random() * 9;
      pieces.push({
        x: startX, y: startY,
        vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
        size: 5 + Math.random() * 7, spin: Math.random() * 6, turn: (Math.random() - 0.5) * 0.3,
        colour: COLOURS[i % COLOURS.length], round: Math.random() < 0.35,
      });
    }
    const start = performance.now();
    function frame(now) {
      const t = now - start;
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      ctx.globalAlpha = Math.max(0, 1 - Math.max(0, t - 1800) / 900);   // fade out at the end
      for (const p of pieces) {
        p.vy += 0.28;  p.vx *= 0.985;  p.vy *= 0.985;                     // gravity and air
        p.x += p.vx;   p.y += p.vy;    p.spin += p.turn;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.spin);
        ctx.fillStyle = p.colour;
        if (p.colour === "#FFFFFF") { ctx.strokeStyle = "#F0DFCF"; ctx.lineWidth = 1; }
        if (p.round) { ctx.beginPath(); ctx.arc(0, 0, p.size / 2, 0, Math.PI * 2); ctx.fill(); }
        else { ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2); }
        ctx.restore();
      }
      if (t < 2700) requestAnimationFrame(frame);
      else canvas.remove();
    }
    requestAnimationFrame(frame);
  }
  window.CatdexUI.confetti = confetti;

  // Python marks big moments with class="fx-confetti" and a unique data-fx id.
  function lookForCelebrations() {
    document.querySelectorAll(".fx-confetti[data-fx]").forEach((el) => {
      const key = el.dataset.fx;
      if (played.has(key) || !el.offsetParent) return;   // already played, or not on screen
      played.add(key);
      setTimeout(() => confetti(el), 650);               // just after the card has flipped in
    });
  }

  /* ---- 3. Camera ---------------------------------------------------------------- */
  // Gradio's camera box needs a tap on "access webcam" before every photo. We do that tap
  // for the person: once when they press "Take photos", and again after each photo.
  let cameraWanted = false;
  let autoTaps = 0;            // safety: if the camera won't start (e.g. no permission), stop trying
  let lastTap = 0;
  document.addEventListener("click", (event) => {
    if (event.target.closest("#camera-btn")) { cameraWanted = true; autoTaps = 0; }
    if (event.target.closest("#cat-cam .capture-button")) autoTaps = 0;    // a photo was taken
  }, true);

  function wakeCamera() {
    if (!cameraWanted || autoTaps >= 3 || Date.now() - lastTap < 1500) return;
    const cam = document.querySelector("#cat-cam");
    if (!cam || !cam.offsetParent) return;                 // camera box hidden
    const opener = cam.querySelector('[title="grant webcam access"] button');
    if (!opener) return;                                   // camera already on
    autoTaps += 1;
    lastTap = Date.now();
    opener.click();
  }

  /* ---- 5. Front / back camera switch ------------------------------------------------- */
  // Phones have a front (selfie) and a back camera. Gradio always opens its default one and
  // forgets your choice after every photo. So:
  //   a) we remember which way you want the camera to face (back camera by default - for cats!)
  //   b) whenever Gradio asks the browser for the camera, we add that wish to the request
  //   c) a round 🔄 button on the camera view swaps cameras straight away
  // Laptops don't say which way a camera faces, so there the button just steps to the next camera.
  const media = navigator.mediaDevices;
  const realGetUserMedia = media && media.getUserMedia ? media.getUserMedia.bind(media) : null;
  let facing = "environment";            // "environment" = back camera, "user" = front camera
  let chosenDeviceId = null;             // laptops: the camera picked with the button
  let swappedStream = null;              // a camera stream WE started (Gradio doesn't know to stop it)
  let cameraCount = 0;
  try { facing = localStorage.getItem("catdex-facing") || facing; } catch (e) { /* private mode */ }

  function rememberFacing(value) {
    facing = value;
    try { localStorage.setItem("catdex-facing", value); } catch (e) { /* private mode */ }
  }

  if (realGetUserMedia) {
    media.getUserMedia = function (constraints) {
      const wantsVideo = constraints && constraints.video;
      const hasDevice = wantsVideo && typeof constraints.video === "object" && constraints.video.deviceId;
      if (wantsVideo && !hasDevice) {                       // Gradio opening "a camera" -> make it ours
        const video = typeof constraints.video === "object" ? { ...constraints.video } : {};
        if (chosenDeviceId) video.deviceId = { exact: chosenDeviceId };
        else video.facingMode = { ideal: facing };
        constraints = { ...constraints, video };
      }
      stopSwapped();                                        // only one camera open at a time
      return realGetUserMedia(constraints);
    };
  }

  function stopSwapped() {
    if (swappedStream) swappedStream.getTracks().forEach((t) => t.stop());
    swappedStream = null;
  }

  async function switchCamera() {
    const video = document.querySelector("#cat-cam video");
    const oldStream = video && video.srcObject;
    if (!oldStream || !realGetUserMedia) return;
    const settings = oldStream.getVideoTracks()[0]?.getSettings() || {};
    let request;
    if (settings.facingMode) {                              // phones: flip front <-> back
      rememberFacing(settings.facingMode === "user" ? "environment" : "user");
      request = { facingMode: { exact: facing } };
    } else {                                                // laptops: next camera in the list
      const cams = (await media.enumerateDevices()).filter((d) => d.kind === "videoinput");
      if (cams.length < 2) return;
      const now = cams.findIndex((d) => d.deviceId === settings.deviceId);
      chosenDeviceId = cams[(now + 1) % cams.length].deviceId;
      request = { deviceId: { exact: chosenDeviceId } };
    }
    oldStream.getTracks().forEach((t) => t.stop());         // many phones can't run two cameras at once
    let stream;
    try {
      stream = await realGetUserMedia({ video: { ...request, width: { ideal: 1920 }, height: { ideal: 1440 } } });
    } catch (e) {                                           // "exact" not possible -> ask more gently
      stream = await realGetUserMedia({ video: request.facingMode ? { facingMode: { ideal: facing } } : true });
    }
    stopSwapped();
    swappedStream = stream;
    video.srcObject = stream;                               // the photo button snaps from this video
    await video.play().catch(() => {});
    updateCameraLook();
  }

  // Add the 🔄 button (once), show it only while the camera is live and there's more than one camera,
  // mirror the preview for the selfie camera, and tidy up a stream we started once Gradio closes the camera.
  function updateCameraLook() {
    const cam = document.querySelector("#cat-cam");
    const video = cam && cam.querySelector("video");
    const live = !!(video && video.srcObject && cam.offsetParent);
    if (swappedStream && (!live || video.srcObject !== swappedStream)) stopSwapped();
    if (!cam) return;

    let btn = cam.querySelector(".cam-switch");
    if (!btn) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cam-switch";
      btn.setAttribute("aria-label", "Switch between front and back camera");
      btn.innerHTML = '<span aria-hidden="true">🔄</span>';
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        switchCamera().catch((e) => console.warn("Catdex: couldn't switch camera", e));
      });
      cam.appendChild(btn);
    }
    if (live && !cameraCount && media && media.enumerateDevices) {
      cameraCount = -1;                                     // ask once
      media.enumerateDevices().then((list) => {
        cameraCount = list.filter((d) => d.kind === "videoinput").length;
        updateCameraLook();
      });
    }
    btn.hidden = !(live && cameraCount > 1);
    const track = live && video.srcObject.getVideoTracks()[0];
    const selfie = !!track && track.getSettings().facingMode === "user";
    cam.classList.toggle("is-selfie", selfie);
  }

  /* ---- 4. Scroll to the top when the page (tab) changes ----------------------------- */
  let lastTab = null;
  function watchTabs() {
    const selected = document.querySelector('#catdex-nav button[role="tab"][aria-selected="true"]');
    const id = selected ? selected.dataset.tabId : null;
    if (id && lastTab && id !== lastTab) window.scrollTo({ top: 0, behavior: "auto" });
    if (id) lastTab = id;
  }

  // One watcher for the whole page: runs the checks whenever Gradio redraws something
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      lookForCelebrations();
      wakeCamera();
      updateCameraLook();
      watchTabs();
    });
  }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-selected"] });
  // A video starting to play doesn't change the page layout, so also check when any video starts.
  document.addEventListener("playing", updateCameraLook, true);
})();