const GOOGLE_MAPS_API_KEY = "PASTE_YOUR_GOOGLE_MAPS_API_KEY_HERE";

const translations = {
  de: {
    gateHint: "Finde die Tür.",
    strikeInstruction: "Reibe schnell über die rote Fläche",
    gateTitle: "Nicht jeder Eingang ist sichtbar.",
    gateText: "Entzünde das Streichholz und suche im Dunkeln nach der Tür.",
    skip: "Überspringen",
    brandMeta: "24/7 offen | Bespielbar nur mit Termin",
    bookNow: "Termin buchen",
    buyVoucher: "Gutschein kaufen",
    heroEyebrow: "Escape Room Schenna",
    heroTitle: "Rätsel, Spannung und ein Erlebnis, das ihr gemeinsam löst.",
    heroLead: "24/7 offen | Bespielbar nur mit Termin",
    bookingNote1Title: "Buchungen innerhalb 24h",
    bookingNote1Text: "Bestätigung oder Ablehnung innerhalb 1 Stunde.",
    bookingNote2Title: "Buchungen innerhalb 2 Wochen",
    bookingNote2Text: "Automatisch bestätigt, wenn verfügbar.",
    bookingNote3Title: "Spätere Buchungen",
    bookingNote3Text: "Bestätigung oder Ablehnung innerhalb 1 Stunde.",
    heroVisualText: "Warme Töne, dunkle Stimmung und ein Einstieg, der neugierig macht.",
    aboutEyebrow: "Was ist ein Escape Room?",
    aboutTitle: "Ein Spiel, bei dem ihr Hinweise kombiniert, Rätsel löst und gemeinsam ans Ziel kommt.",
    aboutText1: "In einem Escape Room löst ihr als Team verschiedene Rätsel, entdeckt versteckte Hinweise und verbindet Informationen miteinander. Ziel ist es, innerhalb der vorgegebenen Zeit die Geschichte zu verstehen und die Aufgabe zu meistern.",
    aboutText2: "Ihr braucht dafür keine Vorkenntnisse. Wichtig sind Neugier, Kommunikation und Teamwork. Der Fokus liegt auf Erlebnis, Spannung und gemeinsamem Denken.",
    bookingEyebrow: "Buchung",
    bookingTitle: "Wähle zuerst einen Tag, dann eine freie Uhrzeit.",
    legendAvailable: "frei",
    legendSelected: "gewählt",
    legendFull: "voll",
    freeTimes: "Freie Zeiten",
    selectDayPrompt: "Bitte zuerst einen freien Tag wählen.",
    requestTitle: "Termin anfragen",
    selectSlotPrompt: "Bitte zuerst einen Slot wählen.",
    fieldName: "Name",
    fieldEmail: "E-Mail",
    fieldPhone: "Telefon",
    fieldLanguage: "Sprache",
    fieldPeople: "Personenanzahl",
    fieldVoucher: "Gutscheincode",
    fieldMessage: "Nachricht",
    fieldMessagePlaceholder: "Optional",
    actionRequest: "Termin anfragen",
    actionRequestText: "Anfrage ohne sofortige Zahlung senden.",
    actionPay: "Jetzt bezahlen",
    actionPayText: "Stripe-Checkout später mit echtem Backend anbinden.",
    actionOnsite: "Vor Ort bezahlen",
    actionOnsiteText: "Slot anfragen und Zahlung später vor Ort durchführen.",
    contactEyebrow: "Kontakt & Lage",
    contactTitle: "So findet ihr uns.",
    mapHelp: "Für die echte Karte bitte in app.js deinen Google-Maps-API-Key eintragen.",
    addressLabel: "Adresse",
    phoneLabel: "Telefon",
    emailLabel: "E-Mail",
    warningTitle: "Wichtiger Hinweis",
    warningText: "Keine Toiletten vorhanden.",
    routeEscape: "Route zum Escape Room",
    routeParking: "Route zum Parkplatz",
    bookingSuccessRequest: "Termin angefragt. In V1 wird die Anfrage lokal simuliert.",
    bookingSuccessPay: "Termin angefragt. Hier würdest du später Stripe Checkout starten.",
    bookingSuccessOnsite: "Termin angefragt. Zahlung ist als vor Ort markiert.",
    selectDayError: "Bitte zuerst einen freien Tag wählen.",
    selectSlotError: "Bitte zuerst einen freien Slot wählen.",
    weekdays: ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    months: ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
    selectedDatePrefix: "Ausgewählter Tag:",
    selectedSlotPrefix: "Ausgewählter Slot:"
  },
  it: {
    gateHint: "Trova la porta.",
    strikeInstruction: "Strofina velocemente sulla superficie rossa",
    gateTitle: "Non tutti gli ingressi sono visibili.",
    gateText: "Accendi il fiammifero e cerca la porta nel buio.",
    skip: "Salta",
    brandMeta: "Aperto 24/7 | Giocabile solo su prenotazione",
    bookNow: "Prenota ora",
    buyVoucher: "Acquista buono",
    heroEyebrow: "Escape Room Schenna",
    heroTitle: "Enigmi, tensione e un'esperienza da risolvere insieme.",
    heroLead: "Aperto 24/7 | Giocabile solo su prenotazione",
    bookingNote1Title: "Prenotazioni entro 24h",
    bookingNote1Text: "Conferma o rifiuto entro 1 ora.",
    bookingNote2Title: "Prenotazioni entro 2 settimane",
    bookingNote2Text: "Conferma automatica se disponibile.",
    bookingNote3Title: "Prenotazioni oltre 2 settimane",
    bookingNote3Text: "Conferma o rifiuto entro 1 ora.",
    heroVisualText: "Toni caldi, atmosfera scura e un ingresso che incuriosisce.",
    aboutEyebrow: "Cos'è un Escape Room?",
    aboutTitle: "Un gioco in cui combinate indizi, risolvete enigmi e raggiungete insieme l'obiettivo.",
    aboutText1: "In un escape room risolvete enigmi in squadra, scoprite indizi nascosti e collegate le informazioni. L'obiettivo è capire la storia e completare la missione entro il tempo previsto.",
    aboutText2: "Non servono conoscenze particolari. Sono importanti curiosità, comunicazione e spirito di squadra. L'attenzione è su esperienza, suspense e pensiero condiviso.",
    bookingEyebrow: "Prenotazione",
    bookingTitle: "Scegli prima un giorno, poi un orario disponibile.",
    legendAvailable: "libero",
    legendSelected: "selezionato",
    legendFull: "pieno",
    freeTimes: "Orari disponibili",
    selectDayPrompt: "Seleziona prima un giorno disponibile.",
    requestTitle: "Richiedi appuntamento",
    selectSlotPrompt: "Seleziona prima uno slot.",
    fieldName: "Nome",
    fieldEmail: "E-mail",
    fieldPhone: "Telefono",
    fieldLanguage: "Lingua",
    fieldPeople: "Numero persone",
    fieldVoucher: "Codice buono",
    fieldMessage: "Messaggio",
    fieldMessagePlaceholder: "Opzionale",
    actionRequest: "Richiedi appuntamento",
    actionRequestText: "Invia una richiesta senza pagamento immediato.",
    actionPay: "Paga ora",
    actionPayText: "Collega più tardi Stripe Checkout con un backend reale.",
    actionOnsite: "Paga sul posto",
    actionOnsiteText: "Richiedi lo slot e paga più tardi sul posto.",
    contactEyebrow: "Contatto & posizione",
    contactTitle: "Come trovarci.",
    mapHelp: "Per la mappa reale inserisci la tua chiave API Google Maps in app.js.",
    addressLabel: "Indirizzo",
    phoneLabel: "Telefono",
    emailLabel: "E-mail",
    warningTitle: "Avviso importante",
    warningText: "Non ci sono servizi igienici.",
    routeEscape: "Percorso per Escape Room",
    routeParking: "Percorso per il parcheggio",
    bookingSuccessRequest: "Appuntamento richiesto. Nella V1 la richiesta viene simulata localmente.",
    bookingSuccessPay: "Appuntamento richiesto. Qui in seguito partirà Stripe Checkout.",
    bookingSuccessOnsite: "Appuntamento richiesto. Il pagamento è segnato come sul posto.",
    selectDayError: "Seleziona prima un giorno disponibile.",
    selectSlotError: "Seleziona prima uno slot disponibile.",
    weekdays: ["Lu", "Ma", "Me", "Gi", "Ve", "Sa", "Do"],
    months: ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"],
    selectedDatePrefix: "Giorno selezionato:",
    selectedSlotPrefix: "Slot selezionato:"
  },
  en: {
    gateHint: "Find the door.",
    strikeInstruction: "Rub quickly across the red surface",
    gateTitle: "Not every entrance is visible.",
    gateText: "Light the match and search for the door in the dark.",
    skip: "Skip",
    brandMeta: "Open 24/7 | Playable by appointment only",
    bookNow: "Book now",
    buyVoucher: "Buy voucher",
    heroEyebrow: "Escape Room Schenna",
    heroTitle: "Puzzles, tension and an experience you solve together.",
    heroLead: "Open 24/7 | Playable by appointment only",
    bookingNote1Title: "Bookings within 24h",
    bookingNote1Text: "Accepted or declined within 1 hour.",
    bookingNote2Title: "Bookings within 2 weeks",
    bookingNote2Text: "Automatically confirmed if available.",
    bookingNote3Title: "Later bookings",
    bookingNote3Text: "Accepted or declined within 1 hour.",
    heroVisualText: "Warm tones, dark atmosphere and an intro that sparks curiosity.",
    aboutEyebrow: "What is an escape room?",
    aboutTitle: "A game where you combine clues, solve puzzles and reach the goal together.",
    aboutText1: "In an escape room you solve puzzles as a team, discover hidden clues and connect pieces of information. The goal is to understand the story and complete the mission within the given time.",
    aboutText2: "You do not need prior experience. Curiosity, communication and teamwork matter most. The focus is on atmosphere, suspense and solving things together.",
    bookingEyebrow: "Booking",
    bookingTitle: "Choose a day first, then select an available time.",
    legendAvailable: "available",
    legendSelected: "selected",
    legendFull: "full",
    freeTimes: "Available times",
    selectDayPrompt: "Please choose an available day first.",
    requestTitle: "Request booking",
    selectSlotPrompt: "Please choose a slot first.",
    fieldName: "Name",
    fieldEmail: "Email",
    fieldPhone: "Phone",
    fieldLanguage: "Language",
    fieldPeople: "Number of players",
    fieldVoucher: "Voucher code",
    fieldMessage: "Message",
    fieldMessagePlaceholder: "Optional",
    actionRequest: "Request booking",
    actionRequestText: "Send a request without immediate payment.",
    actionPay: "Pay now",
    actionPayText: "Later connect this to Stripe Checkout with a real backend.",
    actionOnsite: "Pay on site",
    actionOnsiteText: "Request the slot and pay later on site.",
    contactEyebrow: "Contact & location",
    contactTitle: "How to find us.",
    mapHelp: "For the real map, add your Google Maps API key in app.js.",
    addressLabel: "Address",
    phoneLabel: "Phone",
    emailLabel: "Email",
    warningTitle: "Important note",
    warningText: "No toilets available.",
    routeEscape: "Route to Escape Room",
    routeParking: "Route to parking",
    bookingSuccessRequest: "Booking requested. In V1 this is simulated locally.",
    bookingSuccessPay: "Booking requested. Stripe Checkout would start here later.",
    bookingSuccessOnsite: "Booking requested. Payment is marked as on-site.",
    selectDayError: "Please choose an available day first.",
    selectSlotError: "Please choose an available slot first.",
    weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    months: ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    selectedDatePrefix: "Selected day:",
    selectedSlotPrefix: "Selected slot:"
  }
};

const state = {
  lang: "de",
  gateMode: "minimal",
  gateLit: false,
  selectedDate: null,
  selectedSlot: null,
  calendarDate: new Date(new Date().getFullYear(), new Date().getMonth(), 1)
};

const mapConfig = {
  escape: { lat: 46.692840489758765, lng: 11.189778092793254 },
  parking: { lat: 46.6930586220878, lng: 11.188709004823197 }
};

const bookings = createMockAvailability();

const el = {
  gate: document.getElementById("gate"),
  gateScene: document.getElementById("gateScene"),
  lightMask: document.getElementById("lightMask"),
  strikeZone: document.getElementById("strikeZone"),
  strikeProgress: document.getElementById("strikeProgress"),
  doorButton: document.getElementById("doorButton"),
  skipGateBtn: document.getElementById("skipGateBtn"),
  flame: document.getElementById("flame"),
  monthLabel: document.getElementById("calendarMonthLabel"),
  weekdays: document.getElementById("calendarWeekdays"),
  calendarGrid: document.getElementById("calendarGrid"),
  slotList: document.getElementById("slotList"),
  selectedDateLabel: document.getElementById("selectedDateLabel"),
  selectedSlotLabel: document.getElementById("selectedSlotLabel"),
  prevMonthBtn: document.getElementById("prevMonthBtn"),
  nextMonthBtn: document.getElementById("nextMonthBtn"),
  feedback: document.getElementById("bookingFeedback"),
  bookingForm: document.getElementById("bookingForm")
};

init();

function init() {
  applyTranslations();
  bindLanguageSwitcher();
  bindGateModeSwitcher();
  setupGate();
  setupCalendar();
  setupBookingActions();
  initMap();
  showSkipButtonLogic();
}

function applyTranslations() {
  const t = translations[state.lang];
  document.documentElement.lang = state.lang;

  document.querySelectorAll("[data-i18n]").forEach(node => {
    const key = node.dataset.i18n;
    if (t[key]) node.textContent = t[key];
  });

  document.querySelectorAll("[data-i18n-placeholder]").forEach(node => {
    const key = node.dataset.i18nPlaceholder;
    if (t[key]) node.setAttribute("placeholder", t[key]);
  });

  document.querySelectorAll(".lang-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.lang === state.lang);
  });

  renderWeekdays();
  renderCalendar();
  updateSelectedLabels();
}

function bindLanguageSwitcher() {
  document.querySelectorAll(".lang-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      state.lang = btn.dataset.lang;
      applyTranslations();
    });
  });
}

function bindGateModeSwitcher() {
  document.querySelectorAll(".gate-mode-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      state.gateMode = btn.dataset.mode;
      document.querySelectorAll(".gate-mode-btn").forEach(b => b.classList.toggle("active", b === btn));
      el.gateScene.classList.toggle("gate-scene--minimal", state.gateMode === "minimal");
      el.gateScene.classList.toggle("gate-scene--atmospheric", state.gateMode === "atmospheric");
    });
  });
}

function setupGate() {
  let samples = [];
  let lastPoint = null;

  const moveHandler = (clientX, clientY) => {
    if (state.gateLit) {
      el.lightMask.classList.remove("hidden");
      el.lightMask.classList.add("is-on");
      el.lightMask.style.left = `${clientX}px`;
      el.lightMask.style.top = `${clientY}px`;
    }

    const rect = el.strikeZone.getBoundingClientRect();
    const inside = clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
    if (!inside) {
      lastPoint = null;
      return;
    }

    const now = performance.now();
    if (lastPoint) {
      const dx = clientX - lastPoint.x;
      const dy = clientY - lastPoint.y;
      const dt = Math.max(now - lastPoint.t, 1);
      const speed = Math.sqrt(dx * dx + dy * dy) / dt;
      samples.push({ t: now, speed });
      samples = samples.filter(s => now - s.t < 700);
      const energy = Math.min(1, samples.reduce((sum, s) => sum + s.speed, 0) / 4.2);
      el.strikeProgress.style.setProperty("--progress", `${energy * 100}%`);

      if (!state.gateLit && energy > 0.98) {
        lightMatch();
      }
    }
    lastPoint = { x: clientX, y: clientY, t: now };
  };

  window.addEventListener("pointermove", e => moveHandler(e.clientX, e.clientY), { passive: true });
  window.addEventListener("pointerdown", e => moveHandler(e.clientX, e.clientY), { passive: true });

  el.skipGateBtn.addEventListener("click", openSite);
  el.doorButton.addEventListener("click", () => {
    if (state.gateLit) openSite();
  });
}

function lightMatch() {
  state.gateLit = true;
  el.gateScene.classList.add("is-lit");
  localStorage.setItem("escapeRoomGateSeen", "1");
}

function showSkipButtonLogic() {
  const seen = localStorage.getItem("escapeRoomGateSeen") === "1";
  if (seen) {
    el.skipGateBtn.classList.remove("hidden");
  } else {
    setTimeout(() => el.skipGateBtn.classList.remove("hidden"), 2000);
  }
}

function openSite() {
  el.gate.classList.remove("active");
  el.gate.setAttribute("aria-hidden", "true");
}

function createMockAvailability() {
  const result = {};
  const now = new Date();
  for (let i = 0; i < 120; i++) {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i);
    const key = formatDateKey(d);

    const weekday = d.getDay();
    const slots = generateHalfHourSlots("10:00", "20:00").filter((slot, idx) => {
      if (weekday === 1 && idx > 8) return false;
      if (weekday === 2 && idx % 3 === 0) return false;
      if (weekday === 3 && idx % 4 === 0) return false;
      if (weekday === 4 && idx > 10) return false;
      if (weekday === 5 && idx % 5 === 0) return false;
      if (weekday === 6) return idx < 14;
      if (weekday === 0) return idx < 10;
      return true;
    });

    const fullyClosed = i % 7 === 0 || i % 11 === 0;
    result[key] = fullyClosed ? [] : slots.slice(0, Math.max(2, slots.length - (i % 4)));
  }
  return result;
}

function generateHalfHourSlots(start, end) {
  const [sh, sm] = start.split(":").map(Number);
  const [eh, em] = end.split(":").map(Number);
  const slots = [];
  let minutes = sh * 60 + sm;
  const endMinutes = eh * 60 + em;

  while (minutes <= endMinutes - 120) {
    slots.push(minutesToTime(minutes));
    minutes += 30;
  }
  return slots;
}

function minutesToTime(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function setupCalendar() {
  el.prevMonthBtn.addEventListener("click", () => {
    state.calendarDate = new Date(state.calendarDate.getFullYear(), state.calendarDate.getMonth() - 1, 1);
    renderCalendar();
  });

  el.nextMonthBtn.addEventListener("click", () => {
    state.calendarDate = new Date(state.calendarDate.getFullYear(), state.calendarDate.getMonth() + 1, 1);
    renderCalendar();
  });

  renderWeekdays();
  renderCalendar();
}

function renderWeekdays() {
  const t = translations[state.lang];
  el.weekdays.innerHTML = "";
  t.weekdays.forEach(day => {
    const span = document.createElement("span");
    span.textContent = day;
    el.weekdays.appendChild(span);
  });
}

function renderCalendar() {
  const t = translations[state.lang];
  const year = state.calendarDate.getFullYear();
  const month = state.calendarDate.getMonth();

  el.monthLabel.textContent = `${t.months[month]} ${year}`;
  el.calendarGrid.innerHTML = "";

  const firstDay = new Date(year, month, 1);
  const startWeekday = (firstDay.getDay() + 6) % 7;
  const gridStart = new Date(year, month, 1 - startWeekday);

  for (let i = 0; i < 42; i++) {
    const current = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i);
    const btn = document.createElement("button");
    btn.className = "day-btn";
    btn.textContent = current.getDate();

    const key = formatDateKey(current);
    const isCurrentMonth = current.getMonth() === month;
    const isPast = stripTime(current) < stripTime(new Date());
    const availableSlots = bookings[key] || [];
    const hasAvailability = availableSlots.length > 0;
    const isSelected = state.selectedDate === key;

    if (!isCurrentMonth) btn.classList.add("is-other-month");
    if (isPast || !hasAvailability) btn.classList.add("is-disabled");
    if (!isPast && hasAvailability && isCurrentMonth) btn.classList.add("is-available");
    if (isSelected) btn.classList.add("is-selected");

    if (!isPast && hasAvailability && isCurrentMonth) {
      btn.addEventListener("click", () => {
        state.selectedDate = key;
        state.selectedSlot = null;
        renderCalendar();
        renderSlots();
        updateSelectedLabels();
      });
    }

    el.calendarGrid.appendChild(btn);
  }

  renderSlots();
}

function renderSlots() {
  el.slotList.innerHTML = "";
  if (!state.selectedDate) return;

  const slots = bookings[state.selectedDate] || [];
  slots.forEach(slot => {
    const btn = document.createElement("button");
    btn.className = "slot-btn";
    btn.textContent = `${slot} · 2h`;
    btn.addEventListener("click", () => {
      state.selectedSlot = slot;
      document.querySelectorAll(".slot-btn").forEach(node => node.classList.remove("active"));
      btn.classList.add("active");
      updateSelectedLabels();
    });
    el.slotList.appendChild(btn);
  });
}

function updateSelectedLabels() {
  const t = translations[state.lang];
  el.selectedDateLabel.textContent = state.selectedDate
    ? `${t.selectedDatePrefix} ${state.selectedDate}`
    : t.selectDayPrompt;

  el.selectedSlotLabel.textContent = state.selectedSlot
    ? `${t.selectedSlotPrefix} ${state.selectedDate} · ${state.selectedSlot}`
    : t.selectSlotPrompt;
}

function setupBookingActions() {
  document.querySelectorAll(".action-card").forEach(btn => {
    btn.addEventListener("click", () => {
      const t = translations[state.lang];
      const formData = new FormData(el.bookingForm);
      if (!state.selectedDate) {
        el.feedback.textContent = t.selectDayError;
        return;
      }
      if (!state.selectedSlot) {
        el.feedback.textContent = t.selectSlotError;
        return;
      }
      if (!formData.get("name") || !formData.get("email") || !formData.get("phone")) {
        el.bookingForm.reportValidity();
        return;
      }

      const action = btn.dataset.action;
      if (action === "request") el.feedback.textContent = t.bookingSuccessRequest;
      if (action === "pay") el.feedback.textContent = t.bookingSuccessPay;
      if (action === "onsite") el.feedback.textContent = t.bookingSuccessOnsite;
    });
  });
}

function initMap() {
  const mapEl = document.getElementById("map");
  const helpEl = document.querySelector(".map-help");

  if (!GOOGLE_MAPS_API_KEY || GOOGLE_MAPS_API_KEY.includes("PASTE_YOUR")) {
    renderFallbackMap(mapEl);
    return;
  }

  const script = document.createElement("script");
  script.src = `https://maps.googleapis.com/maps/api/js?key=${GOOGLE_MAPS_API_KEY}&callback=initEscapeMap`;
  script.async = true;
  script.defer = true;
  script.onerror = () => renderFallbackMap(mapEl);
  window.initEscapeMap = () => {
    const center = {
      lat: (mapConfig.escape.lat + mapConfig.parking.lat) / 2,
      lng: (mapConfig.escape.lng + mapConfig.parking.lng) / 2
    };

    const map = new google.maps.Map(mapEl, {
      center,
      zoom: 18,
      mapTypeControl: true,
      streetViewControl: true,
      fullscreenControl: true
    });

    new google.maps.Marker({
      position: mapConfig.escape,
      map,
      title: "Escape Room",
      icon: "http://maps.google.com/mapfiles/ms/icons/red-dot.png"
    });

    new google.maps.Marker({
      position: mapConfig.parking,
      map,
      title: "Parkplatz / Parking",
      icon: "http://maps.google.com/mapfiles/ms/icons/blue-dot.png"
    });

    if (helpEl) helpEl.textContent = "";
  };
  document.head.appendChild(script);
}

function renderFallbackMap(mapEl) {
  mapEl.innerHTML = `
    <div class="map-fallback"></div>
    <div class="fake-pin fake-pin--escape" title="Escape Room"></div>
    <div class="fake-pin fake-pin--parking" title="Parkplatz"></div>
  `;
}

function formatDateKey(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function stripTime(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}
