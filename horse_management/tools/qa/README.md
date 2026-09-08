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
