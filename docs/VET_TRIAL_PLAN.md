# Vet trial plan: Tim's horse list and treatment screen

**Goal:** Tim finishes a yard visit with every note typed, every bill correct, and every record updated before leaving the yard. Nobody types a note or a bill twice.
**Scope:** Tim, our own yard, our own horses. 8 to 12 weeks of use after the build.
**Status:** Plan. Not built. Starts after the farrier test (`docs/FARRIER_PAGE_PLAN.md`) passes.
**Build estimate:** about 4 to 6 weeks, because it reuses the farrier page foundations.

This is the text for section 7 of the Google Doc "Vet Visit App Product Spec". It uses the same plain style, so Tim can add to it.

---

## 7. Vet trial phase

After the farrier test, Tim uses a phone or iPad screen on our own horses. It covers the whole visit: the horse list, the treatment, spoken notes that the app types, the summary, and the invoice.

### 7.1 Tim's problems, and what the trial does about each one

These come from section 1 of this doc.

| Tim's problem today | What the trial does | Tim's notes |
|---|---|---|
| Old software, and the better options cost too much (for example Eclipse) | A simple screen for yard visits only. It does not replace the practice system. | |
| Typing up notes by hand | Tim speaks the note. The app types it. Tim checks it and taps Save. | |
| No phone signal at the yard | The screen works with no signal. It sends everything when the signal comes back. | |
| No mobile app | Built for phone and iPad first. | |
| 16 years of data in one system | We do not move the old data. The old system stays the full record. The trial sends new visits to it as a file, or to Xero. | |

### 7.2 What Tim does: the screens

**A. Open the yard.** Scan the QR code on the yard's door card, or pick the yard from a list. The horse list loads onto the phone before the visit, so it works with no signal.

**B. Horse list.** Each horse shows name, photo, microchip, passport number, owner, who pays, and what is due (vaccination, follow-up, dental). Due horses are at the top.

**C. Treatment screen (one horse).**
- Tap items from Tim's price list, for example "Flu/Tet booster £65" or "Sedation". Change the quantity or price, or add a free-text item.
- Medicines: name, batch number, dose. The app shows the withdrawal period and the passport food-chain status.
- Photo: an optional photo of the leg, the wound or the passport page.
- **Voice note:** hold the microphone button and speak. Example: "Mild heat left fore, no lameness at trot, recheck in two weeks." The app types the note, suggests a follow-up date (in this example, 2 weeks), and Tim checks the text before saving it.

**D. Several horses at once.** Select 10 horses and add "Flu/Tet booster" to all of them in one step, for example on a vaccination day.

**E. End-of-visit summary.** One screen shows every horse, every item, every payer and the split between co-owners. Tim checks it and taps **Finish visit**.

**F. Invoices.** After Finish:
- **Trial step 1:** the app makes one invoice per payer **in the practice's name**, with VAT. It sends the invoices to the practice's accounts (Xero or a file for the practice system). Owners pay the practice as they do now.
- **Trial step 2:** the invoice email has a **pay-now link** (card or bank) to the practice's own Stripe account. The money goes to the practice, never to us.

**G. Records go back to the yard.** Vaccination dates, next due dates and follow-up dates go into Yardway, so the yard reminders stay correct. **The yard does not see clinical notes.** It sees only what it needs, for example "Recheck due 24 Nov".

### 7.3 Voice notes: how they work

1. Tim holds the button and speaks. The phone records the audio.
2. With a signal, a speech-to-text service types the note in a few seconds. With no signal, the phone keeps the audio and types the note when the signal comes back.
3. The app tidies the text into short parts: what Tim found, what was done, and when to check again. It suggests a follow-up date when the note gives one.
4. **Tim always checks and edits the text before saving it.** Nothing goes on a record or a bill until Tim has checked it.
5. A word list of equine and drug terms improves accuracy. Tim can add words.
6. The audio is deleted when the note is saved. Only the text stays.

Rules for voice notes:
- The speech-to-text service must have a UK GDPR data agreement with us, and must not keep or learn from the audio.
- Clinical notes follow RCVS rules on clinical records. Ask a vet adviser to check this before the trial.

### 7.4 How it connects to Yardway and the farrier page

| Part | Already there (farrier test or Yardway) | New for the vet trial |
|---|---|---|
| Login, QR code, price list per professional | Farrier page | Vet role, vet price list with VAT |
| Visit record that groups one day's work | Farrier page (`ProviderVisit`) | Same, plus no-signal storage on the phone |
| Horses, owners, shares, who pays | Yardway | Microchip, passport and due items on the list |
| Split between co-owners | Yardway | Same |
| Vet visit and vaccination records | Yardway (`VetVisit`, `Vaccination`) | Clinical notes hidden from the yard |
| Voice notes | No | New |
| Medicines record | No | New |
| Invoices in the professional's name | No (farrier bills go through the yard) | New: practice invoices with VAT, then pay-now links |

### 7.5 Build steps

| # | Step | Weeks |
|---|---|---|
| 1 | Vet role and price list with VAT, using the farrier page foundations. | 0.5 |
| 2 | Horse list and treatment screen, with photos and several horses at once. | 1 |
| 3 | No-signal mode: load the yard before the visit, store work on the phone, send it later, never send twice. | 1 |
| 4 | Voice notes: record, type, tidy, Tim's check, delete audio. | 1 |
| 5 | Summary, invoices in the practice's name, export to Xero or a file, records back to Yardway. | 1 |
| 6 | Test at our yard with Tim, fixes. | 0.5 to 1 |
| Later | Pay-now links (Stripe Connect), medicines record, insurance claim pack. | — |

### 7.6 How we measure the trial

| Measure | Before (week 0) | Target |
|---|---|---|
| Admin time after a 10-horse visit | Time it | Half or less |
| Notes typed by hand | Count | Zero |
| Voice notes that Tim had to change a lot | — | Fewer than 1 in 5 |
| Days from visit to invoice sent | Count | Same day |
| Invoices with a mistake (wrong payer, wrong split) | Count | Zero |
| Visits recorded with no signal that arrived complete | — | All |
| Would Tim pay for it? | — | Yes, and Tim names a price |

### 7.7 Questions for Tim

- [ ] Which practice system holds the 16 years of data? Can it import invoices or visits as a file?
- [ ] Who sends invoices today: Tim, the office, or the yard? Do owners pay the practice or the yard?
- [ ] Is the practice VAT registered? (Most practices are.)
- [ ] Phone or iPad on visits?
- [ ] How long is there no signal: the whole visit, or only in some fields?
- [ ] What must a clinical note always contain for your records?
- [ ] Which medicines do you record on yard visits, and how today?
- [ ] Would owners accept a pay-now link from the practice?
