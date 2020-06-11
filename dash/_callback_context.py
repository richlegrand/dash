import functools
from .dash import g_cc 

from . import exceptions


def has_context(func):
    @functools.wraps(func)
    def assert_context(*args, **kwargs):
        try:
            g_cc.get()
        except:
            raise exceptions.MissingCallbackContextException(
                "dash.callback_context.{} is only available from a callback!".format(
                    getattr(func, "__name__")
                )
            )
        return func(*args, **kwargs)

    return assert_context


class FalsyList(list):
    def __bool__(self):
        # for Python 3
        return False

    def __nonzero__(self):
        # for Python 2
        return False


falsy_triggered = FalsyList([{"prop_id": ".", "value": None}])


# pylint: disable=no-init
class CallbackContext:
    @property
    @has_context
    def inputs(self):
        return getattr(g_cc.get(), "input_values", {})

    @property
    @has_context
    def states(self):
        return getattr(g_cc.get(), "state_values", {})

    @property
    @has_context
    def triggered(self):
        # For backward compatibility: previously `triggered` always had a
        # value - to avoid breaking existing apps, add a dummy item but
        # make the list still look falsy. So `if ctx.triggered` will make it
        # look empty, but you can still do `triggered[0]["prop_id"].split(".")`
        return getattr(g_cc.get(), "triggered_inputs", []) or falsy_triggered

    @property
    @has_context
    def outputs_list(self):
        return getattr(g_cc.get(), "outputs_list", [])

    @property
    @has_context
    def inputs_list(self):
        return getattr(g_cc.get(), "inputs_list", [])

    @property
    @has_context
    def states_list(self):
        g = g_cc.get()
        return getattr(g_cc.get(), "states_list", [])

    @property
    @has_context
    def response(self):
        return getattr(g_cc.get(), "dash_response")

    @property
    @has_context
    def client(self):
        return getattr(g_cc.get(), "client")

callback_context = CallbackContext()
