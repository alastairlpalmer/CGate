/*
  Enquiry forms. Posts to the form's action with fetch and shows the success
  or error state. The states appear with a short transition from display:none
  (CSS @starting-style, docs/plan.md Section 11.4 I).
  Without an action set, the form cannot send, and says so.
*/
function setup() {
  document.querySelectorAll<HTMLFormElement>('[data-form]').forEach((form) => {
    if (form.dataset.bound === 'true') return;
    form.dataset.bound = 'true';

    const ok = form.querySelector<HTMLElement>('[data-form-success]');
    const error = form.querySelector<HTMLElement>('[data-form-error]');
    const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]');

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (ok) ok.hidden = true;
      if (error) error.hidden = true;

      if (form.dataset.formReady !== 'true' || !form.action) {
        if (error) error.hidden = false;
        return;
      }

      if (submit) submit.disabled = true;
      try {
        const response = await fetch(form.action, {
          method: 'POST',
          body: new FormData(form),
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) throw new Error(String(response.status));
        form.reset();
        if (ok) ok.hidden = false;
      } catch {
        if (error) error.hidden = false;
      } finally {
        if (submit) submit.disabled = false;
      }
    });
  });
}

document.addEventListener('astro:page-load', setup);
