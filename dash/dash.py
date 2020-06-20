from __future__ import print_function

import itertools
import os
import random
import sys
import collections
import importlib
import json
import pkgutil
import threading
import re
import logging
import mimetypes

from functools import wraps

import quart
from quart_compress import Compress
import asyncio
import inspect
from werkzeug.debug.tbtools import get_current_traceback
from contextvars import ContextVar
from copy import deepcopy

import plotly
import dash_renderer

from .fingerprint import build_fingerprint, check_fingerprint
from dash.resources import Scripts, Css
from .development.base_component import ComponentRegistry
from .exceptions import PreventUpdate, InvalidResourceError
from .version import __version__
from ._configs import get_combined_config, pathname_configs
from ._utils import (
    AttributeDict,
    create_callback_id,
    format_tag,
    generate_hash,
    get_asset_path,
    get_relative_path,
    inputs_to_dict,
    inputs_to_vals,
    interpolate_str,
    patch_collections_abc,
    split_callback_id,
    stringify_id,
    strip_relative_path,
    runcoro,
    list_to_mods,
    mods_to_list,
    flatten_layout,
    intersect_ids, 
    find_prop_value,
)
from .dependencies import Output
from .pusher import Pusher, Alock
from . import _validate
from . import _watch

# Add explicit mapping for map files
mimetypes.add_type("application/json", ".map", True)

_default_index = """<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>"""

_app_entry = """
<div id="react-entry-point">
    <div class="_dash-loading">
        Loading...
    </div>
</div>
"""

_re_index_entry = "{%app_entry%}", "{%app_entry%}"
_re_index_config = "{%config%}", "{%config%}"
_re_index_scripts = "{%scripts%}", "{%scripts%}"

_re_index_entry_id = 'id="react-entry-point"', "#react-entry-point"
_re_index_config_id = 'id="_dash-config"', "#_dash-config"
_re_index_scripts_id = 'src="[^"]*dash[-_]renderer[^"]*"', "dash-renderer"
_re_renderer_scripts_id = 'id="_dash-renderer', "new DashRenderer"


class _NoUpdate(object):
    # pylint: disable=too-few-public-methods
    pass


# Singleton signal to not update an output, alternative to PreventUpdate
no_update = _NoUpdate()


class _Context(object):
    # pylint: disable=too-few-public-methods
    pass

#
g_cc = ContextVar("calling_context")


class Services(object):
    # Service bits:
    # Update component over socket, opposite = http update
    PUSHER_UPDATE = 1<<0 
    # Dependencies, layout and reload_hash requests over socket, opposite = http requests
    PUSHER_OTHER = 1<<1  
    # not supported for server_service, opposite = initial callback from each client    
    NO_CLIENT_INITIAL_CALLBACK = 1<<2 
    # not supported for server_service, opposite = no initial callback on server side
    SERVER_INITIAL_CALLBACK = 1<<3
    # not supported for server_service, opposite = only requesting client gets update
    SHARED_CALLBACK = 1<<4
    # not supported for server_service, opposite = concurrent callbacks
    SERIALIZED_CALLBACK = 1<<5
    # not supported for server service, opposite = do not inform other clients of shared changes
    SHARE_WITH_OTHER_CLIENTS = 1<<6

    # Callback services:
    # S0: normal dash service
    S0 = 0 

    # SHARED aka S1: all clients see the same thing, e.g. one device.
    SHARED = PUSHER_UPDATE + NO_CLIENT_INITIAL_CALLBACK + SHARED_CALLBACK + \
        SERVER_INITIAL_CALLBACK + SERIALIZED_CALLBACK + SHARE_WITH_OTHER_CLIENTS

    # S2: clients see different things, e.g. N devices.
    S2 = PUSHER_UPDATE + SERIALIZED_CALLBACK

    # Server services:
    PUSHER_ALL = PUSHER_UPDATE + PUSHER_OTHER  # Everything sent over socket except assets
    NORMAL = 0

    @staticmethod
    def shared_test(service):
        if service is None:
            return False
        return service&Services.SHARED_CALLBACK


_inline_clientside_template = """
var clientside = window.dash_clientside = window.dash_clientside || {{}};
var ns = clientside["{namespace}"] = clientside["{namespace}"] || {{}};
ns["{function_name}"] = {clientside_function};
"""


# pylint: disable=too-many-instance-attributes
# pylint: disable=too-many-arguments, too-many-locals
class Dash(object):
    """Dash is a framework for building analytical web applications.
    No JavaScript required.

    If a parameter can be set by an environment variable, that is listed as:
        env: ``DASH_****``
    Values provided here take precedence over environment variables.

    :param name: The name Flask should use for your app. Even if you provide
        your own ``server``, ``name`` will be used to help find assets.
        Typically ``__name__`` (the magic global var, not a string) is the
        best value to use. Default ``'__main__'``, env: ``DASH_APP_NAME``
    :type name: string

    :param server: Sets the Flask server for your app. There are three options:
        ``True`` (default): Dash will create a new server
        ``False``: The server will be added later via ``app.init_app(server)``
            where ``server`` is a ``flask.Flask`` instance.
        ``flask.Flask``: use this pre-existing Flask server.
    :type server: boolean or flask.Flask

    :param assets_folder: a path, relative to the current working directory,
        for extra files to be used in the browser. Default ``'assets'``.
        All .js and .css files will be loaded immediately unless excluded by
        ``assets_ignore``, and other files such as images will be served if
        requested.
    :type assets_folder: string

    :param assets_url_path: The local urls for assets will be:
        ``requests_pathname_prefix + assets_url_path + '/' + asset_path``
        where ``asset_path`` is the path to a file inside ``assets_folder``.
        Default ``'assets'``.
    :type asset_url_path: string

    :param assets_ignore: A regex, as a string to pass to ``re.compile``, for
        assets to omit from immediate loading. Ignored files will still be
        served if specifically requested. You cannot use this to prevent access
        to sensitive files.
    :type assets_ignore: string

    :param assets_external_path: an absolute URL from which to load assets.
        Use with ``serve_locally=False``. Dash can still find js and css to
        automatically load if you also keep local copies in your assets
        folder that Dash can index, but external serving can improve
        performance and reduce load on the Dash server.
        env: ``DASH_ASSETS_EXTERNAL_PATH``
    :type assets_external_path: string

    :param include_assets_files: Default ``True``, set to ``False`` to prevent
        immediate loading of any assets. Assets will still be served if
        specifically requested. You cannot use this to prevent access
        to sensitive files. env: ``DASH_INCLUDE_ASSETS_FILES``
    :type include_assets_files: boolean

    :param url_base_pathname: A local URL prefix to use app-wide.
        Default ``'/'``. Both `requests_pathname_prefix` and
        `routes_pathname_prefix` default to `url_base_pathname`.
        env: ``DASH_URL_BASE_PATHNAME``
    :type url_base_pathname: string

    :param requests_pathname_prefix: A local URL prefix for file requests.
        Defaults to `url_base_pathname`, and must end with
        `routes_pathname_prefix`. env: ``DASH_REQUESTS_PATHNAME_PREFIX``
    :type requests_pathname_prefix: string

    :param routes_pathname_prefix: A local URL prefix for JSON requests.
        Defaults to ``url_base_pathname``, and must start and end
        with ``'/'``. env: ``DASH_ROUTES_PATHNAME_PREFIX``
    :type routes_pathname_prefix: string

    :param serve_locally: If ``True`` (default), assets and dependencies
        (Dash and Component js and css) will be served from local URLs.
        If ``False`` we will use CDN links where available.
    :type serve_locally: boolean

    :param compress: Use gzip to compress files and data served by Flask.
        Default ``True``
    :type compress: boolean

    :param meta_tags: html <meta> tags to be added to the index page.
        Each dict should have the attributes and values for one tag, eg:
        ``{'name': 'description', 'content': 'My App'}``
    :type meta_tags: list of dicts

    :param index_string: Override the standard Dash index page.
        Must contain the correct insertion markers to interpolate various
        content into it depending on the app config and components used.
        See https://dash.plotly.com/external-resources for details.
    :type index_string: string

    :param external_scripts: Additional JS files to load with the page.
        Each entry can be a string (the URL) or a dict with ``src`` (the URL)
        and optionally other ``<script>`` tag attributes such as ``integrity``
        and ``crossorigin``.
    :type external_scripts: list of strings or dicts

    :param external_stylesheets: Additional CSS files to load with the page.
        Each entry can be a string (the URL) or a dict with ``href`` (the URL)
        and optionally other ``<link>`` tag attributes such as ``rel``,
        ``integrity`` and ``crossorigin``.
    :type external_stylesheets: list of strings or dicts

    :param suppress_callback_exceptions: Default ``False``: check callbacks to
        ensure referenced IDs exist and props are valid. Set to ``True``
        if your layout is dynamic, to bypass these checks.
        env: ``DASH_SUPPRESS_CALLBACK_EXCEPTIONS``
    :type suppress_callback_exceptions: boolean

    :param show_undo_redo: Default ``False``, set to ``True`` to enable undo
        and redo buttons for stepping through the history of the app state.
    :type show_undo_redo: boolean

    :param plugins: Extend Dash functionality by passing a list of objects
        with a ``plug`` method, taking a single argument: this app, which will
        be called after the Flask server is attached.
    :type plugins: list of objects
    """

    def __init__(
        self,
        name=None,
        server=True,
        assets_folder="assets",
        assets_url_path="assets",
        assets_ignore="",
        assets_external_path=None,
        eager_loading=False,
        include_assets_files=True,
        url_base_pathname=None,
        requests_pathname_prefix=None,
        routes_pathname_prefix=None,
        serve_locally=True,
        compress=None,
        meta_tags=None,
        index_string=_default_index,
        external_scripts=None,
        external_stylesheets=None,
        suppress_callback_exceptions=None,
        show_undo_redo=False,
        plugins=None,
        server_service=Services.PUSHER_ALL, 
        callback_service=Services.S0,
        **obsolete
    ):
        _validate.check_obsolete(obsolete)

        # We have 3 cases: server is either True (we create the server), False
        # (defer server creation) or a Flask app instance (we use their server)
        if isinstance(server, quart.Quart):
            self.server = server
            if name is None:
                name = getattr(server, "name", "__main__")
        elif isinstance(server, bool):
            name = name if name else "__main__"
            self.server = quart.Quart(name) if server else None
        else:
            raise ValueError("server must be a Quart app or a boolean")

        base_prefix, routes_prefix, requests_prefix = pathname_configs(
            url_base_pathname, routes_pathname_prefix, requests_pathname_prefix
        )
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "__file__"):
            root_path = os.path.dirname(os.path.abspath(mod.__file__))
        else: 
            root_path = os.getcwd() 
        self.config = AttributeDict(
            name=name,
            assets_folder=os.path.join(
                root_path, assets_folder
            ),
            assets_url_path=assets_url_path,
            assets_ignore=assets_ignore,
            assets_external_path=get_combined_config(
                "assets_external_path", assets_external_path, ""
            ),
            eager_loading=eager_loading,
            include_assets_files=get_combined_config(
                "include_assets_files", include_assets_files, True
            ),
            url_base_pathname=base_prefix,
            routes_pathname_prefix=routes_prefix,
            requests_pathname_prefix=requests_prefix,
            serve_locally=serve_locally,
            compress=get_combined_config("compress", compress, True),
            meta_tags=meta_tags or [],
            external_scripts=external_scripts or [],
            external_stylesheets=external_stylesheets or [],
            suppress_callback_exceptions=get_combined_config(
                "suppress_callback_exceptions", suppress_callback_exceptions, False
            ),
            show_undo_redo=show_undo_redo,
            server_service=server_service, 
            callback_service=callback_service,
        )
        self.config.set_read_only(
            [
                "name",
                "assets_folder",
                "assets_url_path",
                "eager_loading",
                "url_base_pathname",
                "routes_pathname_prefix",
                "requests_pathname_prefix",
                "serve_locally",
                "compress",
            ],
            "Read-only: can only be set in the Dash constructor",
        )
        self.config.finalize(
            "Invalid config key. Some settings are only available "
            "via the Dash constructor"
        )

        # list of dependencies - this one is used by the back end for dispatching
        self.callback_map = {}
        # same deps as a list to catch duplicate outputs, and to send to the front end
        self._callback_list = []
        self.shared_callbacks_called = False
        # Table of components, indexed by id
        self.layout_components = {}
        self.handle_layout_lock = Alock()
        self.none_output_count = 0

        # list of inline scripts
        self._inline_scripts = []

        # index_string has special setter so can't go in config
        self._index_string = ""
        self.index_string = index_string
        self._favicon = None

        # default renderer string
        self.renderer = "var renderer = new DashRenderer();"

        # static files from the packages
        self.css = Css(serve_locally)
        self.scripts = Scripts(serve_locally, eager_loading)

        self.registered_paths = collections.defaultdict(set)

        # urls
        self.routes = []

        self._layout = None
        self._layout_is_function = False
        self.validation_layout = None

        self._setup_dev_tools()
        self._hot_reload = AttributeDict(
            hash=None,
            hard=False,
            lock=threading.RLock(),
            watch_thread=None,
            changed_assets=[],
        )

        self._assets_files = []

        self.logger = logging.getLogger(name)
        self.logger.addHandler(logging.StreamHandler(stream=sys.stdout))

        if isinstance(plugins, patch_collections_abc("Iterable")):
            for plugin in plugins:
                plugin.plug(self)

        if self.server is not None:
            self.init_app()

    
    async def handle_layout(self, id_, prop, val):
        async with self.handle_layout_lock:
            if id_ is None or prop=="children":
                comps = flatten_layout(val)
                for comp in comps:
                    try:
                        self.layout_components[comp.id]
                    except KeyError:
                        self.layout_components[comp.id] = comp
                await self._initial_callbacks(comps)

            if id_ and prop:
                try: # Handle races with push_mods.
                    comp = self.layout_components[id_]
                    setattr(comp, prop, val)
                except KeyError:
                    pass


    async def mod_layout(self, mods):
        if isinstance(mods, list):
            mods = list_to_mods(mods)
        for id_, vals in mods.items():
            for prop, val in vals.items():
                await self.handle_layout(id_, prop, val)


    async def share_shared_mods(self, mods, x_client=None):
        result = await self.pusher.send("mod", mods, x_client=x_client)
        await self.mod_layout(mods)
        return result


    async def push_mods_coro(self, mods, client=None):
        if isinstance(mods, list):
            props = mods
            mods = list_to_mods(props)
        else:
            if isinstance(mods, Output):
                mods = {mods.component_id: {mods.component_property: mods.component_value}}
            props = mods_to_list(mods)

        callback_ids, x_list = self._callback_intersect(props, Services.shared_test)
        if callback_ids and client is None:
            if x_list:
                raise Exception("Cannot send push_mods() on components with a mix of shared and non-shared callbacks.")
            # We need to send input mods first before calling _dispatch_chain
            result = await self.share_shared_mods(mods)
            await self._dispatch_chain(props)
        else:
            # If we modify all clients, we should modify layout.
            if client is None: 
                await self.mod_layout(mods)
            # If component isn't shared we modify with notification.  
            # If component doesn't have any callbacks we also modify with notification
            # (and subsequently don't get called back, which is correct behavior.)
            result = await self.pusher.send("mod_n", mods, client) 
        return result

    # Note, client==None means all clients.
    def push_mods(self, mods, client=None):
        if self.pusher.loop is None:
            raise Exception("Cannot call push_mods before run_server() is called.")
        fut = asyncio.run_coroutine_threadsafe(self.push_mods_coro(mods, client), self.pusher.loop)
        return fut.result()


    def init_app(self, app=None):
        """Initialize the parts of Dash that require a flask app."""
        config = self.config

        if app is not None:
            self.server = app

        self.pusher = Pusher(self.server)

        assets_blueprint_name = "{}{}".format(
            config.routes_pathname_prefix.replace("/", "_"), "dash_assets"
        )

        self.server.register_blueprint(
            quart.Blueprint(
                assets_blueprint_name,
                config.name,
                static_folder=self.config.assets_folder,
                static_url_path="{}{}".format(
                    config.routes_pathname_prefix,
                    self.config.assets_url_path.lstrip("/"),
                ),
            )
        )

        if config.compress:
            # gzip
            Compress(self.server)

        @self.server.errorhandler(PreventUpdate)
        def _handle_error(_):
            """Handle a halted callback and return an empty 204 response."""
            return "", 204

        self.server.before_first_request(self._setup_server)

        # add a handler for components suites errors to return 404
        self.server.errorhandler(InvalidResourceError)(self._invalid_resources_handler)

        pusher_other = self.config.server_service&Services.PUSHER_OTHER
        self._add_url(
            "_dash-component-suites/<string:package_name>/<path:fingerprinted_path>",
            self.serve_component_suites,
        )
        self._add_url("_dash-layout", self.serve_layout, pusher_callback=pusher_other)
        self._add_url("_dash-dependencies", self.dependencies, pusher_callback=pusher_other)
        self._add_url("_dash-update-component", self.dispatch, ["POST"], True)
        self._add_url("_reload-hash", self.serve_reload_hash, pusher_callback=pusher_other) 
        self._add_url("_favicon.ico", self._serve_default_favicon)
        self._add_url("", self.index)

        # catch-all for front-end routes, used by dcc.Location
        self._add_url("<path:path>", self.index)

    def _add_url(self, name, view_func, methods=("GET",), pusher_callback=False):
        full_name = self.config.routes_pathname_prefix + name

        self.server.add_url_rule(
            full_name, view_func=view_func, endpoint=full_name, methods=list(methods)
        )

        # record the url in Dash.routes so that it can be accessed later
        # e.g. for adding authentication with flask_login
        self.routes.append(full_name)

        if pusher_callback:
            self.pusher.add_url(name, view_func)

    @property
    def layout(self):
        return self._layout

    def _layout_value(self):
        return self._layout() if self._layout_is_function else self._layout

    @layout.setter
    def layout(self, value):
        _validate.validate_layout_type(value)
        self._layout_is_function = isinstance(value, patch_collections_abc("Callable"))
        self._layout = value

        # for using flask.has_request_context() to deliver a full layout for
        # validation inside a layout function - track if a user might be doing this.
        if (
            self._layout_is_function
            and not self.validation_layout
            and not self.config.suppress_callback_exceptions
        ):

            def simple_clone(c, children=None):
                cls = type(c)
                # in Py3 we can use the __init__ signature to reduce to just
                # required args and id; in Py2 this doesn't work so we just
                # empty out children.
                sig = getattr(cls.__init__, "__signature__", None)
                props = {
                    p: getattr(c, p)
                    for p in c._prop_names  # pylint: disable=protected-access
                    if hasattr(c, p)
                    and (
                        p == "id" or not sig or sig.parameters[p].default == c.REQUIRED
                    )
                }
                if props.get("children", children):
                    props["children"] = children or []
                return cls(**props)

            layout_value = self._layout_value()
            _validate.validate_layout(value, layout_value)
            self.validation_layout = simple_clone(
                # pylint: disable=protected-access
                layout_value,
                [simple_clone(c) for c in layout_value._traverse_ids()],
            )

    @property
    def index_string(self):
        return self._index_string

    @index_string.setter
    def index_string(self, value):
        checks = (_re_index_entry, _re_index_config, _re_index_scripts)
        _validate.validate_index("index string", checks, value)
        self._index_string = value

    async def serve_layout(self, body=None, client=None, request_id=None):
        layout = self._layout_value()
        await self.handle_layout(None, None, layout)

        if request_id is not None:
            await self.pusher.respond(layout, request_id)
        else:
            # TODO - Set browser cache limit - pass hash into frontend
            return quart.Response(
                json.dumps(layout, cls=plotly.utils.PlotlyJSONEncoder),
                mimetype="application/json",
            )

    def _config(self):
        # pieces of config needed by the front end
        config = {
            "url_base_pathname": self.config.url_base_pathname,
            "requests_pathname_prefix": self.config.requests_pathname_prefix,
            "ui": self._dev_tools.ui,
            "props_check": self._dev_tools.props_check,
            "show_undo_redo": self.config.show_undo_redo,
            "suppress_callback_exceptions": self.config.suppress_callback_exceptions,
            "server_service": self.config.server_service,
        }
        if self._dev_tools.hot_reload or self._dev_tools.client_reload:
            config["hot_reload"] = {
                # convert from seconds to msec as used by js `setInterval`
                "interval": int(self._dev_tools.hot_reload_interval * 1000),
                "max_retry": self._dev_tools.hot_reload_max_retry,
            }
        if self.validation_layout and not self.config.suppress_callback_exceptions:
            config["validation_layout"] = self.validation_layout

        return config

    async def serve_reload_hash(self, body=None, client=None, request_id=None):
        _reload = self._hot_reload
        with _reload.lock:
            hard = _reload.hard
            changed = _reload.changed_assets
            _hash = _reload.hash
            _reload.hard = False
            _reload.changed_assets = []

        res = {
            "reloadHash": _hash,
            "hard": hard,
            "packages": list(self.registered_paths.keys()),
            "files": list(changed),
        }
        if request_id is not None:
            await self.pusher.respond(res, request_id)
        else:
            return quart.jsonify(res)

    def _collect_and_register_resources(self, resources):
        # now needs the app context.
        # template in the necessary component suite JS bundles
        # add the version number of the package as a query parameter
        # for cache busting
        def _relative_url_path(relative_package_path="", namespace=""):

            module_path = os.path.join(
                os.path.dirname(sys.modules[namespace].__file__), relative_package_path
            )

            modified = int(os.stat(module_path).st_mtime)
            return "{}_dash-component-suites/{}/{}".format(
                self.config.requests_pathname_prefix,
                namespace,
                build_fingerprint(
                    relative_package_path,
                    importlib.import_module(namespace).__version__,
                    modified,
                ),
            )

        srcs = []
        for resource in resources:
            is_dynamic_resource = resource.get("dynamic", False)

            if "relative_package_path" in resource:
                paths = resource["relative_package_path"]
                paths = [paths] if isinstance(paths, str) else paths

                for rel_path in paths:
                    self.registered_paths[resource["namespace"]].add(rel_path)

                    if not is_dynamic_resource:
                        srcs.append(
                            _relative_url_path(
                                relative_package_path=rel_path,
                                namespace=resource["namespace"],
                            )
                        )
            elif "external_url" in resource:
                if not is_dynamic_resource:
                    if isinstance(resource["external_url"], str):
                        srcs.append(resource["external_url"])
                    else:
                        srcs += resource["external_url"]
            elif "absolute_path" in resource:
                raise Exception("Serving files from absolute_path isn't supported yet")
            elif "asset_path" in resource:
                static_url = self.get_asset_url(resource["asset_path"])
                # Add a cache-busting query param
                static_url += "?m={}".format(resource["ts"])
                srcs.append(static_url)
        return srcs

    def _generate_css_dist_html(self):
        external_links = self.config.external_stylesheets
        links = self._collect_and_register_resources(self.css.get_all_css())

        return "\n".join(
            [
                format_tag("link", link, opened=True)
                if isinstance(link, dict)
                else '<link rel="stylesheet" href="{}">'.format(link)
                for link in (external_links + links)
            ]
        )

    def _generate_scripts_html(self):
        # Dash renderer has dependencies like React which need to be rendered
        # before every other script. However, the dash renderer bundle
        # itself needs to be rendered after all of the component's
        # scripts have rendered.
        # The rest of the scripts can just be loaded after React but before
        # dash renderer.
        # pylint: disable=protected-access

        mode = "dev" if self._dev_tools["props_check"] is True else "prod"

        deps = []
        for js_dist_dependency in dash_renderer._js_dist_dependencies:
            dep = {}
            for key, value in js_dist_dependency.items():
                dep[key] = value[mode] if isinstance(value, dict) else value

            deps.append(dep)

        dev = self._dev_tools.serve_dev_bundles
        srcs = (
            self._collect_and_register_resources(
                self.scripts._resources._filter_resources(deps, dev_bundles=dev)
            )
            + self.config.external_scripts
            + self._collect_and_register_resources(
                self.scripts.get_all_scripts(dev_bundles=dev)
                + self.scripts._resources._filter_resources(
                    dash_renderer._js_dist, dev_bundles=dev
                )
            )
        )

        return "\n".join(
            [
                format_tag("script", src)
                if isinstance(src, dict)
                else '<script src="{}"></script>'.format(src)
                for src in srcs
            ]
            + ["<script>{}</script>".format(src) for src in self._inline_scripts]
        )

    def _generate_config_html(self):
        return '<script id="_dash-config" type="application/json">{}</script>'.format(
            json.dumps(self._config(), cls=plotly.utils.PlotlyJSONEncoder)
        )

    def _generate_renderer(self):
        return (
            '<script id="_dash-renderer" type="application/javascript">'
            "{}"
            "</script>"
        ).format(self.renderer)

    def _generate_meta_html(self):
        meta_tags = self.config.meta_tags
        has_ie_compat = any(
            x.get("http-equiv", "") == "X-UA-Compatible" for x in meta_tags
        )
        has_charset = any("charset" in x for x in meta_tags)

        tags = []
        if not has_ie_compat:
            tags.append('<meta http-equiv="X-UA-Compatible" content="IE=edge">')
        if not has_charset:
            tags.append('<meta charset="UTF-8">')

        tags += [format_tag("meta", x, opened=True) for x in meta_tags]

        return "\n      ".join(tags)

    # Serve the JS bundles for each package
    async def serve_component_suites(self, package_name, fingerprinted_path):
        path_in_pkg, has_fingerprint = check_fingerprint(fingerprinted_path)

        _validate.validate_js_path(self.registered_paths, package_name, path_in_pkg)

        extension = "." + path_in_pkg.split(".")[-1]
        mimetype = mimetypes.types_map.get(extension, "application/octet-stream")

        package = sys.modules[package_name]
        self.logger.debug(
            "serving -- package: %s[%s] resource: %s => location: %s",
            package_name,
            package.__version__,
            path_in_pkg,
            package.__path__,
        )

        # For development: check local directory for resource first.
        try:
            path = os.path.join(os.path.dirname(__file__), path_in_pkg)
            data = open(path, 'rb').read()    
        except FileNotFoundError:
            data = pkgutil.get_data(package_name, path_in_pkg)
        response = quart.Response(data, mimetype=mimetype)

        if has_fingerprint:
            # Fingerprinted resources are good forever (1 year)
            # No need for ETag as the fingerprint changes with each build
            response.cache_control.max_age = 31536000  # 1 year
        else:
            # Non-fingerprinted resources are given an ETag that
            # will be used / check on future requests
            await response.add_etag()
            tag = response.get_etag()[0]

            request_etag = quart.request.headers.get("If-None-Match")

            if '"{}"'.format(tag) == request_etag:
                response = quart.Response(None, status=304)

        return response

    async def index(self, *args, **kwargs):  # pylint: disable=unused-argument
        scripts = self._generate_scripts_html()
        css = self._generate_css_dist_html()
        config = self._generate_config_html()
        metas = self._generate_meta_html()
        renderer = self._generate_renderer()
        title = getattr(self, "title", "Dash")

        if self._favicon:
            favicon_mod_time = os.path.getmtime(
                os.path.join(self.config.assets_folder, self._favicon)
            )
            favicon_url = self.get_asset_url(self._favicon) + "?m={}".format(
                favicon_mod_time
            )
        else:
            favicon_url = "{}_favicon.ico?v={}".format(
                self.config.requests_pathname_prefix, __version__
            )

        favicon = format_tag(
            "link",
            {"rel": "icon", "type": "image/x-icon", "href": favicon_url},
            opened=True,
        )

        index = self.interpolate_index(
            metas=metas,
            title=title,
            css=css,
            config=config,
            scripts=scripts,
            app_entry=_app_entry,
            favicon=favicon,
            renderer=renderer,
        )

        checks = (
            _re_index_entry_id,
            _re_index_config_id,
            _re_index_scripts_id,
            _re_renderer_scripts_id,
        )
        _validate.validate_index("index", checks, index)
        return index

    def interpolate_index(
        self,
        metas="",
        title="",
        css="",
        config="",
        scripts="",
        app_entry="",
        favicon="",
        renderer="",
    ):
        """Called to create the initial HTML string that is loaded on page.
        Override this method to provide you own custom HTML.

        :Example:

            class MyDash(dash.Dash):
                def interpolate_index(self, **kwargs):
                    return '''<!DOCTYPE html>
                    <html>
                        <head>
                            <title>My App</title>
                        </head>
                        <body>
                            <div id="custom-header">My custom header</div>
                            {app_entry}
                            {config}
                            {scripts}
                            {renderer}
                            <div id="custom-footer">My custom footer</div>
                        </body>
                    </html>'''.format(app_entry=kwargs.get('app_entry'),
                                      config=kwargs.get('config'),
                                      scripts=kwargs.get('scripts'),
                                      renderer=kwargs.get('renderer'))

        :param metas: Collected & formatted meta tags.
        :param title: The title of the app.
        :param css: Collected & formatted css dependencies as <link> tags.
        :param config: Configs needed by dash-renderer.
        :param scripts: Collected & formatted scripts tags.
        :param renderer: A script tag that instantiates the DashRenderer.
        :param app_entry: Where the app will render.
        :param favicon: A favicon <link> tag if found in assets folder.
        :return: The interpolated HTML string for the index.
        """
        return interpolate_str(
            self.index_string,
            metas=metas,
            title=title,
            css=css,
            config=config,
            scripts=scripts,
            favicon=favicon,
            renderer=renderer,
            app_entry=app_entry,
        )

    async def dependencies(self, body=None, client=None, request_id=None):
        if request_id is not None:
            await self.pusher.respond(self._callback_list, request_id)
        else:
            return quart.jsonify(self._callback_list)

    def _insert_callback(self, output, inputs, state, service):
        _validate.validate_callback(output, inputs, state)
        callback_id = create_callback_id(output)
        callback_spec = {
            "output": callback_id,
            "inputs": [c.to_dict() for c in inputs],
            "state": [c.to_dict() for c in state],
            "service": service,
            "clientside_function": None,
        }
        self.callback_map[callback_id] = {
            "inputs": callback_spec["inputs"],
            "state": callback_spec["state"],
            "outputs": [c.to_dict() for c in output] if isinstance(output, list) else [output.to_dict()],
            "service": service,
        }
        self._callback_list.append(callback_spec)

        return callback_id

    def clientside_callback(
        self, clientside_function, output, inputs, state=(), service=Services.S0
    ):
        """Create a callback that updates the output by calling a clientside
        (JavaScript) function instead of a Python function.

        Unlike `@app.callback`, `clientside_callback` is not a decorator:
        it takes either a
        `dash.dependencies.ClientsideFunction(namespace, function_name)`
        argument that describes which JavaScript function to call
        (Dash will look for the JavaScript function at
        `window.dash_clientside[namespace][function_name]`), or it may take
        a string argument that contains the clientside function source.

        For example, when using a `dash.dependencies.ClientsideFunction`:
        ```
        app.clientside_callback(
            ClientsideFunction('my_clientside_library', 'my_function'),
            Output('my-div' 'children'),
            [Input('my-input', 'value'),
             Input('another-input', 'value')]
        )
        ```

        With this signature, Dash's front-end will call
        `window.dash_clientside.my_clientside_library.my_function` with the
        current values of the `value` properties of the components `my-input`
        and `another-input` whenever those values change.

        Include a JavaScript file by including it your `assets/` folder. The
        file can be named anything but you'll need to assign the function's
        namespace to the `window.dash_clientside` namespace. For example,
        this file might look:
        ```
        window.dash_clientside = window.dash_clientside || {};
        window.dash_clientside.my_clientside_library = {
            my_function: function(input_value_1, input_value_2) {
                return (
                    parseFloat(input_value_1, 10) +
                    parseFloat(input_value_2, 10)
                );
            }
        }
        ```

        Alternatively, you can pass the JavaScript source directly to
        `clientside_callback`. In this case, the same example would look like:
        ```
        app.clientside_callback(
            '''
            function(input_value_1, input_value_2) {
                return (
                    parseFloat(input_value_1, 10) +
                    parseFloat(input_value_2, 10)
                );
            }
            ''',
            Output('my-div' 'children'),
            [Input('my-input', 'value'),
             Input('another-input', 'value')]
        )
        ```
        """
        self._insert_callback(output, inputs, state, Services.S0)

        # If JS source is explicitly given, create a namespace and function
        # name, then inject the code.
        if isinstance(clientside_function, str):

            out0 = output
            if isinstance(output, (list, tuple)):
                out0 = output[0]

            namespace = "_dashprivate_{}".format(out0.component_id)
            function_name = "{}".format(out0.component_property)

            self._inline_scripts.append(
                _inline_clientside_template.format(
                    namespace=namespace.replace('"', '\\"'),
                    function_name=function_name.replace('"', '\\"'),
                    clientside_function=clientside_function,
                )
            )

        # Callback is stored in an external asset.
        else:
            namespace = clientside_function.namespace
            function_name = clientside_function.function_name

        self._callback_list[-1]["clientside_function"] = {
            "namespace": namespace,
            "function_name": function_name,
        }

    def callback_s0(self, output, inputs, state=()):
        return self.callback(output, inputs, state, Services.S0)

    def callback_shared(self, output, inputs, state=()):
        return self.callback(output, inputs, state, Services.SHARED)

    def callback_s2(self, output, inputs, state=()):
        return self.callback(output, inputs, state, Services.S2)

    def callback(self, output, inputs, state=(), service=None):
        # if service isn't set, set to default callback service
        if service is None:
            service = self.config.callback_service
        if output is None:
            output = Output("_none", str(self.none_output_count))
            self.none_output_count += 1

        """
        Normally used as a decorator, `@app.callback` provides a server-side
        callback relating the values of one or more `output` items to one or
        more `input` items which will trigger the callback when they change,
        and optionally `state` items which provide additional information but
        do not trigger the callback directly.
        """
        callback_id = self._insert_callback(output, inputs, state, service)
        multi = isinstance(output, (list, tuple))

        def wrap_func(func):
            is_coro = inspect.iscoroutinefunction(func)
            @wraps(func)
            async def add_context(body, response, lock, client):
                g = _Context()  
                g_cc.set(g)          
                g.inputs_list = inputs = body.get("inputs", [])
                g.states_list = state = body.get("state", [])
                output = body["output"]
                outputs_list = body.get("outputs") or split_callback_id(output)
                g.outputs_list = outputs_list

                g.input_values = input_values = inputs_to_dict(inputs)
                g.state_values = inputs_to_dict(state)
                changed_props = body.get("changedPropIds", [])
                g.triggered_inputs = [
                    {"prop_id": x, "value": input_values.get(x)} for x in changed_props
                ]
                g.dash_response = response # None if pusher request
                g.client = client # None if http request
                
                args = inputs_to_vals(inputs) + inputs_to_vals(state)

                # remember args for shared callbacks
                if Services.shared_test(service):
                    self.callback_map[output]["args"] = {"inputs": deepcopy(inputs), "state": deepcopy(state)}

                if is_coro:
                    if lock is not None:
                        await lock.acquire()
                    try:
                        output_value = await func(*args)  # %% callback invoked
                    finally:
                        if lock is not None:
                            lock.release()
                else:
                    if lock is not None:
                        lock.acquire()
                    try:
                        output_value = func(*args)  # %% callback invoked
                    finally:
                        if lock is not None:
                            lock.release()

                alt = False
                if isinstance(output_value, _NoUpdate):
                    raise PreventUpdate
                # Single alternate result
                elif isinstance(output_value, Output):
                    component_ids = {output_value.component_id: 
                        {output_value.component_property: output_value.component_value}}
                    alt = True
                # List of alternate results
                elif isinstance(output_value, (list, tuple)) and len(output_value)>0 and \
                    isinstance(output_value[0], Output):
                    component_ids = list_to_mods(output_value)
                    alt = True
                # output==none
                elif "id" in outputs_list and outputs_list["id"]=="_none":
                    raise PreventUpdate
                else:
                    # wrap single outputs so we can treat them all the same
                    # for validation and response creation
                    if not multi:
                        output_value, outputs_list = [output_value], [outputs_list]

                    _validate.validate_multi_return(outputs_list, output_value, callback_id)

                    component_ids = collections.defaultdict(dict)
                    has_update = False
                    for val, spec in zip(output_value, outputs_list):
                        if isinstance(val, _NoUpdate):
                            continue
                        for vali, speci in (
                            zip(val, spec) if isinstance(spec, list) else [[val, spec]]
                        ):
                            if not isinstance(vali, _NoUpdate):
                                has_update = True
                                id_str = stringify_id(speci["id"])
                                component_ids[id_str][speci["property"]] = vali

                    if not has_update:
                        raise PreventUpdate

                response = {"response": component_ids, "multi": True}

                try:
                    jsonResponse = json.dumps(
                        response, cls=plotly.utils.PlotlyJSONEncoder
                    )
                except TypeError:
                    _validate.fail_callback_output(output_value, output)

                return jsonResponse, response, alt

            if is_coro:
                lock = Alock() if service&Services.SERIALIZED_CALLBACK else None
            else:
                lock = threading.Lock() if service&Services.SERIALIZED_CALLBACK else None 
            self.callback_map[callback_id]["callback"] = add_context, is_coro, lock
        
            return add_context

        return wrap_func

    async def call_callback(self, body, response, client):
        func, is_coro, lock = self.callback_map[body["output"]]["callback"]

        if is_coro:
            return await func(body, response, lock, client)  # %% callback invoked
        else:
            loop = asyncio.get_event_loop()
            # The callback isn't a coroutine, so we need to run in an executor.
            # Note, there is no easy way to have a wrapper return a coroutine or routine conditionally...
            # So func is always declared a coroutine and runcoro() allows us to run it
            # (without any awaits -- it's only declared a coroutine) inside executor thread.  
            return await loop.run_in_executor(None, runcoro, func(body, response, lock, client))  # %% callback invoked 

    # dispatch() and callback() are fairly complex because we're handling various cases:
    # Shared/unshared, coro/threaded, socket service/http service, alt response/regular response/no response.
    async def dispatch(self, body=None, client=None, request_id=None):
        if body:        
            service = self.callback_map[body["output"]]["service"]
            shared = Services.shared_test(service)
            # Client will only be set on first callback in chain.  We only want to 
            # send changed props for first dispatch in chain.
            # We share input changes with other clients, but not originating client.
            # Note, we could wait and merge input_mods with output_mods and send in 
            # one combined message, but this way, we get the changes sent without the 
            # latency of the callback. 
            if client and shared: 
                input_mods = collections.defaultdict(dict)
                for cpi in body["changedPropIds"]:
                    id_, prop = cpi.split(".")
                    input_mods[id_][prop] = find_prop_value(body["inputs"], id_, prop)
                    if service&Services.SHARE_WITH_OTHER_CLIENTS:
                        #print("send input mods", input_mods, client)
                        await self.share_shared_mods(input_mods, client)

            # Call callback.
            try:
                json_response, response, alt = await self.call_callback(body, None, client)
            except PreventUpdate:
                if request_id is not None:
                    await self.pusher.respond({}, request_id) # send empty response
                return
            # Handle response if this is a request.
            if request_id is not None:
                if shared:
                    # Prevent notifications caused by update/setProps at client
                    await self.pusher.respond({}, request_id)
                else:
                    if alt: 
                        # If not shared and alt result, we need to return empty result and push alt
                        # result so the right callbacks are called.
                        await self.pusher.respond({}, request_id)
                        await self.push_mods_coro(response["response"], client)
                    else:
                        await self.pusher.respond(response, request_id) 

            # Share changed props and outputs with all clients if it's a shared callback.
            if shared:
                output_mods = response["response"]
                outputs = mods_to_list(output_mods)
                if service&Services.SHARE_WITH_OTHER_CLIENTS:
                    #print("send output mods", output_mods)
                    await self.share_shared_mods(output_mods)
                callback_ids, x_list = await self._dispatch_chain(outputs)
                if x_list:
                    raise Exception("{} callback(s) are part of shared callback chain, but are not shared.".format(x_list))   
        else:
            body = await quart.request.get_json()
            response = quart.Response(None, mimetype="application/json")
            json_output, output, alt = await self.call_callback(body, response, None)
            if alt:
                raise Exception("Cannot return alternative results with server_service not set to PUSHER_ALL.")            
            response.set_data(json_output)
            return response

    def callback_connect(self, func):
            self.pusher.callback_connect(func)

    @property
    def clients(self):
        return self.pusher.clients
    

    def _valid_callback_ids(self, service_test):
        valid = []
        for output, callback in self.callback_map.items():
            try:
                if not service_test(callback["service"]):
                    raise Exception
                for i in callback["inputs"] + callback["outputs"]:  
                    if i["id"] not in self.layout_components:
                        raise Exception
                valid.append(output)
            except Exception:
                pass
        return valid


    # Return a list of callbacks (output ids).
    def _callback_compare(self, props, service_test, test, callback_ids):
        callbacks = []
        x_callbacks = []
        if callback_ids is None:
            callback_ids = self._valid_callback_ids(lambda x : True)
        for output in callback_ids:
            callback = self.callback_map[output]
            if test(callback["inputs"]):
                if service_test(callback["service"]):
                    callbacks.append(output)
                else:
                    x_callbacks.append(output)
        return callbacks, x_callbacks

    def _callback_intersect(self, props, service_test, callback_ids=None):
        return self._callback_compare(props, service_test, 
            lambda i : intersect_ids(i, props), callback_ids)

    def _callback_diff(self, props, service_test, callback_ids=None):
        return self._callback_compare(props, service_test, 
            lambda i : not intersect_ids(i, props), callback_ids)

    # This method can only apply to shared callbacks.
    def _callback_body(self, output, inputs):
        # callback body consists of: 
        # {'output': _, 'outputs': [], 'inputs': [], 'changedPropIds': [], 'state': []}
        callback = self.callback_map[output]
        if "args" in callback:
            body = deepcopy(callback["args"])
        else: # construct from layout
            inputs_ = deepcopy(callback["inputs"])
            for i in inputs_:
                comp = self.layout_components[i["id"]]
                i["value"] = getattr(comp, i["property"], None)
            state = deepcopy(callback["state"])    
            for s in state:
                comp = self.layout_components[s["id"]]
                s["value"] = getattr(comp, s["property"], None)
            body = {"inputs": inputs_, "state": state}
        # add inputs
        changedPropIds = []
        for i in inputs:
            for j in body["inputs"]:
                if i["id"]==j["id"] and i["property"]==j["property"] and "value" in i:
                    j["value"] = i["value"]
                    changedPropIds.append(i["id"] + "." + i["property"])
        body["changedPropIds"] = changedPropIds
        body["output"] = output
        body["outputs"] = callback["outputs"][0].copy() if len(callback["outputs"])==1 else deepcopy(callback["outputs"]) 
        if len(body["outputs"])==1:
            body["outputs"] = body["outputs"]
        #print("**** body", body)
        return body

    # This method can only apply to shared callbacks.
    # Call all callbacks, return results in a list [{'id': _, 'property': _, 'value': _}, ...]. 
    async def _dispatch_callbacks(self, bodies):
        tasks = []
        # If there are multiple callbacks, run in parallel.
        for body in bodies:
            if quart.has_websocket_context():
                task = asyncio.create_task(quart.copy_current_websocket_context(self.dispatch)(body))
            else:
                task = asyncio.create_task(self.dispatch(body))
            tasks.append(task)

        await asyncio.gather(*tasks)

    # This method can only apply to shared callbacks.
    async def _dispatch_chain(self, props):
        bodies = []
        callback_ids, x_list = self._callback_intersect(props, Services.shared_test)
        for output in callback_ids:
            bodies.append(self._callback_body(output, props)) 
        await self._dispatch_callbacks(bodies)
        return callback_ids, x_list


    async def _initial_callbacks(self, comps):
        if self.shared_callbacks_called:
            return 
        service_bits = Services.SERVER_INITIAL_CALLBACK + Services.SHARED_CALLBACK
        service_test = lambda service : (service&service_bits)==service_bits
        self.shared_callbacks_called = True
        for callback_id, callback in self.callback_map.items():
            if service_test(callback["service"]) and "args" not in callback:
                self.shared_callbacks_called = False
                break
        if self.shared_callbacks_called:
            return

        # Find all shared callbacks that haven't been called
        callback_ids = []
        valid_ids = self._valid_callback_ids(service_test)
        for output in valid_ids:
            callback = self.callback_map[output]
            if service_test(callback["service"]) and "args" not in callback:
                callback_ids.append(output)

        # If any inputs of our callbacks are incident with comps, these are the callbacks 
        # we will call
        all_callback_ids = set()
        for c in comps:
            for output in callback_ids:
                for i in self.callback_map[output]["inputs"]:
                    if c.id==i["id"]:
                        all_callback_ids.add(output)

        while all_callback_ids:
            # Assemble a list of all outputs from all_callback_ids
            outputs = []
            for output in all_callback_ids:
                outputs.extend(self.callback_map[output]["outputs"])

            # Find all callbacks that don't have any outputs as inputs (and meet service_test).
            # These are the callbacks at the top of any callback chains.  We will call them first/next.
            callback_ids, x_list = self._callback_diff(outputs, service_test, all_callback_ids)
            bodies = [self._callback_body(output, []) for output in callback_ids]
            await self._dispatch_callbacks(bodies)

            # Final check -- if some of the callbacks haven't been called 
            # because of PreventUpdate, etc., continue with remaining callbacks
            discards = [output for output in all_callback_ids if "args" in self.callback_map[output]]
            for discard in discards:
                all_callback_ids.discard(discard)


    def _setup_server(self):
        # Apply _force_eager_loading overrides from modules
        eager_loading = self.config.eager_loading
        for module_name in ComponentRegistry.registry:
            module = sys.modules[module_name]
            eager = getattr(module, "_force_eager_loading", False)
            eager_loading = eager_loading or eager

        # Update eager_loading settings
        self.scripts.config.eager_loading = eager_loading

        if self.config.include_assets_files:
            self._walk_assets_directory()

        _validate.validate_layout(self.layout, self._layout_value())

        self._generate_scripts_html()
        self._generate_css_dist_html()

    def _add_assets_resource(self, url_path, file_path):
        res = {"asset_path": url_path, "filepath": file_path}
        if self.config.assets_external_path:
            res["external_url"] = "{}{}".format(
                self.config.assets_external_path, url_path
            )
        self._assets_files.append(file_path)
        return res

    def _walk_assets_directory(self):
        walk_dir = self.config.assets_folder
        slash_splitter = re.compile(r"[\\/]+")
        ignore_str = self.config.assets_ignore
        ignore_filter = re.compile(ignore_str) if ignore_str else None

        for current, _, files in os.walk(walk_dir):
            if current == walk_dir:
                base = ""
            else:
                s = current.replace(walk_dir, "").lstrip("\\").lstrip("/")
                splitted = slash_splitter.split(s)
                if len(splitted) > 1:
                    base = "/".join(slash_splitter.split(s))
                else:
                    base = splitted[0]

            if ignore_filter:
                files_gen = (x for x in files if not ignore_filter.search(x))
            else:
                files_gen = files

            for f in sorted(files_gen):
                path = "/".join([base, f]) if base else f

                full = os.path.join(current, f)

                if f.endswith("js"):
                    self.scripts.append_script(self._add_assets_resource(path, full))
                elif f.endswith("css"):
                    self.css.append_css(self._add_assets_resource(path, full))
                elif f == "favicon.ico":
                    self._favicon = path

    @staticmethod
    def _invalid_resources_handler(err):
        return err.args[0], 404

    @staticmethod
    async def _serve_default_favicon():
        return quart.Response(
            pkgutil.get_data("dash", "favicon.ico"), content_type="image/x-icon"
        )

    def get_asset_url(self, path):
        asset = get_asset_path(
            self.config.requests_pathname_prefix,
            path,
            self.config.assets_url_path.lstrip("/"),
        )

        return asset

    def get_relative_path(self, path):
        """
        Return a path with `requests_pathname_prefix` prefixed before it.
        Use this function when specifying local URL paths that will work
        in environments regardless of what `requests_pathname_prefix` is.
        In some deployment environments, like Dash Enterprise,
        `requests_pathname_prefix` is set to the application name,
        e.g. `my-dash-app`.
        When working locally, `requests_pathname_prefix` might be unset and
        so a relative URL like `/page-2` can just be `/page-2`.
        However, when the app is deployed to a URL like `/my-dash-app`, then
        `app.get_relative_path('/page-2')` will return `/my-dash-app/page-2`.
        This can be used as an alternative to `get_asset_url` as well with
        `app.get_relative_path('/assets/logo.png')`

        Use this function with `app.strip_relative_path` in callbacks that
        deal with `dcc.Location` `pathname` routing.
        That is, your usage may look like:
        ```
        app.layout = html.Div([
            dcc.Location(id='url'),
            html.Div(id='content')
        ])
        @app.callback(Output('content', 'children'), [Input('url', 'pathname')])
        def display_content(path):
            page_name = app.strip_relative_path(path)
            if not page_name:  # None or ''
                return html.Div([
                    dcc.Link(href=app.get_relative_path('/page-1')),
                    dcc.Link(href=app.get_relative_path('/page-2')),
                ])
            elif page_name == 'page-1':
                return chapters.page_1
            if page_name == "page-2":
                return chapters.page_2
        ```
        """
        asset = get_relative_path(self.config.requests_pathname_prefix, path)

        return asset

    def strip_relative_path(self, path):
        """
        Return a path with `requests_pathname_prefix` and leading and trailing
        slashes stripped from it. Also, if None is passed in, None is returned.
        Use this function with `get_relative_path` in callbacks that deal
        with `dcc.Location` `pathname` routing.
        That is, your usage may look like:
        ```
        app.layout = html.Div([
            dcc.Location(id='url'),
            html.Div(id='content')
        ])
        @app.callback(Output('content', 'children'), [Input('url', 'pathname')])
        def display_content(path):
            page_name = app.strip_relative_path(path)
            if not page_name:  # None or ''
                return html.Div([
                    dcc.Link(href=app.get_relative_path('/page-1')),
                    dcc.Link(href=app.get_relative_path('/page-2')),
                ])
            elif page_name == 'page-1':
                return chapters.page_1
            if page_name == "page-2":
                return chapters.page_2
        ```
        Note that `chapters.page_1` will be served if the user visits `/page-1`
        _or_ `/page-1/` since `strip_relative_path` removes the trailing slash.

        Also note that `strip_relative_path` is compatible with
        `get_relative_path` in environments where `requests_pathname_prefix` set.
        In some deployment environments, like Dash Enterprise,
        `requests_pathname_prefix` is set to the application name, e.g. `my-dash-app`.
        When working locally, `requests_pathname_prefix` might be unset and
        so a relative URL like `/page-2` can just be `/page-2`.
        However, when the app is deployed to a URL like `/my-dash-app`, then
        `app.get_relative_path('/page-2')` will return `/my-dash-app/page-2`

        The `pathname` property of `dcc.Location` will return '`/my-dash-app/page-2`'
        to the callback.
        In this case, `app.strip_relative_path('/my-dash-app/page-2')`
        will return `'page-2'`

        For nested URLs, slashes are still included:
        `app.strip_relative_path('/page-1/sub-page-1/')` will return
        `page-1/sub-page-1`
        ```
        """
        return strip_relative_path(self.config.requests_pathname_prefix, path)

    def _setup_dev_tools(self, **kwargs):
        debug = kwargs.get("debug", False)
        dev_tools = self._dev_tools = AttributeDict()

        for attr in (
            "ui",
            "props_check",
            "serve_dev_bundles",
            "client_reload",
            "hot_reload",
            "silence_routes_logging",
            "prune_errors",
        ):
            dev_tools[attr] = get_combined_config(
                attr, kwargs.get(attr, None), default=debug
            )

        for attr, _type, default in (
            ("hot_reload_interval", float, 3),
            ("hot_reload_watch_interval", float, 0.5),
            ("hot_reload_max_retry", int, 8),
        ):
            dev_tools[attr] = _type(
                get_combined_config(attr, kwargs.get(attr, None), default=default)
            )

        return dev_tools

    def enable_dev_tools(
        self,
        debug=None,
        dev_tools_ui=None,
        dev_tools_props_check=None,
        dev_tools_serve_dev_bundles=None,
        dev_tools_client_reload=None,
        dev_tools_hot_reload=None,
        dev_tools_hot_reload_interval=None,
        dev_tools_hot_reload_watch_interval=None,
        dev_tools_hot_reload_max_retry=None,
        dev_tools_silence_routes_logging=None,
        dev_tools_prune_errors=None,
    ):
        """Activate the dev tools, called by `run_server`. If your application
        is served by wsgi and you want to activate the dev tools, you can call
        this method out of `__main__`.

        All parameters can be set by environment variables as listed.
        Values provided here take precedence over environment variables.

        Available dev_tools environment variables:

            - DASH_DEBUG
            - DASH_UI
            - DASH_PROPS_CHECK
            - DASH_SERVE_DEV_BUNDLES
            - DASH_HOT_RELOAD
            - DASH_HOT_RELOAD_INTERVAL
            - DASH_HOT_RELOAD_WATCH_INTERVAL
            - DASH_HOT_RELOAD_MAX_RETRY
            - DASH_SILENCE_ROUTES_LOGGING
            - DASH_PRUNE_ERRORS

        :param debug: Enable/disable all the dev tools unless overridden by the
            arguments or environment variables. Default is ``True`` when
            ``enable_dev_tools`` is called directly, and ``False`` when called
            via ``run_server``. env: ``DASH_DEBUG``
        :type debug: bool

        :param dev_tools_ui: Show the dev tools UI. env: ``DASH_UI``
        :type dev_tools_ui: bool

        :param dev_tools_props_check: Validate the types and values of Dash
            component props. env: ``DASH_PROPS_CHECK``
        :type dev_tools_props_check: bool

        :param dev_tools_serve_dev_bundles: Serve the dev bundles. Production
            bundles do not necessarily include all the dev tools code.
            env: ``DASH_SERVE_DEV_BUNDLES``
        :type dev_tools_serve_dev_bundles: bool

        :param dev_tools_hot_reload: Activate hot reloading when app, assets,
            and component files change. env: ``DASH_HOT_RELOAD``
        :type dev_tools_hot_reload: bool

        :param dev_tools_hot_reload_interval: Interval in seconds for the
            client to request the reload hash. Default 3.
            env: ``DASH_HOT_RELOAD_INTERVAL``
        :type dev_tools_hot_reload_interval: float

        :param dev_tools_hot_reload_watch_interval: Interval in seconds for the
            server to check asset and component folders for changes.
            Default 0.5. env: ``DASH_HOT_RELOAD_WATCH_INTERVAL``
        :type dev_tools_hot_reload_watch_interval: float

        :param dev_tools_hot_reload_max_retry: Maximum number of failed reload
            hash requests before failing and displaying a pop up. Default 8.
            env: ``DASH_HOT_RELOAD_MAX_RETRY``
        :type dev_tools_hot_reload_max_retry: int

        :param dev_tools_silence_routes_logging: Silence the `werkzeug` logger,
            will remove all routes logging. Enabled with debugging by default
            because hot reload hash checks generate a lot of requests.
            env: ``DASH_SILENCE_ROUTES_LOGGING``
        :type dev_tools_silence_routes_logging: bool

        :param dev_tools_prune_errors: Reduce tracebacks to just user code,
            stripping out Flask and Dash pieces. Only available with debugging.
            `True` by default, set to `False` to see the complete traceback.
            env: ``DASH_PRUNE_ERRORS``
        :type dev_tools_prune_errors: bool

        :return: debug
        """
        if debug is None:
            debug = get_combined_config("debug", None, True)

        dev_tools = self._setup_dev_tools(
            debug=debug,
            ui=dev_tools_ui,
            props_check=dev_tools_props_check,
            serve_dev_bundles=dev_tools_serve_dev_bundles,
            client_reload=dev_tools_client_reload,
            hot_reload=dev_tools_hot_reload,
            hot_reload_interval=dev_tools_hot_reload_interval,
            hot_reload_watch_interval=dev_tools_hot_reload_watch_interval,
            hot_reload_max_retry=dev_tools_hot_reload_max_retry,
            silence_routes_logging=dev_tools_silence_routes_logging,
            prune_errors=dev_tools_prune_errors,
        )

        if dev_tools.silence_routes_logging:
            logging.getLogger("werkzeug").setLevel(logging.ERROR)
            self.logger.setLevel(logging.INFO)

        if dev_tools.client_reload:
            self._hot_reload.hash = generate_hash()

        if dev_tools.hot_reload:
            _reload = self._hot_reload
            _reload.hash = generate_hash()

            component_packages_dist = [
                os.path.dirname(package.path)
                if hasattr(package, "path")
                else package.filename
                for package in (
                    pkgutil.find_loader(x)
                    for x in list(ComponentRegistry.registry) + ["dash_renderer"]
                )
            ]

            _reload.watch_thread = threading.Thread(
                target=lambda: _watch.watch(
                    [self.config.assets_folder] + component_packages_dist,
                    self._on_assets_change,
                    sleep_time=dev_tools.hot_reload_watch_interval,
                )
            )
            _reload.watch_thread.daemon = True
            _reload.watch_thread.start()

        if debug and dev_tools.prune_errors:

            @self.server.errorhandler(Exception)
            def _wrap_errors(_):
                # find the callback invocation, if the error is from a callback
                # and skip the traceback up to that point
                # if the error didn't come from inside a callback, we won't
                # skip anything.
                tb = get_current_traceback()
                skip = 0
                for i, line in enumerate(tb.plaintext.splitlines()):
                    if "%% callback invoked %%" in line:
                        skip = int((i + 1) / 2)
                        break
                return get_current_traceback(skip=skip).render_full(), 500

        if (
            debug
            and dev_tools.serve_dev_bundles
            and not self.scripts.config.serve_locally
        ):
            # Dev bundles only works locally.
            self.scripts.config.serve_locally = True
            print(
                "WARNING: dev bundles requested with serve_locally=False.\n"
                "This is not supported, switching to serve_locally=True"
            )

        return debug

    # noinspection PyProtectedMember
    def _on_assets_change(self, filename, modified, deleted):
        _reload = self._hot_reload
        with _reload.lock:
            _reload.hard = True
            _reload.hash = generate_hash()

            if self.config.assets_folder in filename:
                asset_path = (
                    os.path.relpath(
                        filename,
                        os.path.commonprefix([self.config.assets_folder, filename]),
                    )
                    .replace("\\", "/")
                    .lstrip("/")
                )

                _reload.changed_assets.append(
                    {
                        "url": self.get_asset_url(asset_path),
                        "modified": int(modified),
                        "is_css": filename.endswith("css"),
                    }
                )

                if filename not in self._assets_files and not deleted:
                    res = self._add_assets_resource(asset_path, filename)
                    if filename.endswith("js"):
                        self.scripts.append_script(res)
                    elif filename.endswith("css"):
                        self.css.append_css(res)

                if deleted:
                    if filename in self._assets_files:
                        self._assets_files.remove(filename)

                    def delete_resource(resources):
                        to_delete = None
                        for r in resources:
                            if r.get("asset_path") == asset_path:
                                to_delete = r
                                break
                        if to_delete:
                            resources.remove(to_delete)

                    if filename.endswith("js"):
                        # pylint: disable=protected-access
                        delete_resource(self.scripts._resources._resources)
                    elif filename.endswith("css"):
                        # pylint: disable=protected-access
                        delete_resource(self.css._resources._resources)

    def run_server(
        self,
        host=os.getenv("HOST", "127.0.0.1"),
        port=os.getenv("PORT", "8050"),
        debug=False,
        dev_tools_ui=None,
        dev_tools_props_check=None,
        dev_tools_serve_dev_bundles=None,
        dev_tools_client_reload=None,
        dev_tools_hot_reload=None,
        dev_tools_hot_reload_interval=None,
        dev_tools_hot_reload_watch_interval=None,
        dev_tools_hot_reload_max_retry=None,
        dev_tools_silence_routes_logging=None,
        dev_tools_prune_errors=None,
        **flask_run_options
    ):
        """Start the flask server in local mode, you should not run this on a
        production server, use gunicorn/waitress instead.

        If a parameter can be set by an environment variable, that is listed
        too. Values provided here take precedence over environment variables.

        :param host: Host IP used to serve the application
            env: ``HOST``
        :type host: string

        :param port: Port used to serve the application
            env: ``PORT``
        :type port: int

        :param debug: Set Flask debug mode and enable dev tools.
            env: ``DASH_DEBUG``
        :type debug: bool

        :param debug: Enable/disable all the dev tools unless overridden by the
            arguments or environment variables. Default is ``True`` when
            ``enable_dev_tools`` is called directly, and ``False`` when called
            via ``run_server``. env: ``DASH_DEBUG``
        :type debug: bool

        :param dev_tools_ui: Show the dev tools UI. env: ``DASH_UI``
        :type dev_tools_ui: bool

        :param dev_tools_props_check: Validate the types and values of Dash
            component props. env: ``DASH_PROPS_CHECK``
        :type dev_tools_props_check: bool

        :param dev_tools_serve_dev_bundles: Serve the dev bundles. Production
            bundles do not necessarily include all the dev tools code.
            env: ``DASH_SERVE_DEV_BUNDLES``
        :type dev_tools_serve_dev_bundles: bool

        :param dev_tools_client_reload: Activate auto refresh on client browser when app restarts.
        :type dev_tools_client_reload: bool

        :param dev_tools_hot_reload: Activate hot reloading when app, assets,
            and component files change. env: ``DASH_HOT_RELOAD``
        :type dev_tools_hot_reload: bool

        :param dev_tools_hot_reload_interval: Interval in seconds for the
            client to request the reload hash. Default 3.
            env: ``DASH_HOT_RELOAD_INTERVAL``
        :type dev_tools_hot_reload_interval: float

        :param dev_tools_hot_reload_watch_interval: Interval in seconds for the
            server to check asset and component folders for changes.
            Default 0.5. env: ``DASH_HOT_RELOAD_WATCH_INTERVAL``
        :type dev_tools_hot_reload_watch_interval: float

        :param dev_tools_hot_reload_max_retry: Maximum number of failed reload
            hash requests before failing and displaying a pop up. Default 8.
            env: ``DASH_HOT_RELOAD_MAX_RETRY``
        :type dev_tools_hot_reload_max_retry: int

        :param dev_tools_silence_routes_logging: Silence the `werkzeug` logger,
            will remove all routes logging. Enabled with debugging by default
            because hot reload hash checks generate a lot of requests.
            env: ``DASH_SILENCE_ROUTES_LOGGING``
        :type dev_tools_silence_routes_logging: bool

        :param dev_tools_prune_errors: Reduce tracebacks to just user code,
            stripping out Flask and Dash pieces. Only available with debugging.
            `True` by default, set to `False` to see the complete traceback.
            env: ``DASH_PRUNE_ERRORS``
        :type dev_tools_prune_errors: bool

        :param flask_run_options: Given to `Flask.run`

        :return:
        """
        debug = self.enable_dev_tools(
            debug,
            dev_tools_ui,
            dev_tools_props_check,
            dev_tools_serve_dev_bundles,
            dev_tools_client_reload,
            dev_tools_hot_reload,
            dev_tools_hot_reload_interval,
            dev_tools_hot_reload_watch_interval,
            dev_tools_hot_reload_max_retry,
            dev_tools_silence_routes_logging,
            dev_tools_prune_errors,
        )

        # Verify port value
        try:
            port = int(port)
            assert port in range(1, 65536)
        except Exception as e:
            e.args = [
                "Expecting an integer from 1 to 65535, found port={}".format(repr(port))
            ]
            raise

        if self._dev_tools.silence_routes_logging:
            # Since it's silenced, the address doesn't show anymore.
            ssl_context = flask_run_options.get("ssl_context")
            self.logger.info(
                "Running on %s://%s:%s%s",
                "https" if ssl_context else "http",
                host,
                port,
                self.config.requests_pathname_prefix,
            )

            # Generate a debugger pin and log it to the screen.
            debugger_pin = os.environ["WERKZEUG_DEBUG_PIN"] = "-".join(
                itertools.chain(
                    "".join([str(random.randint(0, 9)) for _ in range(3)])
                    for _ in range(3)
                )
            )

            self.logger.info("Debugger PIN: %s", debugger_pin)

        self.server.run(host=host, port=port, debug=debug, **flask_run_options)

