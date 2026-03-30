# Escape Room Schenna – V1

Enthalten:
- `index.html`
- `styles.css`
- `app.js`

## Was ist in dieser V1 drin?
- Gate mit Reibefläche, Match-Interaktion, Tür und Skip-Logik
- Umschalter zwischen zwei Gate-Stilen
- 3-sprachige Website (DE / IT / EN)
- Hero, Info, Buchung, Kontakt
- Kalender mit Mock-Verfügbarkeit
- Ausgraute volle Tage
- Slot-Auswahl in 30-Minuten-Schritten
- Formular mit Anfrage / Pay now / Vor Ort bezahlen
- Google Maps API vorbereitet
- Fallback-Karte ohne API-Key

## Wichtige Stellen
### Google Maps API
In `app.js`:
```js
const GOOGLE_MAPS_API_KEY = "PASTE_YOUR_GOOGLE_MAPS_API_KEY_HERE";
```

### Stripe
Stripe ist in dieser V1 noch nicht live verdrahtet.
Empfohlener nächster Schritt:
- kleines Node.js Backend
- Slot reservieren
- Stripe Checkout Session erzeugen
- Webhook für Zahlungsbestätigung
- Buchungsstatus speichern

## Lokaler Start
Einfach `index.html` im Browser öffnen.
Für echte API-Integrationen später besser mit lokalem Server starten, z. B.:
- VS Code Live Server
- `npx serve`
