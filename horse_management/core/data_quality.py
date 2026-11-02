"""Find rows that break the database's check constraints.

docs/LAUNCH_READINESS_PLAN.md, P1 work package 9.

The forms already refuse a negative payment or an end date before the
start date. The database now refuses them too (``CheckConstraint`` on the
models), so a script, an import or a bug cannot store one either.

Adding a constraint fails if an existing row breaks it, with an error that
does not say which row. So each migration that adds constraints first calls
``refuse_if_violated``, which names the rows. ``python manage.py
check_data_quality`` gives the same report at any time.
"""

from django.db import models


def violations(model, condition, limit=10):
    """(count, first few primary keys) of rows where ``condition`` is false.

    Rows where the condition is unknown (a NULL column) pass, as they do
    in the database.
    """
    rows = model._base_manager.exclude(condition)
    return rows.count(), list(rows.order_by('pk').values_list('pk', flat=True)[:limit])


def report(rules):
    """Lines describing each broken rule. ``rules`` is (model, name, condition)."""
    lines = []
    for model, name, condition in rules:
        count, pks = violations(model, condition)
        if count:
            more = ', ...' if count > len(pks) else ''
            lines.append(
                f'{model._meta.label} ({name}): {count} row(s), ids {", ".join(map(str, pks))}{more}'
            )
    return lines


def live_rules():
    """Every CheckConstraint on every installed model."""
    from django.apps import apps

    return [
        (model, constraint.name, constraint.condition)
        for model in apps.get_models()
        for constraint in model._meta.constraints
        if isinstance(constraint, models.CheckConstraint)
    ]


def refuse_if_violated(apps, rules):
    """Migration step: stop with a readable list before a constraint is added.

    ``rules`` is (app_label, model_name, constraint name, condition).
    """
    problems = report([(apps.get_model(app, model), name, cond) for app, model, name, cond in rules])
    if problems:
        raise RuntimeError(
            'Some existing rows break the new data rules, so the database was '
            'not changed. Correct these rows (or delete them) and deploy again:\n  '
            + '\n  '.join(problems)
        )
