STRIPE -> EPSON RT V2

Aenderungen gegenueber V1
-------------------------
- Reparto 11 statt Reparto 1.
- Reparto 11 wird vor Nutzung automatisch gelesen/geprueft.
- Bei einem Epson-Fehler wird ein eventuell offener JavaPOS/Documento-Commerciale-Zustand
  automatisch per resetPrinter beendet, damit die Epson nicht wieder bei
  "Attesa chiusura scontrino modalita JAVAPOS" haengen bleibt.
- Betrag, Stripe-Zahlungsdatum und Stripe-Zahlungszeit bleiben frei waehlbar.
- Es wird NICHT authorizeSales verwendet: keine neue Nexi-Kartenzahlung.

WICHTIG
-------
Das ausgewaehlte Stripe-Datum und die Uhrzeit werden als Zusatzinformation auf dem
Beleg dokumentiert. Der fiskalische Ausstellungszeitpunkt des Documento Commerciale
bleibt der echte Zeitpunkt, an dem die Epson den Beleg erstellt.

Vor dem ersten produktiven Einsatz:
1. Mit NEXI_0AC0 WLAN verbinden.
2. Tool starten.
3. "Verbindung testen".
4. Nur einen tatsaechlich vorhandenen Stripe-Umsatz buchen.
5. Gedruckten Beleg kontrollieren.
