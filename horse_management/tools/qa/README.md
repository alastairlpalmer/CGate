# One-off QA scripts

Throwaway scripts written to check a specific change by driving the app or
the ORM directly. They are **not** part of the application and nothing
imports them.

They live here rather than beside the Django apps for two reasons:

* They read as production code when they sit in the package root. A
  reviewer has to open each one to find out it is scratch work.
* Several write to hard-coded paths under `/tmp` and take screenshots.
  Keeping them out of the package makes it obvious they never run on a
  server.

The real test suite is `python manage.py test`. Prefer adding a test there
over adding a script here.

The QA reports under `docs/` cite several of these by bare filename (for
example "`verify_final.py` TEST H"). Those reports are a record of what was
checked at the time; the scripts they name are the ones in this directory.

## The farrier phone check

`farrier_seed.py` and `farrier_phone_qa.js` are kept on purpose: they are
step 7 of `docs/FARRIER_PAGE_PLAN.md` and can be run again after a change
to the farrier page. Run them against a throwaway database:

```bash
export DATABASE_URL=sqlite:////tmp/farrier-qa.sqlite3 DEBUG=True QA_NO_TOOLBAR=1 MFA_REQUIRED_FOR_ADMINS=False AXES_ENABLED=False
python manage.py migrate
python manage.py shell < tools/qa/farrier_seed.py
python manage.py runserver 127.0.0.1:8765
node tools/qa/farrier_phone_qa.js http://127.0.0.1:8765 /tmp/farrier-qa
```

The report lists, per phone and screen, overflow and tap targets under
44 px. Screenshots go in the same folder.

## The C-vet offline check

`cvet_offline_qa.js` is kept on purpose: it is the browser check of the
C-vet app (`docs/C_VET_PLAN.md`, sections 7 to 7c). It uses the
farrier seed above for its horses, and `cvet_seed.py` for the practice
and the vet login. Start the server with the test page on, and store the
test's files somewhere throwaway:

```bash
python manage.py shell < tools/qa/cvet_seed.py
export CVET_ENABLED=True MEDIA_ROOT=/tmp/cvet-qa-media
python manage.py runserver 127.0.0.1:8765
node tools/qa/cvet_offline_qa.js http://127.0.0.1:8765 /tmp/cvet-qa
```

It runs a day of visits with no network: it opens the client, treats one
horse (items from the item list, a changed price, a note, a recheck, a
photo, a voice recording), adds one item to three horses at once, makes a
new client and its horse, reads both visits folded and unfolded, and
finishes them. With the network back, it reads a sent visit: the words
of its note, its recording and its photo.
Then it brings the network back, loses the reply to one upload on
purpose, reads the records back, adds a price and signs out. It prints
what the server must then hold. It is Chromium, not an iPhone: the test
on the real phone is section 8 of the plan.

`cvet_desktop_qa.js` is the same app on a wide screen (section 7h of the
plan). Run it after the offline check, on the same server:

```bash
node tools/qa/cvet_desktop_qa.js http://127.0.0.1:8765 /tmp/cvet-qa
```

The run above is in test mode, as a new server is: a test bill is made and
nothing goes into the yard's records. To check the real path, set
`CVET_TEST_MODE=False` for the server and for the script, on a fresh
throwaway database. The script then prints the yard records to look for:
vaccination records, a vet visit record, charges and a cost.
