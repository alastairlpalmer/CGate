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
C-vet offline prototype (`docs/C_VET_PLAN.md`, section 7). It uses the
farrier seed above for its horses and its login. Start the server with
the test page on, and allow the microphone test to store files somewhere
throwaway:

```bash
export CVET_PROTOTYPE_ENABLED=True MEDIA_ROOT=/tmp/cvet-qa-media
python manage.py runserver 127.0.0.1:8765
node tools/qa/cvet_offline_qa.js http://127.0.0.1:8765 /tmp/cvet-qa
```

It downloads the horse list, cuts the network, opens the page again,
saves three notes (one with a photo and a voice recording), brings the
network back and loses the reply to one upload on purpose. The server
must end with exactly three entries. It is Chromium, not an iPhone: the
test on the real phone is section 8 of the plan.
