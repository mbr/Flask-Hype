from functools import wraps


def recordable(once=False):
    """Creates blueprint-recordable functions on Flask apps.

    Allows abstracting away one difference between a blueprint and an actual
    flask app. A function can be made recordable by using the decorator like this::

        >>> @recordable()
        >>> def hello(app, sender):
        ...     print('hello {}. love, {}'.format(app, sender))
        ...

        >>> from flask import Flask, Blueprint
        >>> app = Flask('doctest')
        >>> hello(app, 'the recordable function')  # executes immediately
        hello <Flask 'doctest'>. love, the recordable function

        >>> bp = Blueprint('bp', __name__)
        >>> hello(bp, 'me again, this time from a blueprint')
        >>> app.register_blueprint(bp)  # causes f to execute
        hello <Flask 'doctest'>. love, me again, this time from a blueprint

    :param once: If ``True``, :method:`~flask.Blueprint.record_once` is used
                 instead of :method:`~flask.Blueprint.record`.
    :return: Returns ``None`` if a ``app_or_bp`` was a Blueprint, otherwise
             the return value of ``f``.
    """

    record_func = 'record_once' if once else 'record'

    def decorator(f):
        @wraps(f)
        def _(app_or_bp, *args, **kwargs):
            # we differentiate between apps and blueprints by checking for
            # the presence of the desired record function
            rec = getattr(app_or_bp, record_func, None)

            if rec:
                rec(lambda state: f(state.app, *args, **kwargs))
            else:
                return f(app_or_bp, *args, **kwargs)

            # return values of f are lost
        return _

    return decorator
