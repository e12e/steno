/* Steno Desktop UI Controller — Utilitarian 3-Tab UX + Dual-Mode Vintage Oscilloscope */

const UX_STATE_MACHINE = {
  intent: "record",
  hasContext: false,
  step: "idle",
  activeTab: "record",
  colorScheme: "system",
  uiLang: "no",
  audioLang: "no",
  selectedAudioPath: "",
  recording: false,
  running: false,
  micTesting: false,
  playing: false,
  recStartMs: 0,
  recTimerId: null,
  lastDetectedSuggestion: null,
  hardware: null,
  resourceFit: null,
  profiles: null,
  supportedLanguages: [],
  audioInputs: [],
  resumableSessions: [],
};

const I18N = {
  no: {
    app_title: "Steno",
    state_ready: "Klar",
    state_recording: "Tar opp",
    state_testing: "Mikrofontest",
    state_processing: "Behandler",
    tab_record: "Ta opp",
    tab_transcribe: "Transkriber & Referat",
    tab_settings: "Innstillinger",
    scope_standby: "STANDBY",
    scope_recording: "LIVE OPPTAK",
    scope_testing: "MIKROFONTEST",
    scope_transcribing: "ANALYSERER TALE",
    rec_start: "Start opptak",
    rec_stop: "Stopp opptak",
    mic_test: "Test mikrofon (4s)",
    mic_testing: "Tester mikrofon...",
    play_audio: "Spill av",
    stop_audio: "Stopp avspilling",
    mic_device_label: "Mikrofon / Inngang",
    mic_gain_label: "Programforsterkning",
    mic_auto_gain_label: "Auto-forsterkning",
    sys_mic_vol_label: "Systemnivå (mic)",
    sound_settings_btn: "Lydinnstillinger",
    live_transcribe_label: "Sanntids lydsjekk hvert 5. sekund",
    auto_speak_guide_label: "Spill taleguide før opptak",
    prompt_guide_hint: "Les opp eller oppgi møtenavn og deltakere i starten av opptaket — hjelper Steno å kjenne igjen talere.",
    live_feed_placeholder: "Sanntids taleutdrag og nivå sjekkes her under opptak eller hurtigsjekk...",
    post_rec_saved: "Opptak lagret og klart:",
    post_rec_cta: "Gå til Transkriber & Referat",
    source_section_title: "Lydfil eller økt",
    dropzone_hint: "Slipp lyd-, video- eller transkripsjonsfil her (.wav, .m4a, .mp3, .mp4, .txt, .md)",
    browse_audio_btn: "Velg fil...",
    url_fetch_btn: "Hent URL",
    media_url_placeholder: "Lim inn lenke til video/strøm (YouTube, NRK, direkte URL)...",
    resume_session_label: "Tidligere / delvis fullførte økter",
    resume_none: "-- Velg lagret økt --",
    no_file_selected: "Ingen fil valgt",
    quick_check_btn: "Hurtigsjekk (20s)",
    lang_section_title: "Språk og format",
    audio_lang_label: "Talespråk i lyd",
    detect_lang_btn: "Oppdag språk",
    note_format_label: "Format / mal",
    format_auto: "Automatisk",
    format_mote: "Møtereferat (flere talere)",
    format_foredrag: "Foredrag / Notat (1 taler)",
    min_speakers_label: "Min talere",
    max_speakers_label: "Maks talere",
    speaker_bounds_error: "Ugyldig antall talere (min må være <= maks).",
    pipeline_section_title: "Transkripsjon & Referat",
    task_mode_label: "Kjøremodus",
    task_auto_resume: "Start / Fortsett fra sjekkpunkt",
    task_transcribe_only: "Kun transkripsjon",
    task_notes_only: "Kun referat (bruk eksisterende transkripsjon)",
    task_rerun_all: "Kjør alt på nytt",
    run_btn: "Start / Fortsett",
    stop_btn: "Pause / Stopp",
    open_transcript_btn: "Åpne transkripsjon",
    open_notes_html_btn: "Åpne referat",
    open_folder_btn: "Åpne mappe",
    ckpt_s1: "1. Tale",
    ckpt_s2: "2. Tidskoder",
    ckpt_s3: "3. Talere",
    ckpt_s4: "4. Navn",
    ckpt_s5: "5. Referat",
    context_summary: "Møtekontekst (tittel, talere, agenda, stikkord)",
    meeting_title_label: "Tittel / møtenavn",
    speaker_names_label: "Talerliste (kommaseparert)",
    context_url_placeholder: "Hent kontekst fra nettside eller dokumentlenke...",
    context_url_btn: "Hent lenke",
    context_file_btn: "Last inn fil...",
    agenda_label: "Agenda / lysbilder / referatgrunnlag",
    manual_notes_label: "Egne notater (blandes inn i referatet)",
    settings_drawer_summary: "System, språk, modeller og utdatamappe",
    appearance_section: "Utseende og språk",
    ui_lang_label: "Grensesnittspråk",
    color_scheme_label: "Fargetema",
    scheme_system: "System (følg OS)",
    scheme_light: "Lys (beige oscilloskop)",
    scheme_dark: "Mørk (grønt oscilloskop)",
    models_section: "AI-modeller og maskinvare",
    model_profile_label: "Ressursprofil",
    profile_light: "Lett (raskest)",
    profile_recommended: "Anbefalt",
    profile_heavy: "Tung (maks kvalitet)",
    whisper_model_label: "Whisper ASR-modell",
    ollama_model_label: "Ollama LLM-modell",
    apply_rec_btn: "Bruk anbefalte",
    output_section: "Utdatamappe og filnavn",
    out_dir_label: "Utdatamappe",
    pick_dir_btn: "Velg...",
    html_name_label: "Transkripsjon (.html)",
    notes_name_label: "Referat (.md)",
    notes_html_name_label: "Referat (.html)",
    diarization_section: "Taleridentifikasjon (Hugging Face)",
    hf_token_label: "Hugging Face Token (valgfritt hvis pyannote er mellomlagret)",
    hf_token_placeholder: "hf_...",
    maintenance_section: "App og vedlikehold",
    install_shortcut_btn: "Installer skrivebordssnarvei",
    uninstall_btn: "Avinstaller Steno...",
    progress_idle: "Klar",
    log_drawer_summary: "Kjørelogg",
    save_log_btn: "Lagre logg...",
    uninstall_title: "Avinstallere Steno?",
    uninstall_body: "Dette fjerner mellomlagrede kjøretidsfiler, nedlastede modeller og skrivebordssnarveier for Steno.",
    uninstall_cancel: "Avbryt",
    uninstall_confirm: "Avinstaller nå",
    apply_detected_lang: "Bytt til oppdaget språk",
    url_error: "Skriv inn en gyldig http://- eller https://-lenke.",
    dir_error: "Utdatamappen kan ikke være tom.",
  },
  en: {
    app_title: "Steno",
    state_ready: "Ready",
    state_recording: "Recording",
    state_testing: "Mic check",
    state_processing: "Processing",
    tab_record: "Record",
    tab_transcribe: "Transcribe & Notes",
    tab_settings: "Settings",
    scope_standby: "STANDBY",
    scope_recording: "LIVE RECORDING",
    scope_testing: "MIC CHECK",
    scope_transcribing: "ANALYSING",
    rec_start: "Start Recording",
    rec_stop: "Stop Recording",
    mic_test: "Test Mic (4s)",
    mic_testing: "Testing Mic...",
    play_audio: "Play",
    stop_audio: "Stop Playback",
    mic_device_label: "Microphone / Input",
    mic_gain_label: "Software boost",
    mic_auto_gain_label: "Auto gain",
    sys_mic_vol_label: "System input volume",
    sound_settings_btn: "Sound Settings",
    live_transcribe_label: "Live speech check every 5s",
    auto_speak_guide_label: "Speak intro prompt before recording",
    prompt_guide_hint: "State the session title and participant names at the start — this helps Steno identify speakers accurately.",
    live_feed_placeholder: "Live speech snippets and levels appear here during recording or audio check...",
    post_rec_saved: "Recording saved:",
    post_rec_cta: "Continue to Transcribe & Notes",
    source_section_title: "Audio File or Session",
    dropzone_hint: "Drop audio, video, or transcript here (.wav, .m4a, .mp3, .mp4, .txt, .md)",
    browse_audio_btn: "Browse...",
    url_fetch_btn: "Fetch URL",
    media_url_placeholder: "Paste video or stream URL (YouTube, NRK, direct media URL)...",
    resume_session_label: "Saved / partial sessions",
    resume_none: "-- Choose saved session --",
    no_file_selected: "No file selected",
    quick_check_btn: "Quick Check (20s)",
    lang_section_title: "Language & Output Format",
    audio_lang_label: "Spoken language",
    detect_lang_btn: "Detect Language",
    note_format_label: "Note format",
    format_auto: "Automatic",
    format_mote: "Meeting Notes (multi-speaker)",
    format_foredrag: "Lecture / Talk (1 speaker)",
    min_speakers_label: "Min speakers",
    max_speakers_label: "Max speakers",
    speaker_bounds_error: "Invalid speaker range (min must be <= max).",
    pipeline_section_title: "Transcribe & Generate Notes",
    task_mode_label: "Execution mode",
    task_auto_resume: "Start / Resume from checkpoint",
    task_transcribe_only: "Transcribe only",
    task_notes_only: "Notes only (reuse existing transcript)",
    task_rerun_all: "Re-run all stages",
    run_btn: "Start / Resume",
    stop_btn: "Pause / Stop",
    open_transcript_btn: "Open Transcript",
    open_notes_html_btn: "Open Notes",
    open_folder_btn: "Open Folder",
    ckpt_s1: "1. ASR",
    ckpt_s2: "2. Align",
    ckpt_s3: "3. Diarize",
    ckpt_s4: "4. Names",
    ckpt_s5: "5. Notes",
    context_summary: "Session Context (title, speakers, agenda, notes)",
    meeting_title_label: "Session title",
    speaker_names_label: "Speaker names (comma-separated)",
    context_url_placeholder: "Fetch agenda or slides from URL...",
    context_url_btn: "Fetch Link",
    context_file_btn: "Load File...",
    agenda_label: "Agenda / slides / reference text",
    manual_notes_label: "Your manual notes (merged into summary)",
    settings_drawer_summary: "System, language, models & output folder",
    appearance_section: "Appearance & Language",
    ui_lang_label: "Interface language",
    color_scheme_label: "Color theme",
    scheme_system: "System (follow OS)",
    scheme_light: "Light (beige oscilloscope)",
    scheme_dark: "Dark (green oscilloscope)",
    models_section: "AI Models & Hardware Fit",
    model_profile_label: "Resource profile",
    profile_light: "Light (fastest)",
    profile_recommended: "Recommended",
    profile_heavy: "Heavy (highest quality)",
    whisper_model_label: "Whisper ASR model",
    ollama_model_label: "Ollama LLM model",
    apply_rec_btn: "Apply Recommended",
    output_section: "Output Folder & Filenames",
    out_dir_label: "Output folder",
    pick_dir_btn: "Browse...",
    html_name_label: "Transcript (.html)",
    notes_name_label: "Notes (.md)",
    notes_html_name_label: "Notes (.html)",
    diarization_section: "Speaker Diarization (Hugging Face)",
    hf_token_label: "Hugging Face Token (optional if pyannote is cached)",
    hf_token_placeholder: "hf_...",
    maintenance_section: "Application & Maintenance",
    install_shortcut_btn: "Install Desktop Shortcut",
    uninstall_btn: "Uninstall Steno...",
    progress_idle: "Ready",
    log_drawer_summary: "Execution Log",
    save_log_btn: "Save Log...",
    uninstall_title: "Uninstall Steno?",
    uninstall_body: "This removes Steno cached runtimes, downloaded model files, and desktop shortcuts.",
    uninstall_cancel: "Cancel",
    uninstall_confirm: "Uninstall Now",
    apply_detected_lang: "Switch to detected language",
    url_error: "Enter a valid http:// or https:// URL.",
    dir_error: "Output folder cannot be empty.",
  },
};

function t(key) {
  const dict = I18N[UX_STATE_MACHINE.uiLang] || I18N.no;
  return dict[key] || I18N.no[key] || key;
}

function el(id) {
  return document.getElementById(id);
}

function hasApi() {
  return Boolean(window.pywebview && window.pywebview.api);
}

function announceStatus(message) {
  const region = el("status_live_region");
  if (!region || !message) return;
  region.textContent = message;
}

function appendLogLine(line) {
  const pre = el("log");
  if (!pre || line === undefined || line === null) return;
  pre.textContent += (pre.textContent ? "\n" : "") + String(line);
  pre.scrollTop = pre.scrollHeight;
}

function formatDuration(sec) {
  const total = Math.max(0, Math.floor(sec));
  const mins = String(Math.floor(total / 60)).padStart(2, "0");
  const secs = String(total % 60).padStart(2, "0");
  return mins + ":" + secs;
}

/* ==========================================================================
   Dual-Mode Vintage Oscilloscope (Light: Beige Paper / Dark: Green CRT)
   ========================================================================== */

const SCOPE_STATE = {
  history: [],
  latest: new Array(72).fill(0),
  lastSampleMs: 0,
  rafId: null,
  statusKey: "scope_standby",
};

function isEffectiveDarkMode() {
  const meta = document.head.querySelector('meta[name="color-scheme"]');
  const content = meta ? meta.getAttribute("content") : "light dark";
  if (content === "dark") return true;
  if (content === "light") return false;
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function drawScopeOnce() {
  const canvas = el("scope_canvas");
  if (!canvas) return false;
  const ctx = canvas.getContext("2d");
  if (!ctx) return false;

  const dpr = Math.max(1, window.devicePixelRatio || 1);
  const cssW = canvas.clientWidth || 640;
  const cssH = canvas.clientHeight || 176;
  const targetW = Math.round(cssW * dpr);
  const targetH = Math.round(cssH * dpr);
  if (canvas.width !== targetW || canvas.height !== targetH) {
    canvas.width = targetW;
    canvas.height = targetH;
  }

  ctx.save();
  ctx.scale(dpr, dpr);

  const dark = isEffectiveDarkMode();
  const palette = dark
    ? {
        bg: "#08120c",
        gridMinor: "#132d1f",
        gridAxis: "#1f4c34",
        trailRgb: "52, 211, 153",
        beam: "#6ee7b7",
        glow: "rgba(74, 222, 128, 0.28)",
      }
    : {
        bg: "#efe7d5",
        gridMinor: "#ddd1b7",
        gridAxis: "#c5b38f",
        trailRgb: "146, 88, 38",
        beam: "#6c3f1c",
        glow: "rgba(146, 88, 38, 0.16)",
      };

  // Background
  ctx.fillStyle = palette.bg;
  ctx.fillRect(0, 0, cssW, cssH);

  // Etched 12x4 graticule
  ctx.lineWidth = 1;
  ctx.strokeStyle = palette.gridMinor;
  const cols = 12;
  const rows = 4;
  ctx.beginPath();
  for (let c = 1; c < cols; c++) {
    const x = Math.round((cssW * c) / cols) + 0.5;
    ctx.moveTo(x, 0);
    ctx.lineTo(x, cssH);
  }
  for (let r = 1; r < rows; r++) {
    const y = Math.round((cssH * r) / rows) + 0.5;
    ctx.moveTo(0, y);
    ctx.lineTo(cssW, y);
  }
  ctx.stroke();

  // Center crosshair axes & graduation ticks
  const midX = Math.round(cssW / 2) + 0.5;
  const midY = Math.round(cssH / 2) + 0.5;
  ctx.strokeStyle = palette.gridAxis;
  ctx.beginPath();
  ctx.moveTo(0, midY);
  ctx.lineTo(cssW, midY);
  ctx.moveTo(midX, 0);
  ctx.lineTo(midX, cssH);
  for (let i = 1; i < cols * 4; i++) {
    const tx = (cssW * i) / (cols * 4);
    ctx.moveTo(tx, midY - 3);
    ctx.lineTo(tx, midY + 3);
  }
  ctx.stroke();

  const ageMs = performance.now() - SCOPE_STATE.lastSampleMs;
  const active = UX_STATE_MACHINE.recording || UX_STATE_MACHINE.micTesting;
  const ampGain = cssH * 0.42;

  const drawTrace = (samples, color, width, blurColor) => {
    const n = samples.length;
    if (!n) return;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    if (blurColor) {
      ctx.shadowColor = blurColor;
      ctx.shadowBlur = 7;
    }
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const x = (i / Math.max(1, n - 1)) * cssW;
      const clamped = Math.max(-1, Math.min(1, samples[i]));
      const y = midY - clamped * ampGain;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.restore();
  };

  // Persistence trails (up to 7 frames)
  for (let idx = 0; idx < SCOPE_STATE.history.length; idx++) {
    const ratio = (idx + 1) / (SCOPE_STATE.history.length + 1);
    const alpha = (0.06 + ratio * 0.34).toFixed(3);
    drawTrace(
      SCOPE_STATE.history[idx],
      "rgba(" + palette.trailRgb + ", " + alpha + ")",
      1.35,
      null
    );
  }

  // Soft decay when silent or idle
  if (!active || ageMs > 220) {
    let maxAbs = 0;
    for (let i = 0; i < SCOPE_STATE.latest.length; i++) {
      SCOPE_STATE.latest[i] *= active ? 0.92 : 0.82;
      if (Math.abs(SCOPE_STATE.latest[i]) < 0.002) SCOPE_STATE.latest[i] = 0;
      else maxAbs = Math.max(maxAbs, Math.abs(SCOPE_STATE.latest[i]));
    }
    if (!active && maxAbs === 0 && SCOPE_STATE.history.length > 0) {
      SCOPE_STATE.history.shift();
    }
  }

  // Primary beam
  drawTrace(SCOPE_STATE.latest, palette.beam, 2.0, palette.glow);

  ctx.restore();
  return active || SCOPE_STATE.history.length > 0 || SCOPE_STATE.latest.some((v) => Math.abs(v) > 0);
}

function animateScope() {
  SCOPE_STATE.rafId = null;
  const keepGoing = drawScopeOnce();
  if (keepGoing) {
    SCOPE_STATE.rafId = window.requestAnimationFrame(animateScope);
  }
}

function ensureScopeAnimating() {
  if (SCOPE_STATE.rafId === null) {
    SCOPE_STATE.rafId = window.requestAnimationFrame(animateScope);
  }
}

function pushWaveformSamples(samples) {
  if (!Array.isArray(samples) || samples.length === 0) return;
  const copy = samples.map((v) => {
    const num = Number(v);
    return Number.isFinite(num) ? Math.max(-1, Math.min(1, num)) : 0;
  });
  SCOPE_STATE.history.push(SCOPE_STATE.latest.slice());
  if (SCOPE_STATE.history.length > 7) {
    SCOPE_STATE.history.shift();
  }
  SCOPE_STATE.latest = copy;
  SCOPE_STATE.lastSampleMs = performance.now();
  ensureScopeAnimating();
}

/* ==========================================================================
   Tabs, Theme, Bilingual i18n & Header Summary
   ========================================================================== */

const TAB_CONFIG = [
  { mode: "record", tabId: "tab_record", panelId: "panel_record" },
  { mode: "transcribe", tabId: "tab_transcribe", panelId: "panel_transcribe" },
  { mode: "settings", tabId: "tab_settings", panelId: "panel_settings" },
];

function selectModeTab(mode) {
  const norm = TAB_CONFIG.some((c) => c.mode === mode) ? mode : "record";
  UX_STATE_MACHINE.activeTab = norm;
  if (norm === "record" || norm === "transcribe") {
    UX_STATE_MACHINE.intent = norm;
  }

  for (const item of TAB_CONFIG) {
    const tabBtn = el(item.tabId);
    const panel = el(item.panelId);
    const isActive = item.mode === norm;
    if (tabBtn) {
      tabBtn.setAttribute("aria-selected", isActive ? "true" : "false");
      tabBtn.tabIndex = isActive ? 0 : -1;
    }
    if (panel) {
      if (isActive) {
        panel.removeAttribute("hidden");
      } else {
        panel.setAttribute("hidden", "until-found");
      }
    }
  }

  const contextDrawer = el("context_notes_drawer");
  if (contextDrawer) {
    if (norm === "settings") {
      contextDrawer.setAttribute("hidden", "");
    } else {
      contextDrawer.removeAttribute("hidden");
    }
  }

  if (norm === "record") {
    drawScopeOnce();
  }
}

function setColorScheme(scheme, persist) {
  const norm = scheme === "light" || scheme === "dark" ? scheme : "system";
  UX_STATE_MACHINE.colorScheme = norm;

  const meta = document.head.querySelector('meta[name="color-scheme"]');
  if (meta) {
    meta.setAttribute("content", norm === "system" ? "light dark" : norm);
  }
  try {
    if (norm === "system") {
      localStorage.removeItem("steno-color-scheme");
    } else {
      localStorage.setItem("steno-color-scheme", norm);
    }
  } catch (_e) {}

  const sel = el("color_scheme_select");
  if (sel && sel.value !== norm) {
    sel.value = norm;
  }

  drawScopeOnce();
  updateSettingsSummary();

  if (persist !== false && hasApi()) {
    window.pywebview.api.save_preferences({ color_scheme: norm }).catch(() => {});
  }
}

function applyTranslations() {
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    const key = node.getAttribute("data-i18n");
    if (key) node.textContent = t(key);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
    const key = node.getAttribute("data-i18n-placeholder");
    if (key) node.setAttribute("placeholder", t(key));
  });
  populateMicDevices();
  renderResumableSessionsSelect(UX_STATE_MACHINE.resumableSessions);
  updateHeaderStateBadge();
  updateSettingsSummary();
  updateRecordButtonText();
  updatePlayButtonText();
  if (!UX_STATE_MACHINE.selectedAudioPath && el("selected-file")) {
    el("selected-file").textContent = t("no_file_selected");
  }
}

function populateMicDevices() {
  const micSel = el("mic_device_select");
  const list = UX_STATE_MACHINE.audioInputs;
  if (!micSel || !Array.isArray(list) || list.length === 0) return;
  const prevVal = micSel.value;
  const isEn = UX_STATE_MACHINE.uiLang === "en";
  micSel.innerHTML = "";
  for (const dev of list) {
    const opt = document.createElement("option");
    opt.value = dev.id || "default";
    opt.textContent = (isEn ? dev.label_en : dev.label_no) || dev.label || dev.name || dev.id || "Default";
    micSel.appendChild(opt);
  }
  if (prevVal && Array.from(micSel.options).some((o) => o.value === prevVal)) {
    micSel.value = prevVal;
  }
}

function renderResumableSessionsSelect(list) {
  const sel = el("resumable_sessions_select");
  if (!sel) return;
  const prevVal = sel.value;
  const isEn = UX_STATE_MACHINE.uiLang === "en";
  sel.innerHTML = "";
  const emptyOpt = document.createElement("option");
  emptyOpt.value = "";
  emptyOpt.textContent = t("resume_none");
  sel.appendChild(emptyOpt);
  if (Array.isArray(list)) {
    for (const s of list) {
      const opt = document.createElement("option");
      opt.value = s.audio_path || "";
      const count = s.completed_count !== undefined ? s.completed_count : s.completed_stages;
      const stage = (isEn ? s.next_stage_en : s.next_stage_no) || "";
      const baseName = s.stem || s.label || s.audio_name || s.audio_path || "";
      opt.textContent =
        baseName +
        (count !== undefined ? " [" + count + "/5]" : "") +
        (stage ? " — " + stage : "");
      sel.appendChild(opt);
    }
  }
  if (prevVal && Array.from(sel.options).some((o) => o.value === prevVal)) {
    sel.value = prevVal;
  }
}

function setUiLanguage(lang, persist) {
  const norm = String(lang || "no").toLowerCase().startsWith("en") ? "en" : "no";
  UX_STATE_MACHINE.uiLang = norm;
  document.documentElement.lang = norm;
  const sel = el("ui_lang_select");
  if (sel && sel.value !== norm) {
    sel.value = norm;
  }
  populateSupportedLanguages();
  applyTranslations();

  if (persist !== false && hasApi()) {
    window.pywebview.api.save_preferences({ ui_lang: norm }).catch(() => {});
  }
}

function populateSupportedLanguages() {
  const sel = el("audio_lang_select");
  if (!sel || !Array.isArray(UX_STATE_MACHINE.supportedLanguages) || UX_STATE_MACHINE.supportedLanguages.length === 0) {
    return;
  }
  const current = UX_STATE_MACHINE.audioLang || sel.value || "no";
  sel.innerHTML = "";
  const isEn = UX_STATE_MACHINE.uiLang === "en";
  for (const item of UX_STATE_MACHINE.supportedLanguages) {
    const opt = document.createElement("option");
    opt.value = item.code;
    opt.textContent = isEn ? item.label_en : item.label_no;
    sel.appendChild(opt);
  }
  sel.value = current;
}

function setAudioLanguage(lang, persist) {
  const norm = String(lang || "no").trim() || "no";
  UX_STATE_MACHINE.audioLang = norm;
  const audioSel = el("audio_lang_select");
  const hiddenLang = el("language");
  if (audioSel && audioSel.value !== norm) audioSel.value = norm;
  if (hiddenLang) hiddenLang.value = norm;

  const banner = el("lang_confirm_banner");
  if (banner) banner.classList.add("hidden");

  if (persist !== false && hasApi()) {
    window.pywebview.api.save_preferences({ audio_language: norm }).catch(() => {});
    const prof = el("model_profile") ? el("model_profile").value : "recommended";
    const outDir = el("out_dir") ? el("out_dir").value : "";
    window.pywebview.api
      .get_language_recommendations(norm, prof, outDir)
      .then((res) => {
        if (!res || !res.ok) return;
        if (el("whisper_model") && res.chosen_whisper) el("whisper_model").value = res.chosen_whisper;
        if (el("ollama_model") && res.chosen_ollama) el("ollama_model").value = res.chosen_ollama;
        if (res.hardware) UX_STATE_MACHINE.hardware = res.hardware;
        if (res.resource_fit) UX_STATE_MACHINE.resourceFit = res.resource_fit;
        renderResourceBanner();
        updateSettingsSummary();
      })
      .catch(() => {});
  }
}

function onLanguageDetected(info) {
  if (!info || !info.detected_language) return;
  UX_STATE_MACHINE.lastDetectedSuggestion = info;
  const banner = el("lang_confirm_banner");
  const msgNode = el("lang_confirm_text");
  if (!banner || !msgNode) return;
  const pct = Math.round(Number(info.language_probability || 0) * 100);
  const label = UX_STATE_MACHINE.uiLang === "en"
    ? info.label_en || info.detected_language
    : info.label_no || info.detected_language;
  const prefix = UX_STATE_MACHINE.uiLang === "en" ? "Detected language:" : "Oppdaget språk:";
  msgNode.textContent = prefix + " " + label + (pct ? " (" + pct + "%)" : "");
  if (String(info.detected_language) !== String(UX_STATE_MACHINE.audioLang)) {
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
  announceStatus(msgNode.textContent);
}

function confirmDetectedLanguageChange() {
  const info = UX_STATE_MACHINE.lastDetectedSuggestion;
  if (!info || !info.detected_language) return;
  setAudioLanguage(info.detected_language, true);
  if (info.recommended_whisper && el("whisper_model")) {
    el("whisper_model").value = info.recommended_whisper;
  }
  const banner = el("lang_confirm_banner");
  if (banner) banner.classList.add("hidden");
  updateSettingsSummary();
}

function setSessionFormat(fmt) {
  const norm = fmt === "mote" || fmt === "foredrag" ? fmt : "auto";
  const sel = el("note_mode");
  if (sel) sel.value = norm;
}

function setUxIntent(intent) {
  UX_STATE_MACHINE.intent = intent;
  if (intent === "record" || intent === "transcribe") {
    selectModeTab(intent);
  }
}

function setContextAvailability(hasContext) {
  UX_STATE_MACHINE.hasContext = Boolean(hasContext);
}

function setUxStep(step) {
  UX_STATE_MACHINE.step = step;
  updateHeaderStateBadge();
}

function updateHeaderStateBadge() {
  const badge = el("header_state_badge");
  if (!badge) return;
  if (UX_STATE_MACHINE.recording) {
    badge.setAttribute("data-state", "recording");
    badge.textContent = t("state_recording");
  } else if (UX_STATE_MACHINE.micTesting) {
    badge.setAttribute("data-state", "recording");
    badge.textContent = t("state_testing");
  } else if (UX_STATE_MACHINE.running) {
    badge.setAttribute("data-state", "processing");
    badge.textContent = t("state_processing");
  } else {
    badge.setAttribute("data-state", "ready");
    badge.textContent = t("state_ready");
  }
}

function updateSettingsSummary() {
  const pill = el("settings_summary_pill");
  if (!pill) return;
  const whisper = (el("whisper_model") ? el("whisper_model").value : "").split("/").pop() || "whisper";
  const ollama = (el("ollama_model") ? el("ollama_model").value : "") || "ollama";
  const scheme = UX_STATE_MACHINE.colorScheme;
  const schemeTag = scheme === "light" ? "Light" : scheme === "dark" ? "Dark" : "Auto";
  const fit = UX_STATE_MACHINE.resourceFit;
  const warnFlag = fit && fit.level && fit.level !== "ok" ? " (!)" : "";
  pill.textContent = whisper + " · " + ollama + " · " + schemeTag + warnFlag;
}

function toggleLogDrawer(openState) {
  const drawer = el("log_drawer");
  if (!drawer) return;
  if (typeof openState === "boolean") {
    drawer.open = openState;
  } else {
    drawer.open = !drawer.open;
  }
}

/* ==========================================================================
   Recording, 4s Mic Test, Live Audio Feed & Playback
   ========================================================================== */

function updateRecordButtonText() {
  const btn = el("rec_btn");
  if (!btn) return;
  btn.textContent = UX_STATE_MACHINE.recording ? t("rec_stop") : t("rec_start");
  btn.classList.toggle("is-recording", UX_STATE_MACHINE.recording);
}

function updatePlayButtonText() {
  const btn = el("play_audio_btn");
  if (!btn) return;
  btn.textContent = UX_STATE_MACHINE.playing ? t("stop_audio") : t("play_audio");
}

function setScopeTelemetry(statusKey, levelObj) {
  const statusEl = el("scope_status_text");
  const dot = el("scope_dot");
  if (statusEl) statusEl.textContent = t(statusKey);
  if (dot) {
    dot.classList.toggle("live", UX_STATE_MACHINE.recording);
    dot.classList.toggle("test", UX_STATE_MACHINE.micTesting);
  }
  if (levelObj && el("live_level_badge")) {
    el("live_level_badge").textContent = levelObj.label || (levelObj.rms_dbfs + " dBFS");
  }
}

function updateMicTestState(state) {
  if (!state) return;
  const phase = state.phase || "done";
  const testBtn = el("mic_test_btn");
  const out = el("mic_test_result");

  if (phase === "recording") {
    UX_STATE_MACHINE.micTesting = true;
    if (testBtn) {
      testBtn.disabled = true;
      testBtn.textContent = t("mic_testing");
    }
    setScopeTelemetry("scope_testing", state.level || null);
    updateHeaderStateBadge();
  } else if (phase === "transcribing") {
    UX_STATE_MACHINE.micTesting = false;
    setScopeTelemetry("scope_transcribing", state.level || null);
    updateHeaderStateBadge();
    ensureScopeAnimating();
  } else {
    UX_STATE_MACHINE.micTesting = false;
    if (testBtn) {
      testBtn.disabled = false;
      testBtn.textContent = t("mic_test");
    }
    setScopeTelemetry("scope_standby", state.level || null);
    updateHeaderStateBadge();
    ensureScopeAnimating();

    if (out) {
      out.classList.remove("hidden");
      if (state.ok) {
        const snippet = state.stt_text ? "“" + state.stt_text + "”" : (UX_STATE_MACHINE.uiLang === "en" ? "(no speech detected)" : "(ingen tale oppdaget)");
        const lvl = state.level && state.level.label ? " · " + state.level.label : "";
        out.setAttribute("data-tone", "ok");
        out.textContent = snippet + lvl;
        if (state.audio_path && el("play_audio_btn")) {
          el("play_audio_btn").disabled = false;
        }
        if (state.detected_language) {
          onLanguageDetected({
            detected_language: state.detected_language,
            language_probability: state.language_probability,
            label_no: state.detected_label_no,
            label_en: state.detected_label_en,
            recommended_whisper: state.recommended_whisper,
          });
        }
      } else {
        out.setAttribute("data-tone", "danger");
        out.textContent = state.error || "Mic test failed";
      }
      announceStatus(out.textContent);
    }
  }
}

async function startRecordingToggle() {
  if (!hasApi()) return;
  const outDir = el("out_dir") ? el("out_dir").value : "";

  if (UX_STATE_MACHINE.recording) {
    UX_STATE_MACHINE.recording = false;
    if (UX_STATE_MACHINE.recTimerId) {
      clearInterval(UX_STATE_MACHINE.recTimerId);
      UX_STATE_MACHINE.recTimerId = null;
    }
    updateRecordButtonText();
    setScopeTelemetry("scope_standby", null);
    updateHeaderStateBadge();
    ensureScopeAnimating();

    const suggested = await window.pywebview.api.stop_recording(outDir);
    if (suggested && suggested.audio_path) {
      applySuggestedAudioState(suggested);
      const postBanner = el("post_rec_banner");
      const postName = el("post_rec_filename");
      if (postBanner && postName) {
        postName.textContent = suggested.audio_path.split(/[\\/]/).pop();
        postBanner.classList.remove("hidden");
      }
      announceStatus(t("post_rec_saved") + " " + suggested.audio_path);
    }
    return;
  }

  const postBanner = el("post_rec_banner");
  if (postBanner) postBanner.classList.add("hidden");

  const payload = {
    out_dir: outDir,
    speak_guide: Boolean(el("auto_speak_guide") && el("auto_speak_guide").checked),
    live_transcribe: Boolean(el("live_transcribe_chk") && el("live_transcribe_chk").checked),
    whisper_model: el("whisper_model") ? el("whisper_model").value : "NbAiLab/nb-whisper-small",
    language: UX_STATE_MACHINE.audioLang || "no",
    meeting_title: el("meeting_title") ? el("meeting_title").value : "",
    speaker_names_hint: el("speaker_names_hint") ? el("speaker_names_hint").value : "",
    agenda_text: el("agenda_text") ? el("agenda_text").value : "",
    mic_device: el("mic_device_select") ? el("mic_device_select").value : "default",
    gain_db: Number(el("mic_gain_select") ? el("mic_gain_select").value : 0),
    auto_gain: Boolean(el("mic_auto_gain") && el("mic_auto_gain").checked),
    ui_lang: UX_STATE_MACHINE.uiLang,
    note_mode: el("note_mode") ? el("note_mode").value : "mote",
  };

  const res = await window.pywebview.api.start_recording(payload);
  if (res && res.ok) {
    UX_STATE_MACHINE.recording = true;
    UX_STATE_MACHINE.recStartMs = Date.now();
    if (el("live_check_panel")) el("live_check_panel").textContent = "";
    if (el("rec_timer")) el("rec_timer").textContent = "00:00";
    UX_STATE_MACHINE.recTimerId = setInterval(() => {
      if (el("rec_timer")) {
        el("rec_timer").textContent = formatDuration((Date.now() - UX_STATE_MACHINE.recStartMs) / 1000);
      }
    }, 500);
    updateRecordButtonText();
    setScopeTelemetry("scope_recording", null);
    updateHeaderStateBadge();
    ensureScopeAnimating();
    announceStatus(t("state_recording"));
  } else if (res && res.error) {
    appendLogLine("FEIL: " + res.error);
  }
}

async function runMicTestAction() {
  if (!hasApi() || UX_STATE_MACHINE.recording) return;
  const out = el("mic_test_result");
  if (out) out.classList.add("hidden");
  updateMicTestState({ phase: "recording", duration_sec: 4 });
  await window.pywebview.api.test_microphone({
    mic_device: el("mic_device_select") ? el("mic_device_select").value : "default",
    gain_db: Number(el("mic_gain_select") ? el("mic_gain_select").value : 6),
    auto_gain: Boolean(el("mic_auto_gain") && el("mic_auto_gain").checked),
    duration_sec: 4.0,
    whisper_model: el("whisper_model") ? el("whisper_model").value : "NbAiLab/nb-whisper-small",
    language: UX_STATE_MACHINE.audioLang || "no",
  });
}

async function togglePlaySelectedAudio() {
  if (!hasApi()) return;
  const res = await window.pywebview.api.toggle_play_audio(UX_STATE_MACHINE.selectedAudioPath || "");
  if (res) {
    UX_STATE_MACHINE.playing = Boolean(res.playing);
    updatePlayButtonText();
  }
}

/* ==========================================================================
   Transcribe, Checkpoints, Validation, Files & Pipeline Controls
   ========================================================================== */

function applySuggestedAudioState(info) {
  if (!info || !info.audio_path) return;
  UX_STATE_MACHINE.selectedAudioPath = info.audio_path;
  if (el("selected-file")) el("selected-file").textContent = info.audio_path;
  if (el("out_dir") && info.out_dir) el("out_dir").value = info.out_dir;
  if (el("html_name") && info.html_name) el("html_name").value = info.html_name;
  if (el("notes_name") && info.notes_name) el("notes_name").value = info.notes_name;
  if (el("notes_html_name") && info.notes_html_name) el("notes_html_name").value = info.notes_html_name;

  if (el("run_btn")) el("run_btn").disabled = UX_STATE_MACHINE.running;
  if (el("check_audio_btn")) el("check_audio_btn").disabled = Boolean(info.is_transcript_input);
  if (el("detect_lang_btn")) el("detect_lang_btn").disabled = false;
  if (el("play_audio_btn")) el("play_audio_btn").disabled = Boolean(info.is_transcript_input);

  refreshCheckpointStatus();
  checkFilenameConflicts();
}

async function browseAudio() {
  if (!hasApi()) return;
  const res = await window.pywebview.api.pick_audio_file();
  if (res && res.audio_path) applySuggestedAudioState(res);
}

async function promptAudioUrl() {
  const input = el("media_url_input");
  if (input) {
    selectModeTab("transcribe");
    input.focus();
  } else {
    const url = window.prompt("URL:", "https://");
    if (url && hasApi()) {
      const outDir = el("out_dir") ? el("out_dir").value : "";
      const res = await window.pywebview.api.fetch_media_url(url, outDir);
      if (res && res.audio_path) applySuggestedAudioState(res);
    }
  }
}

async function fetchMediaFromInput() {
  if (!hasApi()) return;
  const inp = el("media_url_input");
  const err = el("media_url_error");
  if (!inp) return;
  const val = inp.value.trim();
  if (!/^https?:\/\//i.test(val)) {
    inp.setAttribute("aria-invalid", "true");
    if (err) {
      err.textContent = t("url_error");
      err.classList.remove("hidden");
    }
    return;
  }
  const outDir = el("out_dir") ? el("out_dir").value : "";
  const res = await window.pywebview.api.fetch_media_url(val, outDir);
  if (res && res.audio_path) {
    applySuggestedAudioState(res);
    inp.value = "";
  }
}

async function handleDroppedAudio(pathOrList) {
  if (!hasApi() || !pathOrList) return;
  const pathStr = Array.isArray(pathOrList) ? String(pathOrList[0] || "") : String(pathOrList);
  if (!pathStr) return;
  const lower = pathStr.toLowerCase();
  if (/\.(pptx|pdf|odp|docx|odt|csv)$/.test(lower)) {
    const txt = await window.pywebview.api.load_context_file_path(pathStr);
    if (txt && el("agenda_text")) {
      el("agenda_text").value = (el("agenda_text").value ? el("agenda_text").value + "\n\n" : "") + txt;
      setContextAvailability(true);
      const drawer = el("context_notes_drawer");
      if (drawer) drawer.open = true;
    }
    return;
  }
  const outDir = el("out_dir") ? el("out_dir").value : "";
  const res = await window.pywebview.api.suggest_output_names(pathStr, outDir);
  if (res && res.audio_path) {
    applySuggestedAudioState(res);
    selectModeTab("transcribe");
  }
}

async function refreshResumableSessions() {
  if (!hasApi()) return;
  const outDir = el("out_dir") ? el("out_dir").value : "";
  const list = await window.pywebview.api.list_resumable_sessions(outDir);
  UX_STATE_MACHINE.resumableSessions = Array.isArray(list) ? list : [];
  renderResumableSessionsSelect(UX_STATE_MACHINE.resumableSessions);
}

async function loadResumableSession(audioPath) {
  if (!hasApi() || !audioPath) return;
  const outDir = el("out_dir") ? el("out_dir").value : "";
  const res = await window.pywebview.api.suggest_output_names(audioPath, outDir);
  if (res && res.audio_path) applySuggestedAudioState(res);
}

async function refreshCheckpointStatus() {
  if (!hasApi()) return;
  const audioPath = UX_STATE_MACHINE.selectedAudioPath;
  const outDir = el("out_dir") ? el("out_dir").value : "";
  const hName = el("html_name") ? el("html_name").value : "";
  const nName = el("notes_name") ? el("notes_name").value : "";
  const nhName = el("notes_html_name") ? el("notes_html_name").value : "";

  const st = await window.pywebview.api.get_checkpoint_status(outDir, audioPath, hName, nName, nhName);
  if (!st) return;

  const map = [
    ["ckpt_s1", st.s1_asr],
    ["ckpt_s2", st.s2_align],
    ["ckpt_s3", st.s3_diarize],
    ["ckpt_s4", st.s4_names],
    ["ckpt_s5", st.s5_notes],
  ];
  for (const [id, done] of map) {
    const chip = el(id);
    if (chip) chip.classList.toggle("done", Boolean(done));
  }
  if (el("open_transcript_btn")) el("open_transcript_btn").disabled = !st.html_exists;
  if (el("open_notes_html_btn")) el("open_notes_html_btn").disabled = !st.notes_html_exists;
}

async function checkFilenameConflicts() {
  if (!hasApi()) return;
  const outDir = el("out_dir") ? el("out_dir").value : "";
  const hName = el("html_name") ? el("html_name").value : "";
  const nName = el("notes_name") ? el("notes_name").value : "";
  const nhName = el("notes_html_name") ? el("notes_html_name").value : "";
  const conflicts = await window.pywebview.api.check_conflicts(outDir, hName, nName, nhName);
  const msg = el("conflict-msg");
  if (!msg) return;
  if (Array.isArray(conflicts) && conflicts.length > 0) {
    msg.textContent = (UX_STATE_MACHINE.uiLang === "en" ? "Existing files will be reused/updated: " : "Eksisterende filer oppdateres: ") + conflicts.join(", ");
    msg.classList.remove("hidden");
  } else {
    msg.textContent = "";
    msg.classList.add("hidden");
  }
}

async function detectLanguageAction() {
  if (!hasApi()) return;
  const res = await window.pywebview.api.detect_language(UX_STATE_MACHINE.selectedAudioPath || "");
  if (res && res.ok && res.detected_language) {
    onLanguageDetected(res);
  } else if (res && res.error) {
    appendLogLine("Språkdeteksjon: " + res.error);
  }
}

async function runQuickAudioCheckAction() {
  if (!hasApi() || !UX_STATE_MACHINE.selectedAudioPath) return;
  if (el("live_check_panel")) el("live_check_panel").textContent = "";
  await window.pywebview.api.run_quick_audio_check(
    UX_STATE_MACHINE.selectedAudioPath,
    el("whisper_model") ? el("whisper_model").value : "NbAiLab/nb-whisper-small",
    UX_STATE_MACHINE.audioLang || "no",
    el("meeting_title") ? el("meeting_title").value : "",
    el("speaker_names_hint") ? el("speaker_names_hint").value : "",
    el("agenda_text") ? el("agenda_text").value : ""
  );
}

function validateSpeakerBounds() {
  const minEl = el("min_speakers");
  const maxEl = el("max_speakers");
  const errEl = el("speaker_bounds_error");
  if (!minEl || !maxEl) return true;
  const minVal = minEl.value.trim();
  const maxVal = maxEl.value.trim();
  let ok = true;
  if (minVal !== "" && maxVal !== "" && Number(minVal) > Number(maxVal)) {
    ok = false;
  }
  minEl.setAttribute("aria-invalid", ok ? "false" : "true");
  maxEl.setAttribute("aria-invalid", ok ? "false" : "true");
  if (errEl) {
    errEl.textContent = ok ? "" : t("speaker_bounds_error");
    errEl.classList.toggle("hidden", ok);
  }
  return ok;
}

async function startPipelineJob() {
  if (!hasApi() || !UX_STATE_MACHINE.selectedAudioPath) return;
  if (!validateSpeakerBounds()) return;

  await window.pywebview.api.start_job({
    audio_path: UX_STATE_MACHINE.selectedAudioPath,
    out_dir: el("out_dir") ? el("out_dir").value : "",
    html_name: el("html_name") ? el("html_name").value : "",
    notes_name: el("notes_name") ? el("notes_name").value : "",
    notes_html_name: el("notes_html_name") ? el("notes_html_name").value : "",
    whisper_model: el("whisper_model") ? el("whisper_model").value : "NbAiLab/nb-whisper-small",
    ollama_model: el("ollama_model") ? el("ollama_model").value : "gemma4:e4b",
    language: UX_STATE_MACHINE.audioLang || "no",
    hf_token: el("hf_token") ? el("hf_token").value : "",
    min_speakers: el("min_speakers") ? el("min_speakers").value : "",
    max_speakers: el("max_speakers") ? el("max_speakers").value : "",
    speaker_names_hint: el("speaker_names_hint") ? el("speaker_names_hint").value : "",
    meeting_title: el("meeting_title") ? el("meeting_title").value : "",
    agenda_text: el("agenda_text") ? el("agenda_text").value : "",
    note_mode: el("note_mode") ? el("note_mode").value : "auto",
    task_mode: el("task_mode") ? el("task_mode").value : "auto_resume",
    manual_notes_text: el("manual_notes_text") ? el("manual_notes_text").value : "",
  });
}

async function stopPipelineJob() {
  if (!hasApi()) return;
  await window.pywebview.api.stop_job();
}

function updateProgress(percent, label) {
  const bar = el("progress_bar");
  const valEl = el("progress_val");
  const labelEl = el("progress_label");
  const clamped = Math.max(0, Math.min(100, Number(percent) || 0));
  if (bar) bar.value = clamped;
  if (valEl) valEl.textContent = clamped + "%";
  if (labelEl && label) labelEl.textContent = label;
}

async function openGeneratedFile(kind) {
  if (!hasApi()) return;
  const outDir = el("out_dir") ? el("out_dir").value : "";
  const name = kind === "notes"
    ? (el("notes_name") ? el("notes_name").value : "")
    : kind === "notes_html"
      ? (el("notes_html_name") ? el("notes_html_name").value : "")
      : (el("html_name") ? el("html_name").value : "");
  await window.pywebview.api.open_file_in_os(name, outDir);
}

async function openFinder() {
  if (!hasApi()) return;
  const outDir = el("out_dir") ? el("out_dir").value : "";
  await window.pywebview.api.reveal_in_finder(outDir);
}

async function fetchContextFromLink() {
  if (!hasApi()) return;
  const inp = el("context_url_input");
  const err = el("context_url_error");
  if (!inp) return;
  const url = inp.value.trim();
  if (!/^https?:\/\//i.test(url)) {
    inp.setAttribute("aria-invalid", "true");
    if (err) {
      err.textContent = t("url_error");
      err.classList.remove("hidden");
    }
    return;
  }
  const res = await window.pywebview.api.fetch_context_url(url);
  if (res && res.ok && res.text && el("agenda_text")) {
    el("agenda_text").value = (el("agenda_text").value ? el("agenda_text").value + "\n\n" : "") + res.text;
    setContextAvailability(true);
    inp.value = "";
  }
}

async function browseContextFiles() {
  if (!hasApi()) return;
  const extracted = await window.pywebview.api.pick_agenda_file();
  if (extracted && el("agenda_text")) {
    el("agenda_text").value = (el("agenda_text").value ? el("agenda_text").value + "\n\n" : "") + extracted;
    setContextAvailability(true);
  }
}

async function browseOutputDir() {
  if (!hasApi()) return;
  const res = await window.pywebview.api.pick_output_dir(UX_STATE_MACHINE.selectedAudioPath || "");
  if (res) {
    if (res.audio_path) applySuggestedAudioState(res);
    else if (res.out_dir && el("out_dir")) {
      el("out_dir").value = res.out_dir;
      refreshCheckpointStatus();
      refreshResumableSessions();
    }
  }
}

function renderResourceBanner() {
  const banner = el("hw_resource_banner");
  const txt = el("hw_resource_text");
  const fit = UX_STATE_MACHINE.resourceFit;
  if (!banner || !txt || !fit) return;
  const msg = fit.message || fit.summary || fit.warning || "";
  txt.textContent = msg;
  const tone = fit.level === "critical" ? "danger" : (fit.level === "warning" || fit.fits === false ? "warn" : "ok");
  banner.setAttribute("data-tone", tone);
  banner.classList.toggle("hidden", !msg);
}

async function checkHardwareResourceFit() {
  if (!hasApi()) return;
  const w = el("whisper_model") ? el("whisper_model").value : "";
  const o = el("ollama_model") ? el("ollama_model").value : "";
  const d = el("out_dir") ? el("out_dir").value : "";
  const res = await window.pywebview.api.check_model_resources(w, o, d, UX_STATE_MACHINE.audioLang);
  if (res) {
    UX_STATE_MACHINE.hardware = res.hardware;
    UX_STATE_MACHINE.resourceFit = res.resource_fit;
    renderResourceBanner();
    updateSettingsSummary();
  }
}

async function applyRecommendedModels() {
  if (!hasApi()) return;
  const prof = el("model_profile") ? el("model_profile").value : "recommended";
  const d = el("out_dir") ? el("out_dir").value : "";
  const res = await window.pywebview.api.get_language_recommendations(UX_STATE_MACHINE.audioLang, prof, d);
  if (res) {
    const rec = res.recommendations || {};
    const recWhisper = rec.default_whisper || rec.whisper_model || res.chosen_whisper || "";
    const recOllama = rec.default_ollama || rec.ollama_model || res.chosen_ollama || "";
    if (el("whisper_model") && recWhisper) el("whisper_model").value = recWhisper;
    if (el("ollama_model") && recOllama) el("ollama_model").value = recOllama;
    if (res.hardware) UX_STATE_MACHINE.hardware = res.hardware;
    if (res.resource_fit) UX_STATE_MACHINE.resourceFit = res.resource_fit;
    renderResourceBanner();
    updateSettingsSummary();
  }
}

async function openSoundSettingsAction() {
  if (!hasApi()) return;
  await window.pywebview.api.open_sound_settings();
}

async function installStenoShortcut() {
  if (!hasApi()) return;
  const res = await window.pywebview.api.install_app(UX_STATE_MACHINE.uiLang);
  if (res && res.message) {
    appendLogLine("==> " + res.message);
    announceStatus(res.message);
  }
}

function openUninstallDialog() {
  const dlg = el("uninstall_modal");
  if (!dlg) return;
  if (typeof dlg.showModal === "function") {
    dlg.showModal();
  } else {
    dlg.setAttribute("open", "");
  }
}

function closeUninstallDialog() {
  const dlg = el("uninstall_modal");
  if (!dlg) return;
  if (typeof dlg.close === "function") {
    dlg.close();
  } else {
    dlg.removeAttribute("open");
  }
}

async function confirmUninstallAction() {
  closeUninstallDialog();
  if (hasApi()) {
    await window.pywebview.api.uninstall_app(UX_STATE_MACHINE.uiLang);
  }
}

async function saveLogToFile() {
  if (!hasApi() || !el("log")) return;
  const outDir = el("out_dir") ? el("out_dir").value : "";
  await window.pywebview.api.save_log_dialog(el("log").textContent || "", outDir, UX_STATE_MACHINE.selectedAudioPath || "");
}

/* ==========================================================================
   Structured Backend Event Dispatcher & Safe Legacy Dispatch (NO eval)
   ========================================================================== */

window.__stenoDispatchEvent = function (type, payload) {
  const data = payload || {};
  if (type === "waveform") {
    pushWaveformSamples(data.samples);
    if (data.level && el("live_level_badge")) {
      el("live_level_badge").textContent = data.level.label || (data.level.rms_dbfs + " dBFS");
    }
    return;
  }
  if (type === "live_level") {
    if (el("live_level_badge")) {
      el("live_level_badge").textContent = data.label || (data.rms_dbfs + " dBFS");
    }
    return;
  }
  if (type === "live_chunk") {
    const panel = el("live_check_panel");
    if (panel && data.line) {
      panel.textContent += (panel.textContent ? "\n" : "") + data.line;
      panel.scrollTop = panel.scrollHeight;
    }
    if (data.level && el("live_level_badge")) {
      el("live_level_badge").textContent = data.level.label || (data.level.rms_dbfs + " dBFS");
    }
    return;
  }
  if (type === "mic_test") {
    updateMicTestState(data);
    return;
  }
  if (type === "language_detected") {
    onLanguageDetected(data);
    return;
  }
  if (type === "log") {
    appendLogLine(data.line);
    return;
  }
  if (type === "progress") {
    updateProgress(data.percent, data.label);
    return;
  }
  if (type === "running") {
    UX_STATE_MACHINE.running = Boolean(data.running);
    if (el("run_btn")) el("run_btn").disabled = UX_STATE_MACHINE.running || !UX_STATE_MACHINE.selectedAudioPath;
    if (el("stop_btn")) el("stop_btn").disabled = !UX_STATE_MACHINE.running;
    if (el("rec_btn")) el("rec_btn").disabled = UX_STATE_MACHINE.running;
    updateHeaderStateBadge();
    if (!UX_STATE_MACHINE.running) {
      refreshCheckpointStatus();
      refreshResumableSessions();
    }
    return;
  }
  if (type === "checkpoints_dirty") {
    refreshCheckpointStatus();
    return;
  }
  if (type === "playback_state") {
    UX_STATE_MACHINE.playing = Boolean(data.playing);
    updatePlayButtonText();
  }
};

function parseSafeCallArgs(rawArgs) {
  const text = rawArgs.trim();
  if (!text) return [];
  let inSingle = false;
  let inDouble = false;
  let escaped = false;
  let jsonified = "";
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (escaped) {
      jsonified += ch;
      escaped = false;
      continue;
    }
    if (ch === "\\") {
      jsonified += ch;
      escaped = true;
      continue;
    }
    if (ch === "'" && !inDouble) {
      inSingle = !inSingle;
      jsonified += '"';
      continue;
    }
    if (ch === '"' && inSingle) {
      jsonified += '\\"';
      continue;
    }
    if (ch === '"') {
      inDouble = !inDouble;
    }
    jsonified += ch;
  }
  return JSON.parse("[" + jsonified + "]");
}

window.__stenoDispatchJsCall = function (code) {
  const allowed = {
    selectModeTab,
    browseAudio,
    promptAudioUrl,
    openGeneratedFile,
    openFinder,
    installStenoShortcut,
    openUninstallDialog,
    setSessionFormat,
    setUiLanguage,
    setAudioLanguage,
    setColorScheme,
    handleDroppedAudio,
    updateProgress,
  };
  const statements = String(code || "")
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean);
  for (const stmt of statements) {
    const m = /^([A-Za-z_][A-Za-z0-9_]*)\(([\s\S]*)\)$/.exec(stmt);
    if (!m) continue;
    const fn = allowed[m[1]];
    if (typeof fn !== "function") continue;
    try {
      const args = parseSafeCallArgs(m[2]);
      fn(...args);
    } catch (_err) {}
  }
};

/* ==========================================================================
   Initialization, Event Bindings, Dropzone & Keyboard Tabs
   ========================================================================== */

let _stenoInitialized = false;

async function initStenoApp() {
  if (_stenoInitialized || !hasApi()) return;
  _stenoInitialized = true;

  // Bind WAI-ARIA Tabs (ArrowLeft/Right/Home/End + beforematch)
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  tabs.forEach((tab, idx) => {
    tab.addEventListener("click", () => {
      const mode = tab.id.replace("tab_", "");
      selectModeTab(mode);
    });
    tab.addEventListener("keydown", (e) => {
      let nextIdx = -1;
      if (e.key === "ArrowRight") nextIdx = (idx + 1) % tabs.length;
      else if (e.key === "ArrowLeft") nextIdx = (idx - 1 + tabs.length) % tabs.length;
      else if (e.key === "Home") nextIdx = 0;
      else if (e.key === "End") nextIdx = tabs.length - 1;
      if (nextIdx >= 0) {
        e.preventDefault();
        const target = tabs[nextIdx];
        selectModeTab(target.id.replace("tab_", ""));
        target.focus();
      }
    });
  });

  document.querySelectorAll('[role="tabpanel"]').forEach((panel) => {
    panel.addEventListener("beforematch", () => {
      const mode = panel.id.replace("panel_", "");
      selectModeTab(mode);
    });
  });

  // Dropzone events
  const dz = el("dropzone");
  if (dz) {
    dz.addEventListener("dragover", (e) => {
      e.preventDefault();
      dz.classList.add("drag-over");
    });
    dz.addEventListener("dragleave", () => dz.classList.remove("drag-over"));
    dz.addEventListener("drop", (e) => {
      e.preventDefault();
      dz.classList.remove("drag-over");
      const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (file && file.path) handleDroppedAudio(file.path);
    });
  }

  // Blur validation with live reset on input
  ["min_speakers", "max_speakers"].forEach((id) => {
    const input = el(id);
    if (!input) return;
    input.addEventListener("blur", validateSpeakerBounds);
    input.addEventListener("input", () => {
      input.setAttribute("aria-invalid", "false");
      if (el("speaker_bounds_error")) el("speaker_bounds_error").classList.add("hidden");
    });
  });

  [
    ["media_url_input", "media_url_error"],
    ["context_url_input", "context_url_error"],
  ].forEach(([inpId, errId]) => {
    const input = el(inpId);
    if (!input) return;
    input.addEventListener("blur", () => {
      const v = input.value.trim();
      const bad = v !== "" && !/^https?:\/\//i.test(v);
      input.setAttribute("aria-invalid", bad ? "true" : "false");
      if (el(errId)) {
        el(errId).textContent = bad ? t("url_error") : "";
        el(errId).classList.toggle("hidden", !bad);
      }
    });
    input.addEventListener("input", () => {
      input.setAttribute("aria-invalid", "false");
      if (el(errId)) el(errId).classList.add("hidden");
    });
  });

  const outDirInp = el("out_dir");
  if (outDirInp) {
    outDirInp.addEventListener("blur", () => {
      const bad = outDirInp.value.trim() === "";
      outDirInp.setAttribute("aria-invalid", bad ? "true" : "false");
      if (el("out_dir_error")) {
        el("out_dir_error").textContent = bad ? t("dir_error") : "";
        el("out_dir_error").classList.toggle("hidden", !bad);
      }
    });
    outDirInp.addEventListener("input", () => {
      outDirInp.setAttribute("aria-invalid", "false");
      if (el("out_dir_error")) el("out_dir_error").classList.add("hidden");
      refreshCheckpointStatus();
    });
  }

  // Model / profile / output change hooks
  const profileSel = el("model_profile");
  if (profileSel) {
    profileSel.addEventListener("change", applyRecommendedModels);
  }
  const guideChk = el("auto_speak_guide");
  if (guideChk) {
    guideChk.addEventListener("change", () => {
      const banner = el("prompt_banner");
      if (!banner) return;
      banner.textContent = t("prompt_guide_hint");
      banner.classList.toggle("hidden", !guideChk.checked);
    });
  }
  ["whisper_model", "ollama_model"].forEach((id) => {
    const node = el(id);
    if (node) {
      node.addEventListener("change", () => {
        checkHardwareResourceFit();
        if (hasApi()) {
          window.pywebview.api.save_preferences({
            language_models: {
              [UX_STATE_MACHINE.audioLang]: {
                whisper_model: el("whisper_model") ? el("whisper_model").value : "",
                ollama_model: el("ollama_model") ? el("ollama_model").value : "",
              },
            },
          }).catch(() => {});
        }
      });
    }
  });
  ["html_name", "notes_name", "notes_html_name"].forEach((id) => {
    const node = el(id);
    if (node) node.addEventListener("change", () => { checkFilenameConflicts(); refreshCheckpointStatus(); });
  });

  // Mic volume slider
  const sysVol = el("sys_mic_vol");
  if (sysVol) {
    sysVol.addEventListener("change", () => {
      if (hasApi()) window.pywebview.api.set_mic_volume(Number(sysVol.value));
    });
  }

  // Native <dialog> outside-click fallback ONLY when closedby is unsupported
  const uninstallDlg = el("uninstall_modal");
  if (uninstallDlg && !("closedBy" in HTMLDialogElement.prototype)) {
    let downOutside = false;
    uninstallDlg.addEventListener("pointerdown", (e) => {
      const r = uninstallDlg.getBoundingClientRect();
      downOutside = e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
    });
    uninstallDlg.addEventListener("pointerup", (e) => {
      const r = uninstallDlg.getBoundingClientRect();
      const upOutside = e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      if (downOutside && upOutside) closeUninstallDialog();
      downOutside = false;
    });
  }

  // OS color scheme listener for canvas live switch
  if (window.matchMedia) {
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    if (typeof mql.addEventListener === "function") {
      mql.addEventListener("change", () => drawScopeOnce());
    }
  }

  // Fetch defaults from Python backend
  try {
    const defaults = await window.pywebview.api.get_defaults();
    if (defaults) {
      UX_STATE_MACHINE.supportedLanguages = defaults.supported_languages || [];
      UX_STATE_MACHINE.hardware = defaults.hardware || null;
      UX_STATE_MACHINE.resourceFit = defaults.resource_fit || null;
      UX_STATE_MACHINE.profiles = defaults.profiles || null;

      if (el("out_dir")) el("out_dir").value = defaults.default_out_dir || "";
      if (el("meeting_title") && !el("meeting_title").value) {
        el("meeting_title").value = defaults.default_meeting_title || "";
      }
      if (defaults.hardware) {
        if (el("whisper_model")) el("whisper_model").value = defaults.hardware.recommended_whisper || "";
        if (el("ollama_model")) el("ollama_model").value = defaults.hardware.recommended_ollama || "";
        if (el("model_profile") && defaults.hardware.recommended_profile) {
          el("model_profile").value = defaults.hardware.recommended_profile;
        }
      }
      if (defaults.hf_token_configured && el("hf_token_status")) {
        el("hf_token_status").textContent = "HF token configured";
      } else if (defaults.diarization_cached && el("hf_token_status")) {
        el("hf_token_status").textContent = "pyannote cached";
      }
      UX_STATE_MACHINE.audioInputs = Array.isArray(defaults.audio_inputs) ? defaults.audio_inputs : [];
      UX_STATE_MACHINE.resumableSessions = Array.isArray(defaults.resumable_sessions) ? defaults.resumable_sessions : [];
      if (defaults.system_input_volume !== null && defaults.system_input_volume !== undefined && el("sys_mic_vol")) {
        el("sys_mic_vol").value = String(defaults.system_input_volume);
      }

      UX_STATE_MACHINE.audioLang = defaults.audio_language || "no";
      setUiLanguage(defaults.ui_lang || "no", false);
      setAudioLanguage(defaults.audio_language || "no", false);
      setColorScheme(defaults.color_scheme || "system", false);
      populateMicDevices();
      renderResumableSessionsSelect(UX_STATE_MACHINE.resumableSessions);
      renderResourceBanner();
      updateSettingsSummary();
    }
  } catch (err) {
    appendLogLine("Init error: " + err);
  }

  drawScopeOnce();
}

window.addEventListener("pywebviewready", initStenoApp);
window.addEventListener("DOMContentLoaded", () => {
  applyTranslations();
  drawScopeOnce();
  if (hasApi()) initStenoApp();
});
window.addEventListener("resize", () => drawScopeOnce());
